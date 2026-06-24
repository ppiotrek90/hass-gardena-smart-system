"""Private Gardena API sensors."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.helpers.entity import EntityCategory

from .coordinator import GardenaSmartSystemCoordinator
from .entities import GardenaEntity
from .private_helpers import get_property

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrivateSensorDescription(SensorEntityDescription):
    """Description of a private API sensor."""

    ability: str = ""
    property: str = ""
    extractor: Callable[[Any], Any] | None = None


PRIVATE_SENSOR_DESCRIPTIONS: tuple[PrivateSensorDescription, ...] = (
    PrivateSensorDescription(
        key="firmware_status",
        name="Firmware Status",
        ability="firmware",
        property="firmware_status",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


class GardenaPrivateSensor(GardenaEntity, SensorEntity):
    """Representation of a private API sensor."""

    entity_description: PrivateSensorDescription

    def __init__(
        self,
        coordinator: GardenaSmartSystemCoordinator,
        device,
        description: PrivateSensorDescription,
    ) -> None:
        super().__init__(coordinator, device, "COMMON")

        self.entity_description = description

        self._attr_unique_id = (
            f"{device.id}_private_{description.key}"
        )

        self._attr_has_entity_name = True

    @property
    def native_value(self):
        """Return sensor value."""

        value = get_property(
            self.device,
            self.entity_description.ability,
            self.entity_description.property,
        )

        if self.entity_description.extractor:
            value = self.entity_description.extractor(value)

        return value


def create_private_sensors(
    coordinator: GardenaSmartSystemCoordinator,
):
    """Create all private API sensors."""

    entities = []

    _LOGGER.debug("Creating private API sensors")

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

    _LOGGER.info(
        "Created %d private sensors",
        len(entities),
    )

    return entities