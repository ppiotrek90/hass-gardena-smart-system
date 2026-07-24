"""Support for Gardena Smart System buttons."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GardenaSmartSystemCoordinator
from .entities import GardenaEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gardena Smart System buttons."""
    coordinator: GardenaSmartSystemCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Create button entities for each mower device
    entities = []

    for location in coordinator.locations.values():
        for device in location.devices.values():
            _LOGGER.debug(
                f"Checking device {device.name} ({device.id}) for button entities "
                f"- Services: {list(device.services.keys())}"
            )

            # Add buttons if device has MOWER service
            if "MOWER" in device.services:
                mower_services = device.services["MOWER"]
                _LOGGER.info(
                    f"Found {len(mower_services)} mower services for device: "
                    f"{device.name} ({device.id})"
                )

                for mower_service in mower_services:
                    _LOGGER.info(
                        f"Creating button entities for mower service: {mower_service.id}"
                    )

                    # START_SECONDS_TO_OVERRIDE
                    entities.append(
                        GardenaStartOverrideButton(
                            coordinator, device, mower_service
                        )
                    )

                    # START_DONT_OVERRIDE
                    entities.append(
                        GardenaResumeScheduleButton(
                            coordinator, device, mower_service
                        )
                    )

                    # PARK_UNTIL_NEXT_TASK
                    entities.append(
                        GardenaReturnToDockButton(
                            coordinator, device, mower_service
                        )
                    )

                    # PARK_UNTIL_FURTHER_NOTICE
                    entities.append(
                        GardenaParkUntilFurtherNoticeButton(
                            coordinator, device, mower_service
                        )
                    )

    _LOGGER.info(f"Created {len(entities)} button entities")
    _LOGGER.info(
        f"Adding button entities to Home Assistant: "
        f"{[entity.name for entity in entities]}"
    )
    async_add_entities(entities)
    _LOGGER.info("Button entities added to Home Assistant")


class GardenaStartOverrideButton(GardenaEntity, ButtonEntity):
    """Representation of a Gardena Start Override button."""

    def __init__(
        self,
        coordinator: GardenaSmartSystemCoordinator,
        device,
        mower_service,
    ) -> None:
        """Initialize the Gardena Start Override button."""
        super().__init__(coordinator, device, "MOWER")
        self._attr_name = f"{device.name} Start Mowing Now"
        self._mower_service = mower_service
        self._attr_unique_id = f"{device.id}_start_override"
        self._attr_icon = "mdi:play"

        _LOGGER.info(
            f"Initialized start override button: {self._attr_name} "
            f"with unique_id: {self._attr_unique_id}"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        attrs = super().extra_state_attributes

        if self._mower_service:
            attrs.update({
                "device_id": self.device.id,
                "service_id": self._mower_service.id,
                "button_type": "start_override",
                "description": "Start mowing immediately for 5 hours",
            })

        return attrs

    async def async_press(self) -> None:
        """Start mowing immediately for 5 hours."""
        _LOGGER.info(
            f"=== START OVERRIDE button pressed for {self._attr_name} ==="
        )

        if self._mower_service:
            duration = 18000  # 5 hours in seconds

            command_data = {
                "data": {
                    "id": "start_override_button",
                    "type": "MOWER_CONTROL",
                    "attributes": {
                        "command": "START_SECONDS_TO_OVERRIDE",
                        "seconds": duration,
                    },
                }
            }

            _LOGGER.info(f"Sending start override command: {command_data}")

            try:
                await self.coordinator.client.send_command(
                    self._mower_service.id,
                    command_data,
                )

                self.coordinator.mower_override_end = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=duration)
                )

                await self.coordinator.async_request_refresh()

                _LOGGER.info(
                    f"=== START OVERRIDE button action completed "
                    f"for {self._attr_name} ==="
                )

            except Exception as e:
                _LOGGER.error(
                    f"Error in start override button "
                    f"for {self._attr_name}: {e}"
                )
                raise

        else:
            _LOGGER.error(
                f"No mower service available for {self._attr_name}"
            )


