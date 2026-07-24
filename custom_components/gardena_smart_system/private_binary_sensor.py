"""Private Gardena API binary sensors."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity import EntityCategory

from .coordinator import GardenaSmartSystemCoordinator
from .entities import GardenaEntity
from .models import GardenaDevice
from .private_helpers import get_property

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrivateBinarySensorDescription:
    """Description of a private API binary sensor."""

    key: str
    name: str
    value_fn: Callable[[GardenaDevice], bool | None]

    icon: str | None = None
    device_class: BinarySensorDeviceClass | None = None
    entity_category: EntityCategory | None = None


def as_bool(value: object) -> bool | None:
    """Convert a Private API value to boolean."""
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return bool(value)

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in {
            "true",
            "1",
            "yes",
            "on",
            "enabled",
        }:
            return True

        if normalized in {
            "false",
            "0",
            "no",
            "off",
            "disabled",
        }:
            return False

    return None


PRIVATE_BINARY_SENSOR_DESCRIPTIONS: tuple[
    PrivateBinarySensorDescription, ...
] = (
    # ------------------------------------------------------------------
    # Battery
    # ------------------------------------------------------------------
    PrivateBinarySensorDescription(
        key="battery_charging",
        name="Battery Charging",
        icon="mdi:battery-charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: as_bool(
            get_property(
                device,
                "battery",
                "charging",
            )
        ),
    ),

    # ------------------------------------------------------------------
    # Charging station
    # ------------------------------------------------------------------
    PrivateBinarySensorDescription(
        key="in_charging_station",
        name="In Charging Station",
        icon="mdi:ev-station",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: as_bool(
            get_property(
                device,
                "charging_station",
                "mower_in_charging_station",
            )
        ),
    ),

    # ------------------------------------------------------------------
    # Mowing according to rain
    # ------------------------------------------------------------------
    PrivateBinarySensorDescription(
        key="mowing_according_to_rain_enabled",
        name="Mowing According to Rain",
        icon="mdi:weather-rainy",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: as_bool(
            get_property(
                device,
                "mowing_according_to_rain",
                "is_mowing_according_to_rain_enabled",
            )
        ),
    ),
    PrivateBinarySensorDescription(
        key="waiting_for_permission_to_mow",
        name="Waiting for Permission to Mow",
        icon="mdi:timer-sand",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: as_bool(
            get_property(
                device,
                "mowing_according_to_rain",
                "is_waiting_for_permission_to_mow",
            )
        ),
    ),
)


class GardenaPrivateBinarySensor(
    GardenaEntity,
    BinarySensorEntity,
):
    """Representation of a private API binary sensor."""

    def __init__(
        self,
        coordinator: GardenaSmartSystemCoordinator,
        device: GardenaDevice,
        description: PrivateBinarySensorDescription,
    ) -> None:
        """Initialize binary sensor."""
        super().__init__(
            coordinator,
            device,
            "COMMON",
        )

        self._description = description

        self._attr_unique_id = (
            f"{device.id}_private_api_{description.key}"
        )
        self._attr_name = f"{description.name} (Private)"
        self._attr_has_entity_name = True
        self._attr_suggested_object_id = description.key

        self._attr_device_class = description.device_class
        self._attr_entity_category = description.entity_category

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
    def is_on(self) -> bool | None:
        """Return binary sensor state."""
        try:
            return self._description.value_fn(
                self._current_device
            )

        except Exception:
            _LOGGER.exception(
                "Failed to calculate private binary sensor '%s'",
                self._description.key,
            )
            return None


def create_private_binary_sensors(
    coordinator: GardenaSmartSystemCoordinator,
) -> list[GardenaPrivateBinarySensor]:
    """Create all private API binary sensors."""
    entities: list[GardenaPrivateBinarySensor] = []

    for location in coordinator.locations.values():
        for device in location.devices.values():
            if not getattr(device, "private_data", None):
                continue

            for description in PRIVATE_BINARY_SENSOR_DESCRIPTIONS:
                entities.append(
                    GardenaPrivateBinarySensor(
                        coordinator,
                        device,
                        description,
                    )
                )

    _LOGGER.debug(
        "Created %d private binary sensors",
        len(entities),
    )

    return entities