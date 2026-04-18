"""Constants, enums, and register codes for the Gulfstream pool heat pump API.

These values were reverse-engineered from the Compass WiFi Heat Pump
Navigator APK (com.icmcontrols.gulfstream v1.0.12) and confirmed against
live API responses from captouchwifi.com (ICM Controls' cloud service).

**Warning: the backend register names do not describe their actual purpose
on the Gulfstream pool heater.** The ICM platform is a white-label cloud
service originally designed for HVAC thermostats. Many registers are
repurposed on pool heaters. The constants below use the *actual* purpose
as the Python name; the underlying register code is the value. See
``discovery/confirmed-register-mapping.md`` for the full empirical mapping.
"""

from enum import IntEnum

# ---------------------------------------------------------------------------
# API configuration
# ---------------------------------------------------------------------------

API_URL = "https://www.captouchwifi.com/icm/api/call"

DEFAULT_TIMEOUT = 15  # seconds per HTTP request
DEFAULT_VERIFY_TIMEOUT = 60  # seconds to wait for command verification
DEFAULT_VERIFY_INTERVAL = 5  # seconds between verification polls

# Default fields requested when listing devices.
# MD = mode, CF = fan, RMT = remote sensor
DEVICE_LIST_ADDITIONAL_FIELDS = '["MD","CF","RMT"]'


# ---------------------------------------------------------------------------
# Operating modes
# ---------------------------------------------------------------------------

class Mode(IntEnum):
    """Heat pump operating modes.

    Corresponds to the ``MD`` register.
    """
    OFF = 0
    POOL_HEAT = 1
    POOL_COOL = 2
    POOL_AUTO = 3  # Heat/Cool
    SPA = 4


class DefrostMode(IntEnum):
    """Defrost mode setting. Maps to the ``DFL`` register."""
    REVERSE_CYCLE = 0
    AIR_DEFROST = 1


# ---------------------------------------------------------------------------
# Key register codes
# ---------------------------------------------------------------------------
#
# These are confirmed against the live app display. See
# discovery/confirmed-register-mapping.md for the full mapping and testing
# history.

# --- Primary control ---
REG_MODE = "MD"                # Operating mode (Mode enum)
REG_SETPOINT = "RSV1"          # Heat setpoint (°F)
REG_PANEL_LOCK = "HUNC"        # Panel lock (0 = unlocked, 2 = locked).
                               # Confirmed via app toggle. APK name "HUNC"
                               # is misleading (was thought to be humidity cal).
REG_LKD = "LKD"                # Unknown. APK: "Lock Disable". Did not change
                               # when panel was locked; purpose unclear.
REG_LKO = "LKO"                # Unknown. APK: "Lock Output". Same as LKD.
REG_SCHEDULE = "SCH"           # Schedule mode (0 = off, 1 = on, 2 = vacation)

# --- Temperature readings ---
REG_WATER_TEMP = "RMT"         # Water temperature (°F, calibrated)
REG_COIL_TEMP = "GEN15"        # Coil/evaporator temperature (°F)
REG_SUCTION_TEMP = "LCS"       # Suction line / refrigerant-side temp
REG_SECONDARY_TEMP = "RSV2"    # Secondary/stale temp reading (unclear purpose)

# --- Hardware limits ---
REG_MAX_HEAT = "MXH"           # Maximum heat setpoint (°F, typically 104)
REG_MIN_HEAT = "MNH"           # Minimum heat setpoint (°F, typically 50)

# --- Configuration toggles (all 0 = Disabled, 1 = Enabled) ---
REG_POOL_COOL_ENABLED = "DF1"       # APK name: "defrost param 1" (wrong)
REG_POOL_HEAT_COOL_ENABLED = "DF2"  # APK name: "defrost param 2" (wrong)
REG_REMOTE_TSTAT_ENABLED = "DFG"    # APK name: "defrost mode" (wrong)
REG_REMOTE_HEAT_COOL_ENABLED = "VH" # APK name: "spa timer" (wrong/dual purpose)