class GardenaResumeScheduleButton(GardenaEntity, ButtonEntity):
    """Representation of a Gardena Resume Schedule button."""

    def __init__(
        self,
        coordinator: GardenaSmartSystemCoordinator,
        device,
        mower_service,
    ) -> None:
        """Initialize the Gardena Resume Schedule button."""
        super().__init__(coordinator, device, "MOWER")
        self._attr_name = f"{device.name} Resume Schedule"
        self._mower_service = mower_service
        self._attr_unique_id = f"{device.id}_resume_schedule"
        self._attr_icon = "mdi:calendar-start"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        attrs = super().extra_state_attributes

        if self._mower_service:
            attrs.update({
                "device_id": self.device.id,
                "service_id": self._mower_service.id,
                "button_type": "resume_schedule",
                "description": "Resume automatic mowing schedule",
            })

        return attrs

    async def async_press(self) -> None:
        """Resume automatic mowing schedule."""
        _LOGGER.info(
            f"=== RESUME SCHEDULE button pressed for {self._attr_name} ==="
        )

        if self._mower_service:
            command_data = {
                "data": {
                    "id": "resume_schedule_button",
                    "type": "MOWER_CONTROL",
                    "attributes": {
                        "command": "START_DONT_OVERRIDE",
                    },
                }
            }

            _LOGGER.info(f"Sending resume schedule command: {command_data}")

            try:
                await self.coordinator.client.send_command(
                    self._mower_service.id,
                    command_data,
                )

                # Manual override no longer applies
                self.coordinator.mower_override_end = None

                await self.coordinator.async_request_refresh()

                _LOGGER.info(
                    f"=== RESUME SCHEDULE button action completed "
                    f"for {self._attr_name} ==="
                )

            except Exception as e:
                _LOGGER.error(
                    f"Error in resume schedule button "
                    f"for {self._attr_name}: {e}"
                )
                raise

        else:
            _LOGGER.error(
                f"No mower service available for {self._attr_name}"
            )


class GardenaReturnToDockButton(GardenaEntity, ButtonEntity):
    """Representation of a Gardena Return to Dock button."""

    def __init__(
        self,
        coordinator: GardenaSmartSystemCoordinator,
        device,
        mower_service,
    ) -> None:
        """Initialize the Gardena Return to Dock button."""
        super().__init__(coordinator, device, "MOWER")
        self._attr_name = f"{device.name} Return to Dock"
        self._mower_service = mower_service
        self._attr_unique_id = f"{device.id}_return_to_dock"
        self._attr_icon = "mdi:home"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        attrs = super().extra_state_attributes

        if self._mower_service:
            attrs.update({
                "device_id": self.device.id,
                "service_id": self._mower_service.id,
                "button_type": "return_to_dock",
                "description": "Return to dock until next scheduled task",
            })

        return attrs

    async def async_press(self) -> None:
        """Park mower until next scheduled task."""
        _LOGGER.info(
            f"=== RETURN TO DOCK button pressed for {self._attr_name} ==="
        )

        if self._mower_service:
            command_data = {
                "data": {
                    "id": "return_to_dock_button",
                    "type": "MOWER_CONTROL",
                    "attributes": {
                        "command": "PARK_UNTIL_NEXT_TASK",
                    },
                }
            }

            _LOGGER.info(f"Sending return to dock command: {command_data}")

            try:
                await self.coordinator.client.send_command(
                    self._mower_service.id,
                    command_data,
                )

                self.coordinator.mower_override_end = None

                await self.coordinator.async_request_refresh()

                _LOGGER.info(
                    f"=== RETURN TO DOCK button action completed "
                    f"for {self._attr_name} ==="
                )

            except Exception as e:
                _LOGGER.error(
                    f"Error in return to dock button "
                    f"for {self._attr_name}: {e}"
                )
                raise

        else:
            _LOGGER.error(
                f"No mower service available for {self._attr_name}"
            )


class GardenaParkUntilFurtherNoticeButton(GardenaEntity, ButtonEntity):
    """Representation of a Gardena Park Until Further Notice button."""

    def __init__(
        self,
        coordinator: GardenaSmartSystemCoordinator,
        device,
        mower_service,
    ) -> None:
        """Initialize the Gardena Park Until Further Notice button."""
        super().__init__(coordinator, device, "MOWER")
        self._attr_name = f"{device.name} Park Until Further Notice"
        self._mower_service = mower_service
        self._attr_unique_id = f"{device.id}_park_until_further_notice"
        self._attr_icon = "mdi:pause-circle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        attrs = super().extra_state_attributes

        if self._mower_service:
            attrs.update({
                "device_id": self.device.id,
                "service_id": self._mower_service.id,
                "button_type": "park_until_further_notice",
                "description": "Park mower and ignore schedule until resumed",
            })

        return attrs

    async def async_press(self) -> None:
        """Park mower until further notice."""
        _LOGGER.info(
            f"=== PARK UNTIL FURTHER NOTICE button pressed "
            f"for {self._attr_name} ==="
        )

        if self._mower_service:
            command_data = {
                "data": {
                    "id": "park_until_further_notice_button",
                    "type": "MOWER_CONTROL",
                    "attributes": {
                        "command": "PARK_UNTIL_FURTHER_NOTICE",
                    },
                }
            }

            _LOGGER.info(
                f"Sending park until further notice command: {command_data}"
            )

            try:
                await self.coordinator.client.send_command(
                    self._mower_service.id,
                    command_data,
                )

                self.coordinator.mower_override_end = None

                await self.coordinator.async_request_refresh()

                _LOGGER.info(
                    f"=== PARK UNTIL FURTHER NOTICE button action completed "
                    f"for {self._attr_name} ==="
                )

            except Exception as e:
                _LOGGER.error(
                    f"Error in park until further notice button "
                    f"for {self._attr_name}: {e}"
                )
                raise

        else:
            _LOGGER.error(
                f"No mower service available for {self._attr_name}"
            )