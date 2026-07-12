"""Constants for the Gardena Smart System Integration."""
from __future__ import annotations

from typing import Final

from homeassistant.components.lawn_mower import LawnMowerActivity

# Domain
DOMAIN: Final = "gardena_smart_system"

# Configuration keys
CONF_CLIENT_ID: Final = "client_id"
CONF_CLIENT_SECRET: Final = "client_secret"

# API constants
API_BASE_URL: Final = "https://api.smart.gardena.dev/v2"
API_TIMEOUT: Final = 30

# Private API
PRIVATE_HOST: Final = "https://smart.gardena.com"

# Device types
DEVICE_TYPE_MOWER: Final = "MOWER"
DEVICE_TYPE_VALVE: Final = "VALVE"
DEVICE_TYPE_POWER_SOCKET: Final = "POWER_SOCKET"
DEVICE_TYPE_SENSOR: Final = "SENSOR"

# Service types
SERVICE_TYPE_COMMON: Final = "COMMON"
SERVICE_TYPE_MOWER: Final = "MOWER"
SERVICE_TYPE_VALVE: Final = "VALVE"
SERVICE_TYPE_POWER_SOCKET: Final = "POWER_SOCKET"
SERVICE_TYPE_SENSOR: Final = "SENSOR"

# Mower states
MOWER_STATE_OK: Final = "OK"
MOWER_STATE_WARNING: Final = "WARNING"
MOWER_STATE_ERROR: Final = "ERROR"
MOWER_STATE_UNAVAILABLE: Final = "UNAVAILABLE"

# Gardena service states that represent an actual error condition.
MOWER_ERROR_STATES: Final = frozenset({MOWER_STATE_ERROR, MOWER_STATE_WARNING})

# Mower activities
MOWER_ACTIVITY_PAUSED: Final = "PAUSED"
MOWER_ACTIVITY_PAUSED_IN_CS: Final = "PAUSED_IN_CS"
MOWER_ACTIVITY_CUTTING: Final = "OK_CUTTING"
MOWER_ACTIVITY_CUTTING_TIMER_OVERRIDDEN: Final = "OK_CUTTING_TIMER_OVERRIDDEN"
MOWER_ACTIVITY_SEARCHING: Final = "OK_SEARCHING"
MOWER_ACTIVITY_LEAVING: Final = "OK_LEAVING"
MOWER_ACTIVITY_CHARGING: Final = "OK_CHARGING"
MOWER_ACTIVITY_PARKED: Final = "PARKED_TIMER"
MOWER_ACTIVITY_PARKED_TIMER: Final = "PARKED_TIMER"
MOWER_ACTIVITY_PARKED_PARK_SELECTED: Final = "PARKED_PARK_SELECTED"
MOWER_ACTIVITY_PARKED_AUTOTIMER: Final = "PARKED_AUTOTIMER"
MOWER_ACTIVITY_PARKED_FROST: Final = "PARKED_FROST"
MOWER_ACTIVITY_PARKED_NO_LIGHT: Final = "PARKED_NO_LIGHT"
MOWER_ACTIVITY_PARKED_MOWING_COMPLETED: Final = "PARKED_MOWING_COMPLETED"
MOWER_ACTIVITY_PARKED_RAIN: Final = "PARKED_RAIN"
MOWER_ACTIVITY_PARKED_DAILY_LIMIT_REACHED: Final = "PARKED_DAILY_LIMIT_REACHED"
MOWER_ACTIVITY_STOPPED_IN_GARDEN: Final = "STOPPED_IN_GARDEN"
MOWER_ACTIVITY_INITIATE_NEXT_ACTION: Final = "INITIATE_NEXT_ACTION"
MOWER_ACTIVITY_SEARCHING_FOR_SATELLITES: Final = "SEARCHING_FOR_SATELLITES"
MOWER_ACTIVITY_NONE: Final = "NONE"

