"""WebSocket client for Gardena Smart System real-time events."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, Optional

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from .auth import GardenaAuthenticationManager
from .const import (
    API_BASE_URL,
    WEBSOCKET_PING_INTERVAL,
    WEBSOCKET_SESSION_CHECK_INTERVAL,
    WEBSOCKET_MAX_RECONNECT_ATTEMPTS,
    WEBSOCKET_SESSION_LIFETIME,
    WEBSOCKET_SLOW_RECONNECT_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Constants are defined in const.py:
#   API_BASE_URL                       — base URL for REST + WebSocket
#   WEBSOCKET_PING_INTERVAL            — RFC-6455 ping interval (150 s)
#   WEBSOCKET_SESSION_CHECK_INTERVAL   — session lifetime check interval (60 s)
#   WEBSOCKET_SESSION_LIFETIME         — proactive reconnect threshold (7140 s)


class GardenaWebSocketClient:
    """WebSocket client for Gardena Smart System real-time events.

    Quota awareness
    ---------------
    Every call to ``_get_websocket_url`` costs **one** POST /v2/websocket
    against the 700 req/week hard limit.  To minimise quota burn:

    * Before each reconnect attempt we call ``GET /v2/health`` — a dedicated
      endpoint with no API key requirement and very high limits.  If the API
      is down we skip the costly POST and reschedule instead.
    * Reconnection uses exponential back-off (30 s → … → 900 s) for the first
      ``WEBSOCKET_MAX_RECONNECT_ATTEMPTS`` attempts, then drops to one attempt
      per ``WEBSOCKET_SLOW_RECONNECT_INTERVAL`` (1 h ≈ 168 req/week).
    * A 429 response sets ``_rate_limited_until`` and blocks further URL
      requests until the back-off window expires.

    Keep-alive and session lifetime
    -------------------------------
    Transport-level RFC-6455 ping/pong is handled by the ``websockets``
    library. Gardena recommends a 150-second ping interval.

    Gardena may also send an application-level ``WEBSOCKET_PING`` JSON
    message. When received, we reply immediately with ``WEBSOCKET_PONG``.

    Gardena WebSocket sessions have a two-hour lifetime. The session monitor
    renews the connection proactively at 119 minutes, leaving a one-minute
    margin before the documented limit.
    """

    def __init__(
        self,
        auth_manager: GardenaAuthenticationManager,
        event_callback: Callable[[Dict[str, Any]], None],
        hass=None,
        coordinator=None,
    ) -> None:
        """Initialize the WebSocket client."""
        self.auth_manager = auth_manager
        self.event_callback = event_callback
        self.hass = hass
        self.coordinator = coordinator

        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.websocket_url: Optional[str] = None

        self.is_connected = False
        self.is_connecting = False

        self.reconnect_task: Optional[asyncio.Task] = None
        self.listen_task: Optional[asyncio.Task] = None
        self.session_monitor_task: Optional[asyncio.Task] = None

        self.reconnect_attempts = 0
        self._shutdown = False
        self._rate_limited_until: float = 0.0
        self._proactive_reconnect = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the WebSocket client."""
        if self.is_connected or self.is_connecting:
            _LOGGER.debug("WebSocket client already running")
            return

        _LOGGER.info("Starting Gardena WebSocket client")
        self._shutdown = False
        await self._connect()

    async def stop(self) -> None:
        """Stop the WebSocket client cleanly."""
        _LOGGER.info("Stopping Gardena WebSocket client")
        self._shutdown = True
        await self._cancel_tasks()

        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        self.is_connected = False
        self.is_connecting = False
        self.reconnect_attempts = 0

    async def force_reconnect(self) -> None:
        """Drop the current connection and reconnect immediately."""
        if self._shutdown:
            return

        _LOGGER.info("Forcing WebSocket reconnection")
        self.reconnect_attempts = 0
        self.is_connected = False
        self.is_connecting = False

        await self._cancel_tasks()
        await self._connect()

    @property
    def connection_status(self) -> str:
        """Human-readable connection state."""
        if self._shutdown:
            return "stopped"
        if self.is_connected:
            return "connected"
        if self.is_connecting:
            return "connecting"
        return "disconnected"

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def _connect(self) -> None:
        """Establish a new WebSocket connection."""
        if self.is_connecting:
            return

        self.is_connecting = True

        try:
            await self._get_websocket_url()

            if not self.websocket_url:
                _LOGGER.error("Failed to obtain WebSocket URL")
                self.is_connecting = False
                await self._schedule_reconnect()
                return

            _LOGGER.debug("Connecting to WebSocket")

            ssl_context = None
            if self.auth_manager._dev_mode:
                import ssl
                # ssl.create_default_context() calls load_default_certs() and
                # set_default_verify_paths() which do blocking filesystem I/O.
                # HA's event loop detects this and logs a warning.  Run it in
                # the default executor to keep the loop unblocked.
                loop = asyncio.get_event_loop()
                ssl_context = await loop.run_in_executor(
                    None, ssl.create_default_context
                )
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            # RFC-6455 transport-level keepalive. This is separate from
            # Gardena's optional application-level WEBSOCKET_PING JSON message.
            self.websocket = await websockets.connect(
                self.websocket_url,
                ping_interval=WEBSOCKET_PING_INTERVAL,
                ping_timeout=10,
                ssl=ssl_context,
            )

            self.is_connected = True
            self.is_connecting = False
            self.reconnect_attempts = 0
            self._proactive_reconnect = False

            _LOGGER.info("WebSocket connected successfully")

            # Launch background tasks
            self.listen_task = asyncio.create_task(
                self._listen_for_messages(), name="gardena_ws_listen"
            )
            self.session_monitor_task = asyncio.create_task(
                self._session_monitor_loop(), name="gardena_ws_session_monitor"
            )

            if self.coordinator:
                self.coordinator.async_set_updated_data(self.coordinator.locations)

        except Exception:
            _LOGGER.exception("Failed to connect to WebSocket")
            self.is_connected = False
            self.is_connecting = False
            await self._schedule_reconnect()

    async def _cancel_tasks(self) -> None:
        """Cancel all running background tasks and wait for them to finish."""
        for task_attr in ("reconnect_task", "listen_task", "session_monitor_task"):
            task: Optional[asyncio.Task] = getattr(self, task_attr)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            setattr(self, task_attr, None)

    # ------------------------------------------------------------------
    # Health check — free, no API key, does NOT count toward quota
    # ------------------------------------------------------------------

    async def _check_api_health(self) -> bool:
        """GET /v2/health — dedicated health endpoint with very high limits.

        Per Gardena docs:
            "To make sure the API is up and running, you mustn't use a regular
             endpoint, as you would run into the rate limiting quickly. Instead,
             use the dedicated health check endpoint that doesn't need an API key
             and operates with very high limits."

        Returns True if the API is up (HTTP 200), False otherwise.
        Does NOT count toward the 700 req/week quota.
        """
        try:
            session = await self.auth_manager._get_session()
            async with session.get(
                f"{API_BASE_URL}/health",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                healthy = response.status == 200
                if not healthy:
                    _LOGGER.warning(
                        "Gardena API health check returned HTTP %s — API may be down",
                        response.status,
                    )
                else:
                    _LOGGER.debug("Gardena API health check OK")
                return healthy
        except Exception:
            _LOGGER.debug("Gardena API health check failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # WebSocket URL — ONE POST /v2/websocket = ONE quota request
    # ------------------------------------------------------------------

    async def _get_websocket_url(self) -> None:
        """POST /v2/websocket to obtain a fresh connection URL.

        Each successful call consumes one request from the 700 req/week quota.
        A rate-limit gate prevents calls while a 429 back-off is active.
        """
        now = time.monotonic()
        if now < self._rate_limited_until:
            wait = self._rate_limited_until - now
            _LOGGER.warning(
                "WebSocket URL request skipped — rate-limit back-off active "
                "for another %.0f s",
                wait,
            )
            self.websocket_url = None
            return

        try:
            if not self.auth_manager._is_token_valid():
                await self.auth_manager.authenticate()

            headers = self.auth_manager.get_auth_headers()
            session = await self.auth_manager._get_session()

            location_id: Optional[str] = None
            if self.coordinator and self.coordinator.locations:
                location_id = next(iter(self.coordinator.locations))
                _LOGGER.debug("Using location ID: %s", location_id)
            else:
                _LOGGER.error(
                    "No locations available in coordinator — cannot request "
                    "WebSocket URL. Waiting for initial data load."
                )
                self.websocket_url = None
                return

            async with session.post(
                f"{API_BASE_URL}/websocket",
                headers=headers,
                json={
                    "data": {
                        "type": "WEBSOCKET",
                        "attributes": {"locationId": location_id},
                    }
                },
            ) as response:
                self._track_request("POST", "/v2/websocket", response.status)

                if response.status == 201:
                    data = await response.json()
                    self.websocket_url = data["data"]["attributes"]["url"]
                    _LOGGER.debug("WebSocket URL obtained successfully")

                elif response.status == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = int(retry_after) if retry_after else 300
                    self._rate_limited_until = time.monotonic() + delay
                    _LOGGER.warning(
                        "Rate limited (429) on WebSocket URL request — "
                        "backing off for %d s. "
                        "Check your weekly quota at developer.husqvarnagroup.cloud",
                        delay,
                    )
                    self.websocket_url = None

                else:
                    _LOGGER.error(
                        "Failed to get WebSocket URL: HTTP %s", response.status
                    )
                    self.websocket_url = None

        except Exception:
            _LOGGER.exception("Error requesting WebSocket URL")
            self.websocket_url = None

    def _track_request(
        self, method: str, endpoint: str, status_code: int | None
    ) -> None:
        """Forward request record to the shared quota tracker."""
        if self.coordinator and hasattr(self.coordinator, "client"):
            self.coordinator.client.api_tracker.record(
                method, endpoint, status_code, source="websocket"
            )

    # ------------------------------------------------------------------
    # Keep-alive + proactive session renewal
    # ------------------------------------------------------------------

    async def _session_monitor_loop(self) -> None:
        """Monitor WebSocket session lifetime and reconnect proactively.

        Transport-level WebSocket ping/pong is handled automatically by
        the websockets library via ping_interval and ping_timeout.

        This loop only monitors the Gardena WebSocket session lifetime
        and reconnects proactively before the server-side session limit.
        """
        session_start = time.monotonic()

        try:
            while self.is_connected and not self._shutdown:
                await asyncio.sleep(WEBSOCKET_SESSION_CHECK_INTERVAL)

                if not self.is_connected or not self.websocket or self._shutdown:
                    break

                session_age = time.monotonic() - session_start

                if session_age >= WEBSOCKET_SESSION_LIFETIME:
                    _LOGGER.info(
                        "WebSocket session age %.0f s — closing gracefully "
                        "for proactive reconnect",
                        session_age,
                    )

                    self._proactive_reconnect = True

                    if self.websocket:
                        await self.websocket.close(
                            1000,
                            "proactive reconnect",
                        )

                    break

                _LOGGER.debug(
                    "WebSocket session healthy (age: %.0f s)",
                    session_age,
                )

        except asyncio.CancelledError:
            pass

        except Exception:
            _LOGGER.debug(
                "WebSocket session monitor ended unexpectedly",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Message listening loop
    # ------------------------------------------------------------------

    async def _listen_for_messages(self) -> None:
        """Consume incoming WebSocket messages until the connection closes."""
        _normal_close = False
        try:
            async for message in self.websocket:
                if self._shutdown:
                    break

                try:
                    data = json.loads(message)
                    _LOGGER.debug("Received WebSocket message: %s", data)
                    await self._process_message(data)
                except json.JSONDecodeError:
                    _LOGGER.error(
                        "Failed to parse WebSocket message: %r", message
                    )
                except Exception:
                    _LOGGER.exception("Error processing WebSocket message")

        except ConnectionClosed as exc:
            # Codes 1000 (normal) and 1001 (going away) mean the server
            # closed the session cleanly — this is the expected 2-hour expiry.
            # Reconnect immediately without backoff or health check.
            _normal_close = exc.code in (1000, 1001)
            if _normal_close:
                _LOGGER.info(
                    "WebSocket session expired (code %s) — reconnecting immediately",
                    exc.code,
                )
            else:
                _LOGGER.warning(
                    "WebSocket closed unexpectedly (code %s: %s) — will reconnect",
                    exc.code, exc.reason,
                )
        except WebSocketException:
            _LOGGER.exception("WebSocket protocol error")
        except Exception:
            _LOGGER.exception("Unexpected error in WebSocket listen loop")
        finally:
            self.is_connected = False

            # Cancel session monitor — no point monitoring a closed socket
            if self.session_monitor_task and not self.session_monitor_task.done():
                self.session_monitor_task.cancel()

            if not self._shutdown:
                if self._proactive_reconnect or _normal_close:
                    # Expected reconnect:
                    # - proactive renewal before the 2-hour session limit
                    # - clean server-side close (1000/1001)
                    #
                    # Reconnect immediately without warning/backoff/health check.
                    self.reconnect_attempts = 0
                    self._proactive_reconnect = False

                    _LOGGER.info(
                        "Reconnecting Gardena WebSocket immediately after "
                        "normal session renewal"
                    )

                    await self._connect()
                else:
                    # Unexpected connection loss — use quota-aware backoff.
                    await self._schedule_reconnect()

            if self.coordinator:
                self.coordinator.async_set_updated_data(
                    self.coordinator.locations
                )

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    async def _process_message(self, data: Dict[str, Any]) -> None:
        """Route an incoming message to the appropriate handler."""
        # Application-level ping from server → reply with pong immediately
        inner = data.get("data", {})
        if isinstance(inner, dict) and inner.get("type") == "WEBSOCKET_PING":
            await self._send_pong()
            return

        # Service state update
        msg_type = data.get("type")
        if msg_type in {
            "COMMON", "MOWER",
        }:
            await self._process_service_update(data)
            return

        _LOGGER.debug("Received unhandled WebSocket message type: %s", msg_type)

    async def _process_service_update(self, service_data: Dict[str, Any]) -> None:
        """Extract service info and forward to the coordinator event callback."""
        service_id = service_data.get("id")
        service_type = service_data.get("type")
        attributes = service_data.get("attributes", {})

        if not service_id:
            _LOGGER.debug("Service update missing id — skipping")
            return

        # Diagnostic logging for mower activity freshness.
        # This records exactly what Gardena sent before coordinator processing.
        if service_type == "MOWER":
            activity = attributes.get("activity", {})
            state = attributes.get("state", {})

            _LOGGER.debug(
                "MOWER event from Gardena: "
                "activity=%s activity_timestamp=%s "
                "state=%s state_timestamp=%s",
                activity.get("value"),
                activity.get("timestamp"),
                state.get("value"),
                state.get("timestamp"),
            )

        # Device ID is the UUID before any ':suffix'  (e.g. "uuid:MOWER")
        device_id = service_id.split(":")[0]

        _LOGGER.debug(
            "Service update: id=%s device=%s type=%s",
            service_id,
            device_id,
            service_type,
        )

        if self.event_callback:
            await self.event_callback({
                "type": "service_update",
                "service_id": service_id,
                "service_type": service_type,
                "device_id": device_id,
                "data": attributes,
            })

    async def _send_pong(self) -> None:
        """Reply to a server-initiated WEBSOCKET_PING with WEBSOCKET_PONG."""
        try:
            if self.websocket and self.is_connected:
                pong = {"data": {"type": "WEBSOCKET_PONG", "attributes": {}}}
                await self.websocket.send(json.dumps(pong))
                _LOGGER.debug("Sent WEBSOCKET_PONG")
        except (ConnectionClosed, WebSocketException):
            _LOGGER.debug("Connection closed while sending WEBSOCKET_PONG")
        except Exception:
            _LOGGER.debug("Failed to send WEBSOCKET_PONG", exc_info=True)

    # ------------------------------------------------------------------
    # Reconnection — quota-aware back-off with health check gate
    # ------------------------------------------------------------------

    async def _schedule_reconnect(self) -> None:
        """Schedule the next reconnection attempt.

        Quota math (700 req/week hard limit):
          Fast phase  — exponential back-off: 30s, 60s, 120s, 240s, 480s, 900s
                        up to WEBSOCKET_MAX_RECONNECT_ATTEMPTS times
          Slow phase  — one attempt per WEBSOCKET_SLOW_RECONNECT_INTERVAL (1 h)
                        ≈ 168 req/week, leaves ~532 for HA restarts + REST
          Rate-limit  — minimum delay is the remaining 429 back-off window
        """
        if self._shutdown:
            return

        # Cancel any existing reconnect task
        if self.reconnect_task and not self.reconnect_task.done():
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass

        self.reconnect_attempts += 1

        now = time.monotonic()
        rate_limit_remaining = max(0.0, self._rate_limited_until - now)

        if self.reconnect_attempts > WEBSOCKET_MAX_RECONNECT_ATTEMPTS:
            delay = max(float(WEBSOCKET_SLOW_RECONNECT_INTERVAL), rate_limit_remaining)
            _LOGGER.warning(
                "WebSocket still down after %d attempts — slow retry every %ds "
                "(quota-safe mode)",
                WEBSOCKET_MAX_RECONNECT_ATTEMPTS,
                int(delay),
            )
        else:
            backoff = min(900, 30 * (2 ** (self.reconnect_attempts - 1)))
            delay = max(float(backoff), rate_limit_remaining)
            _LOGGER.warning(
                "WebSocket reconnect attempt %d/%d in %ds",
                self.reconnect_attempts,
                WEBSOCKET_MAX_RECONNECT_ATTEMPTS,
                int(delay),
            )

        self.reconnect_task = asyncio.create_task(
            self._delayed_reconnect(delay), name="gardena_ws_reconnect"
        )

    async def _delayed_reconnect(self, delay: float) -> None:
        """Sleep for *delay* seconds, run a health check, then reconnect.

        The health check (GET /v2/health) is free and has very high limits per
        Gardena docs — it does NOT count toward the 700 req/week quota.  We use
        it to avoid burning a costly POST /v2/websocket when the API is down.
        """
        try:
            _LOGGER.debug("Reconnect scheduled in %.0f s", delay)
            await asyncio.sleep(delay)

            if self._shutdown:
                return

            # Gate: only proceed if the API is actually reachable
            if not await self._check_api_health():
                _LOGGER.warning(
                    "Gardena API health check failed — skipping reconnect "
                    "attempt to preserve quota. Will retry next cycle."
                )
                # Re-schedule: keeps the reconnect loop alive without burning
                # a quota request on a down API
                await self._schedule_reconnect()
                return

            await self._connect()

        except asyncio.CancelledError:
            _LOGGER.debug("Reconnect task cancelled")
        finally:
            self.reconnect_task = None