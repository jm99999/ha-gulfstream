"""Sensor entities for the Gulfstream Pool Heat Pump."""

from __future__ import annotations

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
    """Current water temperature (RSV2 register).

    Redundant with the climate entity's current_temperature, but exposing
    it as a separate sensor gives long-term statistics in the HA recorder
    and makes it easy to use in dashboards and automations.
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
        return self.coordinator.data.water_temp if self.coordinator.data else None


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