# Mower activity → HA LawnMowerActivity mapping
MOWER_ACTIVITY_MAP: Final = {
    MOWER_ACTIVITY_PAUSED: LawnMowerActivity.PAUSED,
    MOWER_ACTIVITY_PAUSED_IN_CS: LawnMowerActivity.PAUSED,
    MOWER_ACTIVITY_CUTTING: LawnMowerActivity.MOWING,
    MOWER_ACTIVITY_CUTTING_TIMER_OVERRIDDEN: LawnMowerActivity.MOWING,
    MOWER_ACTIVITY_SEARCHING: LawnMowerActivity.RETURNING,
    MOWER_ACTIVITY_LEAVING: LawnMowerActivity.MOWING,
    MOWER_ACTIVITY_CHARGING: LawnMowerActivity.DOCKED,
    MOWER_ACTIVITY_PARKED: LawnMowerActivity.DOCKED,
    MOWER_ACTIVITY_PARKED_TIMER: LawnMowerActivity.DOCKED,
    MOWER_ACTIVITY_PARKED_PARK_SELECTED: LawnMowerActivity.DOCKED,
    MOWER_ACTIVITY_PARKED_AUTOTIMER: LawnMowerActivity.DOCKED,
    MOWER_ACTIVITY_PARKED_FROST: LawnMowerActivity.DOCKED,
    MOWER_ACTIVITY_PARKED_NO_LIGHT: LawnMowerActivity.DOCKED,
    MOWER_ACTIVITY_PARKED_MOWING_COMPLETED: LawnMowerActivity.DOCKED,
    MOWER_ACTIVITY_PARKED_RAIN: LawnMowerActivity.DOCKED,
    MOWER_ACTIVITY_PARKED_DAILY_LIMIT_REACHED: LawnMowerActivity.DOCKED,
    MOWER_ACTIVITY_STOPPED_IN_GARDEN: LawnMowerActivity.PAUSED,
    MOWER_ACTIVITY_INITIATE_NEXT_ACTION: LawnMowerActivity.MOWING,
    MOWER_ACTIVITY_SEARCHING_FOR_SATELLITES: LawnMowerActivity.PAUSED,
}

# Human-readable activity labels for dashboard display
MOWER_ACTIVITY_LABELS: Final[dict[str, str]] = {
    MOWER_ACTIVITY_PAUSED:                    "Paused",
    MOWER_ACTIVITY_PAUSED_IN_CS:              "Paused in charging station",
    MOWER_ACTIVITY_CUTTING:                   "Mowing",
    MOWER_ACTIVITY_CUTTING_TIMER_OVERRIDDEN:  "Mowing (manual override)",
    MOWER_ACTIVITY_SEARCHING:                 "Returning to station",
    MOWER_ACTIVITY_LEAVING:                   "Leaving station",
    MOWER_ACTIVITY_CHARGING:                  "Charging",
    MOWER_ACTIVITY_PARKED_TIMER:              "Parked (schedule)",
    MOWER_ACTIVITY_PARKED_PARK_SELECTED:      "Parked (manual)",
    MOWER_ACTIVITY_PARKED_AUTOTIMER:          "Parked (auto timer)",
    MOWER_ACTIVITY_PARKED_FROST:              "Parked (frost protection)",
    MOWER_ACTIVITY_PARKED_NO_LIGHT:           "Parked (low light)",
    MOWER_ACTIVITY_PARKED_MOWING_COMPLETED:   "Parked (mowing completed)",
    MOWER_ACTIVITY_PARKED_RAIN:               "Parked (rain)",
    MOWER_ACTIVITY_PARKED_DAILY_LIMIT_REACHED:"Parked (daily limit reached)",
    MOWER_ACTIVITY_STOPPED_IN_GARDEN:         "Stopped in garden",
    MOWER_ACTIVITY_INITIATE_NEXT_ACTION:      "Starting next action",
    MOWER_ACTIVITY_SEARCHING_FOR_SATELLITES:  "Searching for satellites",
    MOWER_ACTIVITY_NONE:                      "None",
}

# Mower informational codes — operational states that are NOT errors.
MOWER_INFORMATIONAL_CODES: Final = frozenset({
    "no_message",
    "uninitialised",
    "parked_daily_limit_reached",
    "outside_working_area",
    "off_disabled",
    "off_hatch_open",
    "off_hatch_closed",
    "wait_updating",
    "wait_power_up",
    "wait_stop_pressed",
    "wait_for_safety_pin",
    "guide_calibration_accomplished",
    "connection_changed",
    "connection_not_changed",
})

# WebSocket configuration
WEBSOCKET_RECONNECT_DELAY: Final = 5
WEBSOCKET_MAX_RECONNECT_ATTEMPTS: Final = 10
WEBSOCKET_SLOW_RECONNECT_INTERVAL: Final = 3600
# Application-level keep-alive ping interval (seconds).
# Gardena docs: recommended every 5 minutes, session idle timeout ~10 minutes.
WEBSOCKET_KEEPALIVE_INTERVAL: Final = 300
# Proactive reconnect before the 2-hour session limit (seconds).
# 119 minutes = 7140 s — gives 1 minute margin before server closes with 1001.
WEBSOCKET_SESSION_LIFETIME: Final = 7140

# Valve duration configuration
CONF_VALVE_DURATIONS: Final = "valve_durations"
DEFAULT_VALVE_DURATION_SECONDS: Final = 3600

# Attribute names
ATTR_BATTERY_STATE: Final = "battery_state"
ATTR_RF_LINK_LEVEL: Final = "rf_link_level"
ATTR_RF_LINK_STATE: Final = "rf_link_state"
ATTR_ACTIVITY_LABEL: Final = "activity_label"