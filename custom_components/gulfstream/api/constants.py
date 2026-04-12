"""Constants, enums, and register codes for the Gulfstream pool heat pump API.

These values were reverse-engineered from the Compass WiFi Heat Pump
Navigator APK (com.icmcontrols.gulfstream v1.0.12) and confirmed against
live API responses from captouchwifi.com (ICM Controls' cloud service).
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

    Corresponds to the ``MD`` register and the Joomla ``mode`` field.
    """
    OFF = 0
    POOL_HEAT = 1
    POOL_COOL = 2
    POOL_AUTO = 3  # Heat/Cool
    SPA = 4


# ---------------------------------------------------------------------------
# Key register codes
# ---------------------------------------------------------------------------

# These are the two-to-four letter codes used in thermostatGetDetail's
# currentState object.  Only the Gulfstream-relevant subset is listed here;
# see discovery/decompiled/register-map.md for the full backend register set.

# Control registers
REG_MODE = "MD"            # Operating mode (Mode enum)
REG_FAN = "FAN"            # Fan state
REG_SCHEDULE = "SCH"       # Schedule mode (0=off, 1=on, 2=vacation)
REG_LOCK_DISABLE = "LKD"   # Lock/disable panel buttons
REG_LOCK_OUTPUT = "LKO"    # Lock output
REG_SPA_TIMER = "VH"       # Spa timer (0 = continuous)
REG_DEADBAND = "DB"        # Heat/cool deadband (raw value)
REG_ANTI_SHORT_CYCLE = "AXD"  # Anti-short-cycle delay (raw value)

# Sensor / status registers (read-only from device)
REG_SETPOINT = "RSV1"      # Current displayed setpoint
REG_WATER_TEMP = "RMT"     # Current water temperature (displayed in app)
REG_WATER_TEMP_2 = "RSV2"  # Secondary water temp reading (lagging/averaged?)
REG_REMOTE_SENSOR = "RMT"  # Same as water temp on pool heaters
REG_COIL_TEMP = "GEN15"    # Coil temperature (confirmed via app)
REG_LCS = "LCS"            # Unknown sensor (was misidentified as coil temp)
REG_FAULT = "FLT"          # Fault code

# Defrost registers
REG_DEFROST_MODE = "DFG"   # 0 = Air Defrost, 1 = Reverse Cycle
REG_DEFROST_END = "DFL"    # Defrost end temperature (raw value)

# Calibration registers
REG_WATER_CAL = "CAL"      # Water sensor calibration (raw value)
REG_EVAP_CAL = "HUNC"      # Evaporator sensor calibration

# Limits
REG_MAX_HEAT = "MXH"       # Maximum heat setpoint (°F)
REG_MIN_HEAT = "MNH"       # Minimum heat setpoint (°F)
