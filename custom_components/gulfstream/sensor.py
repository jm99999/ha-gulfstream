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

from .const import CONF_DEVICE_KEY, CONF_DEVICE_NAME, DOMAIN, MANUFACTURER, MODEL
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
            GulfstreamSetpointSensor(coordinator, entry),
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
    """Current water temperature."""

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


class GulfstreamSetpointSensor(_GulfstreamSensor):
    """Current heat setpoint."""

    _attr_name = "Setpoint"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT

    def __init__(self, coordinator: GulfstreamCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_KEY]}_setpoint"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.setpoint if self.coordinator.data else None
