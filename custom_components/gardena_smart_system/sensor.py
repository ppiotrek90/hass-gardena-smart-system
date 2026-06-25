"""Support for Gardena Smart System sensors."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, List

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BATTERY_STATE,
    ATTR_RF_LINK_LEVEL,
    ATTR_RF_LINK_STATE,
    DOMAIN,
    MOWER_INFORMATIONAL_CODES,
)
from .coordinator import GardenaSmartSystemCoordinator
from .entities import GardenaEntity
from .private_sensor import create_private_sensors

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gardena Smart System sensors."""
    coordinator: GardenaSmartSystemCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []

    for location in coordinator.locations.values():
        for device in location.devices.values():
            _LOGGER.debug(
                "Checking device %s (%s) — services: %s",
                device.name, device.id, list(device.services.keys()),
            )

            # --- COMMON: battery + RF link ---
            for common_service in device.services.get("COMMON", []):
                has_battery = (
                    common_service.battery_state not in (None, "NO_BATTERY")
                    or common_service.battery_level is not None
                )
                if has_battery:
                    entities.append(GardenaBatterySensor(coordinator, device, common_service))

                if common_service.rf_link_level is not None:
                    entities.append(GardenaRFLinkLevelSensor(coordinator, device, common_service))

            # --- MOWER: error code ---
            for mower_service in device.services.get("MOWER", []):
                entities.append(GardenaMowerErrorSensor(coordinator, device, mower_service))

            # --- VALVE: watering end time ---
            for valve_service in device.services.get("VALVE", []):
                entities.append(GardenaValveRemainingTimeSensor(coordinator, device, valve_service))

            # --- SENSOR: temperature / humidity / light ---
            for sensor_service in device.services.get("SENSOR", []):
                is_soil = (
                    sensor_service.soil_humidity is not None
                    or sensor_service.soil_temperature is not None
                )
                if sensor_service.soil_temperature is not None:
                    entities.append(
                        GardenaTemperatureSensor(
                            coordinator, device, sensor_service,
                            "soil_temperature", is_soil,
                        )
                    )
                if sensor_service.ambient_temperature is not None:
                    entities.append(
                        GardenaTemperatureSensor(
                            coordinator, device, sensor_service,
                            "ambient_temperature", is_soil,
                        )
                    )
                if sensor_service.soil_humidity is not None:
                    entities.append(GardenaHumiditySensor(coordinator, device, sensor_service))
                if sensor_service.light_intensity is not None:
                    entities.append(GardenaLightSensor(coordinator, device, sensor_service))

    # Integration-level diagnostic sensors
    entities.append(GardenaAPIUsageSensor(coordinator, entry.entry_id))
    entities.append(GardenaPrivateAPIUsageSensor(coordinator, entry.entry_id))

    # Private API sensors
    entities.extend(create_private_sensors(coordinator))

    _LOGGER.debug("Created %d sensor entities", len(entities))
    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_service(coordinator, device_id: str, service_type: str, service_id: str):
    """Return a fresh service object from coordinator data, or None."""
    device = coordinator.get_device_by_id(device_id)
    if device:
        for svc in device.services.get(service_type, []):
            if svc.id == service_id:
                return svc
    return None


# ---------------------------------------------------------------------------
# Sensor classes
# ---------------------------------------------------------------------------

