"""Sensor entities for the Gulfstream Pool Heat Pump."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_KEY,
    CONF_DEVICE_NAME,
    DOMAIN,
    FAULT_CODE_UNKNOWN,
    FAULT_CODES,
    FAULT_DESCRIPTIONS,
    MANUFACTURER,
    MODEL,
    STALE_THRESHOLD_SECONDS,
)
from .coordinator import GulfstreamCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gulfstream sensor entities."""
    coordinator: GulfstreamCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            GulfstreamWaterTempSensor(coordinator, entry),
            GulfstreamFaultSensor(coordinator, entry),
        ]
    )


class _GulfstreamSensor(CoordinatorEntity[GulfstreamCoordinator], SensorEntity):
    """Base class for Gulfstream sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GulfstreamCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        device_key = entry.data[CONF_DEVICE_KEY]
        device_name = entry.data[CONF_DEVICE_NAME]
        self._device_key = device_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_key)},
            name=device_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )


class GulfstreamWaterTempSensor(_GulfstreamSensor):
    """Current water temperature (RMT register).

    Returns None — shown as "unknown" and excluded from history graphs — when:

    - The device is offline (WiFi module hasn't checked in within
      STALE_THRESHOLD_SECONDS). Stale data isn't pool temperature.
    - The pool circulation pump is off (no_flow fault). Without water
      flowing through the heat exchanger the sensor reads the temperature
      of stagnant water in the pipe, not the pool itself.

    When the state is unknown HA's recorder skips the data point entirely,
    so history graphs won't show misleading flat lines or dips.
    """

    _attr_name = "Water Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT

    def __init__(self, coordinator: GulfstreamCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_KEY]}_water_temp"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        if not data:
            return None

        # Stale connection — data is too old to represent the pool temperature.
        if not _is_recent(data.last_online, data.server_time, STALE_THRESHOLD_SECONDS):
            return None

        # No water flow — sensor reads pipe water, not pool water.
        fault_state = FAULT_CODES.get(data.registers.get("FLT", 0), FAULT_CODE_UNKNOWN)
        if fault_state == "no_flow":
            return None

        return data.water_temp


class GulfstreamFaultSensor(_GulfstreamSensor):
    """Heat pump fault status.

    Reports the value of the FLT (fault) register as a descriptive string.
    A non-zero FLT usually means the heat pump has detected a hardware
    condition that prevents normal operation.

    The most common fault for a pool heat pump is **no_flow** (FLT=1),
    which means the pool circulation pump is off and water isn't flowing
    through the heat exchanger.

    The CHGF register (change-filter indicator) is reported as an
    extra attribute separately — it triggered the fault indicator in the
    app during live testing even when FLT=0.

    Fault code mapping (inferred from app strings; adjust if needed):
        0  → ok
        1  → no_flow
        2  → high_pressure
        3  → low_pressure
        4  → water_sensor_fault
        5  → evap_sensor_fault
        other → fault
    """

    _attr_name = "Fault"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(FAULT_DESCRIPTIONS.keys())

    def __init__(self, coordinator: GulfstreamCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_KEY]}_fault"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        flt = self.coordinator.data.registers.get("FLT", 0)
        return FAULT_CODES.get(flt, FAULT_CODE_UNKNOWN)

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        regs = self.coordinator.data.registers
        flt = regs.get("FLT", 0)
        chgf = regs.get("CHGF", 0)
        return {
            "fault_code": flt,
            "fault_description": FAULT_DESCRIPTIONS.get(
                FAULT_CODES.get(flt, FAULT_CODE_UNKNOWN), "Unknown"
            ),
            "filter_indicator": chgf,
        }

    @property
    def icon(self) -> str:
        if not self.coordinator.data:
            return "mdi:alert-circle-outline"
        flt = self.coordinator.data.registers.get("FLT", 0)
        return "mdi:alert-circle" if flt != 0 else "mdi:check-circle"


def _is_recent(last_online: str, server_time: str, threshold: int) -> bool:
    """Return True if last_online is within threshold seconds of server_time."""
    try:
        last = datetime.strptime(last_online, "%Y-%m-%d %H:%M:%S")
        server = datetime.strptime(server_time, "%Y-%m-%d %H:%M:%S")
        return (server - last).total_seconds() < threshold
    except (ValueError, TypeError):
        return False
