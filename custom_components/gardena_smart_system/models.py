"""Data models for Gardena Smart System."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class GardenaLocation:
    """Represents a Gardena location."""

    id: str
    name: str
    devices: Dict[str, "GardenaDevice"] = field(default_factory=dict)


@dataclass
class GardenaDevice:
    """Representation of a Gardena device."""

    id: str
    name: str
    model_type: str
    serial: str
    services: Dict[str, List[Any]] = field(default_factory=dict)
    location_id: str = ""

    # None  = private API never fetched for this device
    # {}    = fetched but returned no data
    # {...} = fetched and has data
    private_data: Dict[str, Any] | None = None


@dataclass
class GardenaService:
    """Base class for Gardena services."""

    id: str
    type: str
    device_id: str
    state: Optional[str] = None
    last_error_code: Optional[str] = None


@dataclass
class GardenaCommonService(GardenaService):
    """Common service properties shared across all devices."""

    name: Optional[str] = None
    battery_level: Optional[int] = None
    battery_state: Optional[str] = None
    rf_link_level: Optional[int] = None
    rf_link_state: Optional[str] = None
    model_type: Optional[str] = None
    serial: Optional[str] = None


@dataclass
class GardenaMowerService(GardenaService):
    """Mower service."""

    activity: Optional[str] = None


@dataclass
class GardenaSensorService(GardenaService):
    """Sensor service."""

    ambient_temperature: Optional[float] = None
    light_intensity: Optional[int] = None


class GardenaDataParser:
    """Parser for Gardena API responses."""

    @staticmethod
    def parse_locations_response(data: Dict[str, Any]) -> List[GardenaLocation]:
        """Parse locations response from API."""
        locations = []
        for location_data in data.get("data", []):
            location = GardenaLocation(
                id=location_data["id"],
                name=location_data["attributes"]["name"],
            )
            locations.append(location)
        return locations

    @staticmethod
    def parse_location_response(data: Dict[str, Any]) -> GardenaLocation:
        """Parse location response with devices from API."""
        location_data = data["data"]
        location = GardenaLocation(
            id=location_data["id"],
            name=location_data["attributes"]["name"],
        )

        devices: Dict[str, GardenaDevice] = {}
        services: Dict[str, List[Any]] = {}

        # First pass: create device stubs and collect raw service data
        for item in data.get("included", []):
            if item["type"] == "DEVICE":
                devices[item["id"]] = GardenaDevice(
                    id=item["id"],
                    name="",
                    model_type="",
                    serial="",
                    location_id=location.id,
                )
            elif item["type"] in (
                "MOWER", "SENSOR", "COMMON"
            ):
                services.setdefault(item["type"], []).append(item)

        # Second pass: associate services with devices
        for service_type, service_list in services.items():
            for service_data in service_list:
                relationships = service_data.get("relationships")
                if not relationships:
                    continue
                device_id = (
                    relationships.get("device", {}).get("data", {}).get("id")
                )
                if not device_id or device_id not in devices:
                    continue

                device = devices[device_id]
                device.services.setdefault(service_type, []).append(
                    GardenaDataParser._create_service(service_type, service_data)
                )

                # Populate device-level fields from COMMON service
                if service_type == "COMMON":
                    attrs = service_data.get("attributes", {})
                    device.name = attrs.get("name", {}).get("value", device.name)
                    device.model_type = attrs.get("modelType", {}).get("value", device.model_type)
                    device.serial = attrs.get("serial", {}).get("value", device.serial)

        location.devices = devices
        return location

    @staticmethod
    def _create_service(service_type: str, service_data: Dict[str, Any]) -> Any:
        """Create a typed service object from raw API data."""
        service_id = service_data["id"]
        device_id = (
            service_data.get("relationships", {})
            .get("device", {})
            .get("data", {})
            .get("id", "")
        )
        attrs = service_data.get("attributes", {})

        def _val(key: str) -> Any:
            return attrs.get(key, {}).get("value")

        if service_type == "COMMON":
            return GardenaCommonService(
                id=service_id,
                type="COMMON",
                device_id=device_id,
                name=_val("name"),
                battery_level=_val("batteryLevel"),
                battery_state=_val("batteryState"),
                rf_link_level=_val("rfLinkLevel"),
                rf_link_state=_val("rfLinkState"),
                model_type=_val("modelType"),
                serial=_val("serial"),
            )
        if service_type == "MOWER":
            return GardenaMowerService(
                id=service_id,
                type="MOWER",
                device_id=device_id,
                state=_val("state"),
                activity=_val("activity"),
                last_error_code=_val("lastErrorCode"),
            )
        if service_type == "SENSOR":
            return GardenaSensorService(
                id=service_id,
                type="SENSOR",
                device_id=device_id,
                ambient_temperature=_val("ambientTemperature"),
                light_intensity=_val("lightIntensity"),
            )

        # Fallback for unknown / future service types
        return GardenaService(
            id=service_id,
            type=service_type,
            device_id=device_id,
        )