class GardenaBatterySensor(GardenaEntity, SensorEntity):
    """Battery level sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_icon = "mdi:battery"

    def __init__(self, coordinator, device, common_service) -> None:
        super().__init__(coordinator, device, "COMMON")
        self._service_id = common_service.id
        self._device_id = device.id
        self._attr_name = f"{device.name} Battery Level"
        self._attr_unique_id = f"{device.id}_{common_service.id}_battery_level"

    @property
    def _svc(self):
        return _get_service(self.coordinator, self._device_id, "COMMON", self._service_id)

    @property
    def native_value(self) -> int | None:
        svc = self._svc
        return svc.battery_level if svc else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        svc = self._svc
        if svc:
            attrs.update({
                ATTR_BATTERY_STATE: svc.battery_state,
                ATTR_RF_LINK_LEVEL: svc.rf_link_level,
                ATTR_RF_LINK_STATE: svc.rf_link_state,
            })
        return attrs


class GardenaRFLinkLevelSensor(GardenaEntity, SensorEntity):
    """RF link quality sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:signal"

    def __init__(self, coordinator, device, common_service) -> None:
        super().__init__(coordinator, device, "COMMON")
        self._service_id = common_service.id
        self._device_id = device.id
        self._attr_name = f"{device.name} RF Link Quality"
        self._attr_unique_id = f"{device.id}_{common_service.id}_rf_link_level"

    @property
    def _svc(self):
        return _get_service(self.coordinator, self._device_id, "COMMON", self._service_id)

    @property
    def native_value(self) -> int | None:
        svc = self._svc
        return svc.rf_link_level if svc else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        svc = self._svc
        if svc:
            attrs[ATTR_RF_LINK_STATE] = svc.rf_link_state
        return attrs


class GardenaMowerErrorSensor(GardenaEntity, SensorEntity):
    """Mower last error code sensor."""

    _attr_translation_key = "mower_error"
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:alert-circle-outline"
    _attr_options = [
        "uninitialised", "no_message", "outside_working_area", "no_loop_signal",
        "no_charging_station_signal", "wrong_loop_signal",
        "loop_sensor_problem_front", "loop_sensor_problem_rear",
        "trapped", "upside_down", "low_battery", "empty_battery", "no_drive",
        "lifted", "stuck_in_charging_station", "charging_station_blocked",
        "collision_sensor_problem_rear", "collision_sensor_problem_front",
        "wheel_motor_blocked_right", "wheel_motor_blocked_left",
        "wheel_drive_problem_right", "wheel_drive_problem_left",
        "cutting_system_blocked", "invalid_sub_device_combination",
        "settings_restored", "charging_system_problem", "tilt_sensor_problem",
        "mower_tilted", "wheel_motor_overloaded_right",
        "wheel_motor_overloaded_left", "charging_current_too_high",
        "electronic_problem", "cutting_height_blocked", "cutting_height_problem",
        "temporary_problem", "guide_1_not_found", "guide_2_not_found",
        "guide_3_not_found", "gps_tracker_module_error", "weak_gps_signal",
        "guide_calibration_failed", "temporary_battery_problem",
        "battery_problem", "alarm_mower_switched_off", "alarm_mower_stopped",
        "alarm_mower_lifted", "alarm_mower_tilted", "com_board_not_available",
        "slipped", "invalid_battery_combination", "safety_function_faulty",
        "invalid_system_conf", "lift_sensor_defect", "mobile_loop_defect",
        "left_loop_sensor", "right_loop_sensor", "wrong_pin", "temporary_lift",
        "cutting_drive", "steep_slope", "stop_button_fail",
        "angle_cutting_means_off", "slave_mcu_lost", "cutting_overload",
        "cutting_height_range", "cutting_height_drift", "cutting_height_limited",
        "cutting_height_drive", "cutting_height_current",
        "cutting_height_direction", "mower_to_cs_com", "ultrasonic_error",
        "high_low_bat_temp_a", "high_low_bat_temp_b",
        "too_low_voltage_bat_a", "too_low_voltage_bat_b",
        "alarm_motion", "alarm_geofence",
        "rr_wheel_blocked", "rl_wheel_blocked",
        "rr_wheel_drive", "rl_wheel_drive",
        "rear_right_wheel_overloaded", "rear_left_wheel_overloaded",
        "angular_sensor_defect", "no_power_in_cs", "switch_cord_sensor_defect",
        "map_not_valid", "no_position", "no_rs_communication",
        "folding_sensor_activated",
        "ultrasonic_sensor_1_defect", "ultrasonic_sensor_2_defect",
        "ultrasonic_sensor_3_defect", "ultrasonic_sensor_4_defect",
        "cutting_drive_motor_1_defect", "cutting_drive_motor_2_defect",
        "cutting_drive_motor_3_defect",
        "collision_sensor_defect", "docking_sensor_defect",
        "folding_cutting_deck_sensor_defect", "loop_sensor_defect",
        "collision_sensor_error", "no_confirmed_position",
        "major_cutting_disk_imbalance", "complex_working_area",
        "invalid_sw_configuration", "radar_error", "work_area_tampered",
        "destination_not_reachable", "wait_stop_pressed", "wait_for_safety_pin",
        "destination_not_reachable_warning", "battery_near_end_of_life",
        "edgemotor_blocked", "no_correction_data", "invalid_correction_data",
        "wait_updating", "wait_power_up", "off_disabled", "off_hatch_open",
        "off_hatch_closed", "parked_daily_limit_reached",
        "vision_system_malfunction", "poor_vision_system_performance",
        "vision_processing_failed",
        "loop_sensor_problem_left", "loop_sensor_problem_right",
        "wrong_pin_code", "lost_wheel_brush", "accessory_power_anomaly",
        "loop_wire_broken", "battery_fet_error", "imbalanced_cutting_disc",
        "cutting_motor_problem", "limited_cutting_height_range",
        "cutting_motor_drive_defect", "memory_circuit_problem",
        "stop_button_problem", "difficult_finding_home",
        "guide_calibration_accomplished", "too_many_batteries",
        "alarm_mower_in_motion", "alarm_outside_geofence",
        "connection_changed", "connection_not_changed",
        "unknown",
    ]

    def __init__(self, coordinator, device, mower_service) -> None:
        super().__init__(coordinator, device, "MOWER")
        self._service_id = mower_service.id
        self._device_id = device.id
        self._attr_name = None
        self._attr_unique_id = f"{device.id}_{mower_service.id}_last_error_code"

    @property
    def _svc(self):
        return _get_service(self.coordinator, self._device_id, "MOWER", self._service_id)

    @property
    def native_value(self) -> str | None:
        svc = self._svc
        if not svc:
            return None
        code = (svc.last_error_code or "").lower()
        if code in MOWER_INFORMATIONAL_CODES:
            return "no_message"
        return code or "no_message"

    @property
    def icon(self) -> str:
        if self.native_value and self.native_value != "no_message":
            return "mdi:alert-circle"
        return "mdi:check-circle-outline"


