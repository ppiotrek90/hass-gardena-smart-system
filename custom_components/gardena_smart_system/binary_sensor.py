"""Support for Gardena Smart System binary sensors."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GardenaSmartSystemCoordinator
from .entities import GardenaOnlineEntity

_LOGGER = logging.getLogger(__name__)

# Virtual device identifier for the integration-level status device
_STATUS_DEVICE_ID = "gardena_integration_status"


def _status_device_info(entry_id: str) -> DeviceInfo:
    """DeviceInfo for the virtual 'Gardena Integration Status' device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{_STATUS_DEVICE_ID}_{entry_id}")},
        name="Gardena Integration Status",
        manufacturer="Husqvarna / Gardena",
        model="Integration",
        entry_type="service",
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gardena Smart System binary sensors."""
    coordinator: GardenaSmartSystemCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []

    # Per-device RF-link online sensors
    for location in coordinator.locations.values():
        for device in location.devices.values():
            entities.append(GardenaOnlineBinarySensor(coordinator, device))

    # Integration-level status sensors (one virtual device)
    entities.append(GardenaWebSocketSensor(coordinator, entry.entry_id))
    entities.append(GardenaPrivateApiSensor(coordinator, entry.entry_id))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Per-device online sensor
# ---------------------------------------------------------------------------

class GardenaOnlineBinarySensor(GardenaOnlineEntity, BinarySensorEntity):
    """RF-link online status for a physical Gardena device."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: GardenaSmartSystemCoordinator, device) -> None:
        super().__init__(coordinator, device)
        self._attr_name = f"{device.name} Online"
        self._attr_unique_id = f"{device.id}_online"


# ---------------------------------------------------------------------------
# Integration-level status sensors — grouped under one virtual device
# ---------------------------------------------------------------------------

class GardenaWebSocketSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor: is the Gardena WebSocket currently connected?"""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_name = "WebSocket"

    def __init__(self, coordinator: GardenaSmartSystemCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"gardena_websocket_connected_{entry_id}"
        self._attr_device_info = _status_device_info(entry_id)

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        ws = self.coordinator.websocket_client
        return ws.is_connected if ws else False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ws = self.coordinator.websocket_client
        if not ws:
            return {}
        return {
            "status": ws.connection_status,
            "reconnect_attempts": ws.reconnect_attempts,
        }


class GardenaPrivateApiSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor: did the last private API refresh succeed?

    ON  = private API was refreshed at least once and data is fresh
          (last refresh < 2x the configured interval)
    OFF = never refreshed, or last refresh is stale
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_name = "Private API"

    def __init__(self, coordinator: GardenaSmartSystemCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        # New unique_id — old orphaned entity with same name can be deleted in HA UI
        self._attr_unique_id = f"gardena_private_api_connected_{entry_id}"
        self._attr_device_info = _status_device_info(entry_id)

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        """Return True if private API is refreshing successfully."""
        last = self.coordinator._last_private_refresh
        if last == 0.0:
            return False
        # Consider stale if no refresh in 2x the configured interval
        max_age = self.coordinator._PRIVATE_REFRESH_MIN_INTERVAL * 2
        return (time.monotonic() - last) < max_age

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self.coordinator._last_private_refresh
        if last == 0.0:
            return {"status": "never_refreshed"}
        age_s = int(time.monotonic() - last)
        h, rem = divmod(age_s, 3600)
        m, s = divmod(rem, 60)
        return {
            "status": "ok" if self.is_on else "stale",
            "last_refresh_age": f"{h:02d}:{m:02d}:{s:02d}",
            "last_refresh_age_seconds": age_s,
        }