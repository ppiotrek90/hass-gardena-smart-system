"""Data coordinator for Gardena Smart System."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

from homeassistant.core import EVENT_HOMEASSISTANT_STARTED, HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .gardena_client import GardenaSmartSystemClient
from .models import GardenaLocation
from .websocket_client import GardenaWebSocketClient

_LOGGER = logging.getLogger(__name__)

# Private API is polled at startup and then at most once per this many seconds,
# triggered by WebSocket events. We don't want to poll on every event (mower
# sends many events while cutting) but we do want reasonably fresh stats data.
# 300 s = 5 minutes.
_PRIVATE_REFRESH_MIN_INTERVAL: float = 300.0

# How long to wait after a WebSocket event before hitting the private API.
# Batches rapid bursts of events (e.g. mower status + battery + GPS all at once)
# into a single request.
_PRIVATE_REFRESH_DEBOUNCE: float = 5.0


class GardenaSmartSystemCoordinator(DataUpdateCoordinator[Dict[str, GardenaLocation]]):
    """Gardena Smart System Data Update Coordinator.

    Flow
    ----
    1. ``async_config_entry_first_refresh`` fetches locations + devices from
       the public REST API (2 requests per location) and then the private API
       (1 request per location). This is the *only* REST call we ever make
       for device state — the public API's 700 req/week quota must last.
    2. A WebSocket connection is opened. Every state change (mower activity,
       battery, etc.) arrives as a push message and is applied in-place via
       ``_update_service_attributes``.
    3. When a WebSocket event arrives, ``_request_private_refresh`` schedules
       a *debounced, rate-limited* call to the private API so that the extra
       data (GPS, stats, firmware, etc.) stays reasonably fresh without
       unnecessary polling. The minimum interval between private refreshes is
       ``_PRIVATE_REFRESH_MIN_INTERVAL`` (5 minutes by default).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: GardenaSmartSystemClient,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.locations: Dict[str, GardenaLocation] = {}
        self.websocket_client: Optional[GardenaWebSocketClient] = None
        self._initial_data_loaded = False
        self._shutdown = False

        # Local end time for START_SECONDS_TO_OVERRIDE mowing session.
        self.mower_override_end: Optional[datetime] = None

        # Private API throttling
        self._private_refresh_lock = asyncio.Lock()
        self._pending_private_refresh: Optional[asyncio.Task] = None
        self._last_private_refresh: float = 0.0  # monotonic timestamp
        self._private_refresh_count: int = 0  # session counter
        self._PRIVATE_REFRESH_MIN_INTERVAL: float = _PRIVATE_REFRESH_MIN_INTERVAL
        self._periodic_private_task: Optional[asyncio.Task] = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,  # All updates come via WebSocket
        )

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def async_config_entry_first_refresh(self) -> None:
        """Fetch initial data and start the WebSocket."""
        if not self._initial_data_loaded:
            await super().async_config_entry_first_refresh()
            self._initial_data_loaded = True
            await self._start_websocket()
        else:
            self.async_set_updated_data(self.locations)

    async def _start_websocket(self) -> None:
        """Start (or restart) the WebSocket client."""
        try:
            if not self.websocket_client:
                self.websocket_client = GardenaWebSocketClient(
                    auth_manager=self.client.auth_manager,
                    event_callback=self._handle_websocket_event,
                    hass=self.hass,
                    coordinator=self,
                )

            await self.websocket_client.start()
            _LOGGER.info("WebSocket client started successfully")

            # Start the periodic private API refresh loop only after HA has
            # fully started — starting it during bootstrap causes a setup
            # timeout because the task is still pending when HA checks.
            async def _start_periodic_loop(_event=None) -> None:
                if (
                    self._periodic_private_task is None
                    or self._periodic_private_task.done()
                ):
                    self._periodic_private_task = self.hass.async_create_task(
                        self._periodic_private_refresh_loop(),
                        name="gardena_private_api_loop",
                    )

            if self.hass.is_running:
                # HA already running (e.g. integration reloaded) — start now
                await _start_periodic_loop()
            else:
                # HA still booting — wait for started event
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED,
                    _start_periodic_loop,
                )

            self.async_set_updated_data(self.locations)

        except Exception:
            _LOGGER.exception("Failed to start WebSocket client")

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator cleanly."""
        _LOGGER.debug("Shutting down Gardena Smart System coordinator")
        self._shutdown = True

        # Cancel any pending private refresh so it doesn't run after shutdown.
        if (
            self._pending_private_refresh
            and not self._pending_private_refresh.done()
        ):
            self._pending_private_refresh.cancel()

        if (
            self._periodic_private_task
            and not self._periodic_private_task.done()
        ):
            self._periodic_private_task.cancel()

        if self.websocket_client:
            await self.websocket_client.stop()
            self.websocket_client = None

        if self.client:
            await self.client.close()

    # ------------------------------------------------------------------
    # Initial REST data load
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> Dict[str, GardenaLocation]:
        """Fetch initial data from the public REST API.

        This method is called exactly ONCE at startup by
        ``async_config_entry_first_refresh``. After that, every call just
        returns the cached ``self.locations`` — all live updates arrive via
        the WebSocket and are applied in-place.

        The public API costs one ``GET /locations`` + one
        ``GET /locations/{id}`` per location. With a 700 req/week quota and
        a weekly HA restart that leaves ~696 requests for WebSocket
        reconnections (each costs 1 POST /v2/websocket).
        """
        if self._initial_data_loaded:
            _LOGGER.debug(
                "Refresh requested after initial load — serving cached data "
                "(state is kept current via WebSocket, no REST call made)"
            )
            return self.locations

        _LOGGER.debug(
            "Starting initial data load from Gardena Smart System"
        )

        try:
            # Load persisted API request history so weekly counter
            # survives HA restarts.
            await self.client.api_tracker.async_load(self.hass)

            locations_list = await self.client.get_locations()

            for location in locations_list:
                try:
                    detailed = await self.client.get_location(location.id)
                    self.locations[location.id] = detailed

                    _LOGGER.debug(
                        "Loaded location %s with %d devices",
                        location.id,
                        len(detailed.devices),
                    )

                except Exception:
                    _LOGGER.warning(
                        "Failed to fetch devices for location %s "
                        "— keeping basic info",
                        location.id,
                        exc_info=True,
                    )
                    self.locations[location.id] = location

            # Private API — once at startup, counts as first refresh.
            await self._do_private_refresh()

            _LOGGER.info(
                "Initial data load complete. "
                "All future updates will arrive via WebSocket."
            )

            return self.locations

        except Exception:
            _LOGGER.exception("Error during initial data load")
            raise

    # ------------------------------------------------------------------
    # WebSocket event handling
    # ------------------------------------------------------------------

    async def _handle_websocket_event(
        self,
        event: Dict[str, Any],
    ) -> None:
        """Dispatch incoming WebSocket events to the right handler."""
        try:
            event_type = event.get("type")

            if event_type == "service_update":
                await self._process_service_update(event)

            elif event_type == "device_event":
                await self._process_device_event(event["data"])

            else:
                _LOGGER.debug(
                    "Unknown WebSocket event type: %s",
                    event_type,
                )

        except Exception:
            _LOGGER.exception("Error handling WebSocket event")

    async def _process_service_update(
        self,
        event: Dict[str, Any],
    ) -> None:
        """Apply a service-update event to in-memory device state."""
        service_id = event.get("service_id")
        service_type = event.get("service_type")
        device_id = event.get("device_id")
        event_data = event.get("data", {})

        _LOGGER.debug(
            "Service update: service_id=%s device_id=%s type=%s",
            service_id,
            device_id,
            service_type,
        )

        if not device_id or not service_id:
            _LOGGER.debug(
                "Service update missing device_id or service_id — skipping"
            )
            return

        await self._update_device_from_event(
            device_id,
            service_id,
            service_type,
            event_data,
        )

        # Schedule a private-API refresh (debounced + rate-limited).
        self._request_private_refresh()

        self.async_set_updated_data(self.locations)

    async def _process_device_event(
        self,
        event_data: Dict[str, Any],
    ) -> None:
        """Apply a device-event payload to in-memory device state."""
        device_id = event_data.get("device_id")
        service_id = event_data.get("service_id")
        service_type = event_data.get("service_type")

        if not device_id or not service_id:
            _LOGGER.debug(
                "Device event missing device_id or service_id — skipping"
            )
            return

        await self._update_device_from_event(
            device_id,
            service_id,
            service_type,
            event_data,
        )

        self._request_private_refresh()

        self.async_set_updated_data(self.locations)

    async def _update_device_from_event(
        self,
        device_id: str,
        service_id: str,
        service_type: str,
        event_data: Dict[str, Any],
    ) -> None:
        """Find the matching service object and update its attributes."""
        for location in self.locations.values():
            if device_id not in location.devices:
                continue

            device = location.devices[device_id]

            if service_type not in device.services:
                _LOGGER.debug(
                    "Service type %s not found on device %s",
                    service_type,
                    device_id,
                )
                return

            for service in device.services[service_type]:
                if service.id == service_id:
                    await self._update_service_attributes(
                        service,
                        event_data,
                    )

                    _LOGGER.debug(
                        "Updated %s service %s for device %s",
                        service_type,
                        service_id,
                        device_id,
                    )
                    return

            _LOGGER.debug(
                "Service %s not found in %s services on device %s",
                service_id,
                service_type,
                device_id,
            )
            return

        _LOGGER.debug(
            "Device %s not found in any location",
            device_id,
        )

    async def _update_service_attributes(
        self,
        service: Any,
        event_data: Dict[str, Any],
    ) -> None:
        """Apply WebSocket event payload to a service model object."""

        def _val(data: Dict, key: str) -> Any:
            """Extract a value that may be wrapped as {'value': ...}."""
            raw = data.get(key)

            if isinstance(raw, dict) and "value" in raw:
                return raw["value"]

            return raw

        # --- Service state ---
        if hasattr(service, "state") and "state" in event_data:
            service.state = _val(event_data, "state")

            # Gardena does not always send lastErrorCode=NO_MESSAGE when the
            # mower recovers from an error. In that case the previously cached
            # error would remain visible indefinitely.
            #
            # Use the service state rather than mower activity to determine
            # recovery. According to the Gardena API, state=OK means the
            # service is fully operational. If Gardena explicitly reports OK
            # without including a new lastErrorCode in the same event, clear
            # the previously cached error locally.
            if (
                hasattr(service, "last_error_code")
                and (service.state or "").upper() == "OK"
                and "lastErrorCode" not in event_data
            ):
                service.last_error_code = "NO_MESSAGE"

        # --- Mower ---
        if hasattr(service, "activity") and "activity" in event_data:
            service.activity = _val(event_data, "activity")

        if (
            hasattr(service, "last_error_code")
            and "lastErrorCode" in event_data
        ):
            # If Gardena does provide lastErrorCode, always trust the value
            # received from the API. This also handles an explicit NO_MESSAGE.
            service.last_error_code = _val(
                event_data,
                "lastErrorCode",
            )

        if (
            hasattr(service, "operating_hours")
            and "operatingHours" in event_data
        ):
            service.operating_hours = _val(
                event_data,
                "operatingHours",
            )

        # --- Common ---
        if (
            hasattr(service, "battery_level")
            and "batteryLevel" in event_data
        ):
            service.battery_level = _val(
                event_data,
                "batteryLevel",
            )

        if (
            hasattr(service, "battery_state")
            and "batteryState" in event_data
        ):
            service.battery_state = _val(
                event_data,
                "batteryState",
            )

        if (
            hasattr(service, "rf_link_state")
            and "rfLinkState" in event_data
        ):
            service.rf_link_state = _val(
                event_data,
                "rfLinkState",
            )

        if (
            hasattr(service, "rf_link_level")
            and "rfLinkLevel" in event_data
        ):
            service.rf_link_level = _val(
                event_data,
                "rfLinkLevel",
            )


    # ------------------------------------------------------------------
    # Private API — debounced, rate-limited refresh
    # ------------------------------------------------------------------

    def _request_private_refresh(self) -> None:
        """Schedule a private-API refresh, unless one is already pending.

        This is called on every WebSocket event. To avoid hammering the
        private API we apply two guards:

        1. **Debounce** — a single asyncio task is created and waits
           ``_PRIVATE_REFRESH_DEBOUNCE`` seconds before running. If another
           event arrives during that window the existing task handles it;
           no new task is spawned.

        2. **Rate-limit** — inside the task we check
           ``_last_private_refresh``. If the previous refresh was less than
           ``_PRIVATE_REFRESH_MIN_INTERVAL`` seconds ago we skip the REST call
           entirely and just return. This means at most one private-API call
           per 5 minutes regardless of WebSocket event frequency.
        """
        if (
            self._pending_private_refresh is None
            or self._pending_private_refresh.done()
        ):
            self._pending_private_refresh = self.hass.async_create_task(
                self._debounced_private_refresh()
            )

    async def _debounced_private_refresh(self) -> None:
        """Wait for the debounce window, then refresh if rate-limit allows."""
        try:
            await asyncio.sleep(_PRIVATE_REFRESH_DEBOUNCE)

            now = time.monotonic()
            elapsed = now - self._last_private_refresh

            if elapsed < _PRIVATE_REFRESH_MIN_INTERVAL:
                _LOGGER.debug(
                    "Private API refresh skipped — last refresh was %.0fs ago "
                    "(min interval: %.0fs)",
                    elapsed,
                    _PRIVATE_REFRESH_MIN_INTERVAL,
                )
                return

            await self._do_private_refresh()
            self.async_set_updated_data(self.locations)

        except asyncio.CancelledError:
            pass

        except Exception:
            _LOGGER.exception(
                "Error in debounced private refresh"
            )

        finally:
            self._pending_private_refresh = None

    async def _periodic_private_refresh_loop(self) -> None:
        """Refresh private API periodically.

        This runs regardless of WebSocket events — essential for data that
        changes without triggering a WebSocket event (e.g. next_start schedule
        changed in the Gardena app, firmware updates, rain sensor config).
        """
        try:
            while not self._shutdown:
                await asyncio.sleep(_PRIVATE_REFRESH_MIN_INTERVAL)

                if self._shutdown:
                    break

                _LOGGER.debug(
                    "Periodic private API refresh triggered (every %.0fs)",
                    _PRIVATE_REFRESH_MIN_INTERVAL,
                )

                await self._do_private_refresh()
                self.async_set_updated_data(self.locations)

        except asyncio.CancelledError:
            pass

        except Exception:
            _LOGGER.exception(
                "Error in periodic private refresh loop"
            )

    async def _do_private_refresh(self) -> None:
        """Fetch private API data for all locations."""
        async with self._private_refresh_lock:
            self._last_private_refresh = time.monotonic()
            self._private_refresh_count += 1

            for location in self.locations.values():
                try:
                    private = await self.client.get_private_devices(
                        location.id
                    )

                    updated = 0

                    for private_device in private.get("devices", []):
                        device_id = private_device.get("id")

                        if (
                            device_id
                            and device_id in location.devices
                        ):
                            location.devices[
                                device_id
                            ].private_data = private_device
                            updated += 1

                    _LOGGER.debug(
                        "Private API refreshed for location %s "
                        "— updated %d/%d devices",
                        location.name,
                        updated,
                        len(private.get("devices", [])),
                    )

                except Exception:
                    _LOGGER.exception(
                        "Failed to refresh private API for location %s",
                        location.name,
                    )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def async_refresh_private_data(self) -> None:
        """Force an immediate private API refresh (bypasses rate-limit)."""
        await self._do_private_refresh()
        self.async_set_updated_data(self.locations)

    def get_devices_by_type(
        self,
        device_type: str,
    ) -> list:
        """Return all devices that have at least one service of device_type."""
        return [
            device
            for location in self.locations.values()
            for device in location.devices.values()
            if device_type in device.services
        ]

    def get_device_by_id(
        self,
        device_id: str,
    ) -> Any | None:
        """Return a device by its ID, or None if not found."""
        for location in self.locations.values():
            if device_id in location.devices:
                return location.devices[device_id]

        return None