class GardenaTemperatureSensor(GardenaEntity, SensorEntity):
    """Temperature sensor (soil or ambient)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator, device, sensor_service, temp_attr: str, is_soil: bool) -> None:
        super().__init__(coordinator, device, "SENSOR")
        self._service_id = sensor_service.id
        self._device_id = device.id
        self._temp_attr = temp_attr

        if temp_attr == "soil_temperature":
            suffix = " (Soil Sensor)" if is_soil else ""
            self._attr_name = f"{device.name} Soil Temperature{suffix}"
            self._attr_unique_id = f"{device.id}_{sensor_service.id}_soil_temperature"
        else:
            self._attr_name = f"{device.name} Ambient Temperature"
            self._attr_unique_id = f"{device.id}_{sensor_service.id}_ambient_temperature"

    @property
    def _svc(self):
        return _get_service(self.coordinator, self._device_id, "SENSOR", self._service_id)

    @property
    def native_value(self) -> float | None:
        svc = self._svc
        if not svc:
            return None
        return svc.soil_temperature if self._temp_attr == "soil_temperature" else svc.ambient_temperature


class GardenaHumiditySensor(GardenaEntity, SensorEntity):
    """Soil humidity sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.MOISTURE
    _attr_icon = "mdi:water-percent"

    def __init__(self, coordinator, device, sensor_service) -> None:
        super().__init__(coordinator, device, "SENSOR")
        self._service_id = sensor_service.id
        self._device_id = device.id
        self._attr_name = f"{device.name} Soil Humidity"
        self._attr_unique_id = f"{device.id}_{sensor_service.id}_soil_humidity"

    @property
    def _svc(self):
        return _get_service(self.coordinator, self._device_id, "SENSOR", self._service_id)

    @property
    def native_value(self) -> int | None:
        svc = self._svc
        return svc.soil_humidity if svc else None


