"""Base entity classes for Gardena Smart System."""
from __future__ import annotations

import logging
from abc import ABC
from typing import Any, Optional

from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GardenaSmartSystemCoordinator
from .models import GardenaDevice

_LOGGER = logging.getLogger(__name__)


class GardenaEntity(CoordinatorEntity, ABC):
    """Base class for all Gardena device entities."""

    def __init__(
        self,
        coordinator: GardenaSmartSystemCoordinator,
        device: GardenaDevice,
        service_type: str,
    ) -> None:
        super().__init__(coordinator)
        self.device = device
        self.service_type = service_type
        self._attr_unique_id = f"{device.id}_{service_type}"
        self._attr_name = self._get_entity_name()
        self._attr_device_info = self._build_device_info()

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Entities stay available during brief WebSocket reconnections to avoid
        noisy unavailable/available flicker. They only go unavailable once the
        coordinator itself has failed or the device RF link is explicitly OFFLINE.
        """
        if not self.coordinator.last_update_success:
            return False

        # Allow up to 3 reconnect attempts before marking unavailable
        ws = self.coordinator.websocket_client
        if ws and ws.connection_status == "disconnected" and ws.reconnect_attempts > 3:
            return False

        for location in self.coordinator.locations.values():
            if self.device.id in location.devices:
                device = location.devices[self.device.id]
                common_services = device.services.get("COMMON", [])
                if common_services:
                    rf_state = common_services[0].rf_link_state
                    if rf_state:
                        return rf_state == "ONLINE"
                return True  # device exists but no RF state — assume available

        return False

    # ------------------------------------------------------------------
    # Device info — shared by all entities on the same physical device
    # ------------------------------------------------------------------

    def _get_common_service(self):
        """Return the first COMMON service for this device, or None."""
        return (self.device.services.get("COMMON") or [None])[0]

    def _build_device_info(self) -> DeviceInfo:
        """Build DeviceInfo from COMMON service data.

        model_type comes from the COMMON service attributes, NOT the device
        root object, so we read it directly here. Falls back to the device
        serial/model_type fields populated by the parser if the service is
        absent (should never happen, but avoids 'Unknown Model' in the UI).
        """
        common = self._get_common_service()
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            name=self.device.name,
            manufacturer="Husqvarna / Gardena",
            model=common.model_type if common and common.model_type else self.device.model_type or None,
            serial_number=common.serial if common and common.serial else self.device.serial or None,
        )

    # ------------------------------------------------------------------
    # Naming helpers
    # ------------------------------------------------------------------

    def _get_entity_name(self) -> str:
        device_name = self.device.name or "Unknown Device"
        return f"{device_name} {self._get_service_display_name()}"

    def _get_service_display_name(self) -> str:
        return {
            "COMMON": "Status",
            "MOWER": "Lawn Mower",
            "POWER_SOCKET": "Power Socket",
            "VALVE": "Valve",
            "VALVE_SET": "Valve Set",
            "SENSOR": "Sensor",
        }.get(self.service_type, self.service_type.title())

    # ------------------------------------------------------------------
    # Common state attributes
    # ------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return common attributes available on every Gardena entity."""
        attrs: dict[str, Any] = {}
        common = self._get_common_service()
        if common:
            if common.battery_level is not None:
                attrs["battery_level"] = common.battery_level
            if common.battery_state:
                attrs["battery_state"] = common.battery_state
            if common.rf_link_level is not None:
                attrs["rf_link_level"] = common.rf_link_level
            if common.rf_link_state:
                attrs["rf_link_state"] = common.rf_link_state
        return attrs


# ---------------------------------------------------------------------------
# Specialised base classes kept for backward-compat with existing platforms
# ---------------------------------------------------------------------------

class GardenaOnlineEntity(GardenaEntity):
    """Base for entities that represent the RF-link online status."""

    def __init__(self, coordinator: GardenaSmartSystemCoordinator, device: GardenaDevice) -> None:
        super().__init__(coordinator, device, "COMMON")
        self._device_id = device.id

    def _current_common(self):
        device = self.coordinator.get_device_by_id(self._device_id)
        if device:
            services = device.services.get("COMMON", [])
            return services[0] if services else None
        return None

    @property
    def is_on(self) -> bool:
        svc = self._current_common()
        return svc.rf_link_state == "ONLINE" if svc else False