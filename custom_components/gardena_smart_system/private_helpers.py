"""Helpers for Gardena private API."""

from __future__ import annotations

from typing import Any


def get_property(
    device: Any,
    ability_name: str,
    property_name: str,
    default: Any = None,
) -> Any:
    """Return a property value from the private API."""

    private = getattr(device, "private_data", None)

    if not private:
        return default

    for ability in private.get("abilities", []):
        if ability.get("name") != ability_name:
            continue

        for prop in ability.get("properties", []):
            if prop.get("name") == property_name:
                return prop.get("value", default)

    return default


def get_property_timestamp(
    device: Any,
    ability_name: str,
    property_name: str,
) -> str | None:
    """Return property timestamp from the private API."""

    private = getattr(device, "private_data", None)

    if not private:
        return None

    for ability in private.get("abilities", []):
        if ability.get("name") != ability_name:
            continue

        for prop in ability.get("properties", []):
            if prop.get("name") == property_name:
                return prop.get("timestamp")

    return None


def get_ability(
    device: Any,
    ability_name: str,
) -> dict | None:
    """Return a complete ability."""

    private = getattr(device, "private_data", None)

    if not private:
        return None

    for ability in private.get("abilities", []):
        if ability.get("name") == ability_name:
            return ability

    return None