class GardenaLightSensor(GardenaEntity, SensorEntity):
    """Light intensity sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "lx"
    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_icon = "mdi:white-balance-sunny"

    def __init__(self, coordinator, device, sensor_service) -> None:
        super().__init__(coordinator, device, "SENSOR")
        self._service_id = sensor_service.id
        self._device_id = device.id
        self._attr_name = f"{device.name} Light Intensity"
        self._attr_unique_id = f"{device.id}_{sensor_service.id}_light_intensity"

    @property
    def _svc(self):
        return _get_service(self.coordinator, self._device_id, "SENSOR", self._service_id)

    @property
    def native_value(self) -> int | None:
        svc = self._svc
        return svc.light_intensity if svc else None


class GardenaValveRemainingTimeSensor(GardenaEntity, SensorEntity):
    """Timestamp sensor showing when the current watering session ends."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-sand"

    def __init__(self, coordinator, device, valve_service) -> None:
        super().__init__(coordinator, device, "VALVE")
        self._service_id = valve_service.id
        self._device_id = device.id
        valve_name = valve_service.name or device.name
        self._attr_name = f"{valve_name} Watering End"
        self._attr_unique_id = f"{device.id}_{valve_service.id}_watering_end"

    @property
    def _svc(self):
        return _get_service(self.coordinator, self._device_id, "VALVE", self._service_id)

    @property
    def native_value(self) -> datetime | None:
        svc = self._svc
        if not svc:
            return None
        if svc.activity in ("MANUAL_WATERING", "SCHEDULED_WATERING"):
            if svc.duration and svc.duration_timestamp:
                try:
                    start = datetime.fromisoformat(
                        svc.duration_timestamp.replace("Z", "+00:00")
                    )
                    return start + timedelta(seconds=svc.duration)
                except (ValueError, TypeError):
                    return None
        return None


# Shared device info for integration-level status sensors
def _status_device_info(entry_id: str) -> DeviceInfo:
    """DeviceInfo for the virtual 'Gardena Integration Status' device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"gardena_integration_status_{entry_id}")},
        name="Gardena Integration Status",
        manufacturer="Husqvarna / Gardena",
        model="Integration",
        entry_type="service",
    )


class GardenaAPIUsageSensor(CoordinatorEntity, SensorEntity):
    """Public REST API request counter (700 req/week quota).

    Counts requests to api.smart.gardena.dev only.
    Persists across HA restarts via hass.helpers.storage.
    """

    _attr_has_entity_name = True
    _attr_name = "API Requests (Week)"
    _attr_icon = "mdi:api"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: GardenaSmartSystemCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"gardena_api_usage_{entry_id}"
        self._attr_device_info = _status_device_info(entry_id)

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        return self.coordinator.client.api_tracker.requests_this_week

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tracker = self.coordinator.client.api_tracker
        return {
            "requests_today": tracker.requests_today,
            "requests_this_week": tracker.requests_this_week,
            "quota_weekly": 700,
            "quota_remaining": max(0, 700 - tracker.requests_this_week),
            "requests_by_endpoint": tracker.requests_by_endpoint(),
            "recent_requests": tracker.recent_requests,
        }


class GardenaPrivateAPIUsageSensor(CoordinatorEntity, SensorEntity):
    """Private API (smart.gardena.com) refresh counter.

    Shows how many times private API was refreshed this session and
    when it was last refreshed. No quota limit — informational only.
    """

    _attr_has_entity_name = True
    _attr_name = "Private API Refreshes"
    _attr_icon = "mdi:database-refresh"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: GardenaSmartSystemCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"gardena_private_api_usage_{entry_id}"
        self._attr_device_info = _status_device_info(entry_id)

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        """Return total private API refresh count this session."""
        return self.coordinator._private_refresh_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self.coordinator._last_private_refresh
        if last == 0.0:
            return {"status": "never_refreshed"}

        age_s = int(time.monotonic() - last)
        h, rem = divmod(age_s, 3600)
        m, s = divmod(rem, 60)
        next_in = self.coordinator._PRIVATE_REFRESH_MIN_INTERVAL - age_s

        return {
            "status": "ok",
            "last_refresh_age": f"{h:02d}:{m:02d}:{s:02d}",
            "last_refresh_age_seconds": age_s,
            "next_refresh_in_seconds": max(0, int(next_in)),
        }