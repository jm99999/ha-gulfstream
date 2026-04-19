# Copyright © 2026 J.L.Mann. Personal use only. See LICENSE for terms.
"""Constants for the Gulfstream Pool Heat Pump integration."""

DOMAIN = "gulfstream"

CONF_DEVICE_KEY = "device_key"
CONF_DEVICE_NAME = "device_name"

UPDATE_INTERVAL = 30  # seconds between state polls

# A device is considered stale/offline if last_online is more than this many
# seconds behind server_time. The WiFi module normally polls every 3–10 s;
# 60 s is the threshold the bundled API library uses for is_online.
STALE_THRESHOLD_SECONDS = 60

MANUFACTURER = "Gulfstream / ICM Controls"
MODEL = "Pool Heat Pump"

# ---------------------------------------------------------------------------
# Fault register (FLT) code → human-readable description.
# Values are inferred from the app's fault label strings ("No flow",
# "High pressure switch", etc.) and typical embedded-system bitmask conventions.
# The mapping may need adjustment if live testing reveals different values.
# ---------------------------------------------------------------------------
FAULT_CODES: dict[int, str] = {
    0: "ok",
    1: "no_flow",
    2: "high_pressure",
    3: "low_pressure",
    4: "water_sensor_fault",
    5: "evap_sensor_fault",
}
FAULT_CODE_UNKNOWN = "fault"

FAULT_DESCRIPTIONS: dict[str, str] = {
    "ok":                  "No fault",
    "no_flow":             "No flow — check pool pump",
    "high_pressure":       "High pressure switch",
    "low_pressure":        "Low pressure switch",
    "water_sensor_fault":  "Water sensor malfunction",
    "evap_sensor_fault":   "Evap. sensor malfunction",
    "fault":               "Fault detected",
}
