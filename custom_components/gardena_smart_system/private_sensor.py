"""Private Gardena API sensors."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.helpers.entity import EntityCategory

from .coordinator import GardenaSmartSystemCoordinator
from .entities import GardenaEntity
from .models import GardenaDevice
from .private_helpers import get_property

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrivateSensorDescription:
    """Description of a private API sensor."""

    key: str
    name: str
    value_fn: Callable[[GardenaDevice], Any]

    icon: str | None = None
    device_class: SensorDeviceClass | None = None
    entity_category: EntityCategory | None = None
    native_unit_of_measurement: str | None = None
    state_class: SensorStateClass | None = None


def parse_timestamp(value: str | None) -> datetime | None:
    """Convert Gardena timestamp to datetime."""
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))

        # Gardena returns Unix epoch when there is no valid next start.
        if dt.year <= 1970:
            return None

        return dt

    except (TypeError, ValueError):
        return None


PRIVATE_SENSOR_DESCRIPTIONS: tuple[PrivateSensorDescription, ...] = (
    # ------------------------------------------------------------------
    # Mower
    # ------------------------------------------------------------------
    PrivateSensorDescription(
        key="next_start",
        name="Next Start",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-start",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: parse_timestamp(
            get_property(
                device,
                "mower",
                "timestamp_next_start",
            )
        ),
    ),
    PrivateSensorDescription(
        key="private_status",
        name="Mower Status",
        icon="mdi:robot-mower",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: get_property(
            device,
            "mower",
            "status",
        ),
    ),
    PrivateSensorDescription(
        key="private_error",
        name="Mower Error",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: get_property(
            device,
            "mower",
            "error",
        ),
    ),

    # ------------------------------------------------------------------
    # LONA / mowing
    # ------------------------------------------------------------------
    PrivateSensorDescription(
        key="mowing_progress",
        name="Mowing Progress",
        icon="mdi:progress-clock",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: get_property(
            device,
            "lona",
            "mowing_progress",
        ),
    ),
    PrivateSensorDescription(
        key="mowing_area_id",
        name="Mowing Area",
        icon="mdi:map-marker-radius",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: get_property(
            device,
            "lona",
            "mowing_area_id",
        ),
    ),

    # ------------------------------------------------------------------
    # Firmware
    # ------------------------------------------------------------------
    PrivateSensorDescription(
        key="firmware_status",
        name="Firmware Status",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: get_property(
            device,
            "firmware",
            "firmware_status",
        ),
    ),
    PrivateSensorDescription(
        key="firmware_version",
        name="Firmware Version",
        icon="mdi:information",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: get_property(
            device,
            "device_info",
            "version",
        ),
    ),

    # ------------------------------------------------------------------
    # Battery
    # ------------------------------------------------------------------
    PrivateSensorDescription(
        key="battery_level",
        name="Battery Level",
        icon="mdi:battery-heart",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: get_property(
            device,
            "battery",
            "level",
        ),
    ),

    # ------------------------------------------------------------------
    # Device info
    # ------------------------------------------------------------------
    PrivateSensorDescription(
        key="connection_status",
        name="Connection Status",
        icon="mdi:lan-connect",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: get_property(
            device,
            "device_info",
            "connection_status",
        ),
    ),
)


class GardenaPrivateSensor(GardenaEntity, SensorEntity):
    """Representation of a private API sensor."""

    def __init__(
        self,
        coordinator: GardenaSmartSystemCoordinator,
        device: GardenaDevice,
        description: PrivateSensorDescription,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator, device, "COMMON")

        self._description = description

        self._attr_unique_id = (
            f"{device.id}_private_api_{description.key}"
        )
        self._attr_name = f"{description.name} (Private)"
        self._attr_has_entity_name = True
        self._attr_suggested_object_id = description.key

        self._attr_device_class = description.device_class
        self._attr_entity_category = description.entity_category
        self._attr_native_unit_of_measurement = (
            description.native_unit_of_measurement
        )
        self._attr_state_class = description.state_class

        if description.icon:
            self._attr_icon = description.icon

    @property
    def _current_device(self) -> GardenaDevice:
        """Return the fresh device object from coordinator."""
        return (
            self.coordinator.get_device_by_id(self.device.id)
            or self.device
        )

    @property
    def native_value(self) -> Any:
        """Return sensor state."""
        try:
            return self._description.value_fn(
                self._current_device
            )

        except Exception:
            _LOGGER.exception(
                "Failed to calculate private sensor '%s'",
                self._description.key,
            )
            return None


def create_private_sensors(
    coordinator: GardenaSmartSystemCoordinator,
) -> list[GardenaPrivateSensor]:
    """Create all private API sensors."""
    entities: list[GardenaPrivateSensor] = []

    for location in coordinator.locations.values():
        for device in location.devices.values():
            if not getattr(device, "private_data", None):
                continue

            for description in PRIVATE_SENSOR_DESCRIPTIONS:
                entities.append(
                    GardenaPrivateSensor(
                        coordinator,
                        device,
                        description,
                    )
                )

    _LOGGER.debug(
        "Created %d private sensors",
        len(entities),
    )

    return entities