# --- Numeric configuration ---
REG_DEADBAND = "DFU"           # Pool Heat/Cool deadband (°F, 1:1). APK: "DFU"
REG_ANTI_SHORT_CYCLE = "CAL"   # Anti-short-cycle delay (minutes, 1:1). APK: "CAL"
REG_WATER_CAL = "DB"           # Water sensor cal. raw = 10 + offset. APK: "DB"
REG_EVAP_CAL = "HTS"           # Evap sensor cal. raw = 10 + offset. APK: "HTS"

# --- Spa mode setpoint (separate memory from pool heat setpoint RSV1) ---
REG_SPA_SETPOINT = "RSV2"      # Spa setpoint (°F). Stored independently from
                               # RSV1; app displays whichever matches current mode.
REG_DEFROST_END = "AXD"        # Defrost end temperature (°F, 1:1). APK: "AXD"
REG_DEFROST_MODE = "DFL"       # DefrostMode enum (0 or 1). APK: "DFL"
REG_SPA_TIMER_HOURS = "DF3"    # Spa timer hours (0-20, 0 = Continuous)
REG_SPA_TIMER_MINUTES = "STOF" # Spa timer minutes (0-59, in 15-min increments per UI)

# --- Status ---
REG_FAULT = "FLT"              # Fault code (0 = no fault)
REG_FLOW_INDICATOR = "CHGF"    # Flow-dependent flag (0 = flow, 8 = no flow)
REG_FLOW_SENTINEL = "GEN10"    # Flow sentinel (0 = pump on, 255 = pump off)

# --- Calibration offset encoding ---
CAL_OFFSET_BASE = 10           # Sensor cal registers: raw value = 10 + offset


def encode_cal(offset: int) -> int:
    """Encode a sensor calibration offset (°F) to raw register value.

    The backend stores sensor calibrations as ``10 + offset``.  The range
    accepted by the app UI is -10 to +10 in 1° increments.

    Args:
        offset: Calibration offset in °F (e.g. -3, 0, +5).

    Returns:
        Raw register value suitable for writing to DB or HTS.
    """
    return CAL_OFFSET_BASE + offset


def decode_cal(raw: int) -> int:
    """Decode a raw calibration register value to °F offset."""
    return raw - CAL_OFFSET_BASE


# ---------------------------------------------------------------------------
# Valid input ranges (from Compass WiFi app UI)
# ---------------------------------------------------------------------------

POOL_SETPOINT_MIN = 50         # °F
POOL_SETPOINT_MAX = 100        # °F (note: less than hardware MXH=104)

SPA_SETPOINT_MIN = 50          # °F
SPA_SETPOINT_MAX = 104         # °F

DEADBAND_MIN = 2               # °F
DEADBAND_MAX = 8               # °F (whole numbers only)

ANTI_SHORT_CYCLE_MIN = 0       # minutes
ANTI_SHORT_CYCLE_MAX = 10      # minutes (whole numbers)

DEFROST_END_MIN = 42           # °F
DEFROST_END_MAX = 50           # °F (whole numbers)

SENSOR_CAL_MIN = -10           # °F offset
SENSOR_CAL_MAX = 10            # °F offset

SPA_TIMER_HOURS_MAX = 20       # 0-20 hours
# Spa Timer minutes accepted in UI: 0, 15, 30, 45 (15-min increments)
# Special values: (h=0, m=0) = Off, (h=0, m=1) = Continuous
SPA_TIMER_VALID_MINUTES = {0, 15, 30, 45}


def validate_pool_setpoint(value: int) -> None:
    """Raise ValueError if not a valid Pool setpoint."""
    if not (POOL_SETPOINT_MIN <= value <= POOL_SETPOINT_MAX):
        raise ValueError(
            f"Pool setpoint {value} out of range "
            f"[{POOL_SETPOINT_MIN}, {POOL_SETPOINT_MAX}]")


def validate_spa_setpoint(value: int) -> None:
    """Raise ValueError if not a valid Spa setpoint."""
    if not (SPA_SETPOINT_MIN <= value <= SPA_SETPOINT_MAX):
        raise ValueError(
            f"Spa setpoint {value} out of range "
            f"[{SPA_SETPOINT_MIN}, {SPA_SETPOINT_MAX}]")
