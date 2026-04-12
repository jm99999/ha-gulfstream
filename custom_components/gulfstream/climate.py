"""Climate entity for the Gulfstream Pool Heat Pump."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.constants import Mode
from .const import (
    CONF_DEVICE_KEY,
    CONF_DEVICE_NAME,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    PRESET_NORMAL,
    PRESET_SPA,
)
from .coordinator import GulfstreamCoordinator

_LOGGER = logging.getLogger(__name__)

# Map device Mode → (HVACMode, preset)
_MODE_TO_HA: dict[int, tuple[HVACMode, str]] = {
    Mode.OFF:        (HVACMode.OFF,       PRESET_NORMAL),
    Mode.POOL_HEAT:  (HVACMode.HEAT,      PRESET_NORMAL),
    Mode.POOL_COOL:  (HVACMode.COOL,      PRESET_NORMAL),
    Mode.POOL_AUTO:  (HVACMode.HEAT_COOL, PRESET_NORMAL),
    Mode.SPA:        (HVACMode.HEAT,      PRESET_SPA),
}

# Map (HVACMode, preset) → device Mode
_HA_TO_MODE: dict[tuple[HVACMode, str], Mode] = {
    (HVACMode.OFF,       PRESET_NORMAL): Mode.OFF,
    (HVACMode.HEAT,      PRESET_NORMAL): Mode.POOL_HEAT,
    (HVACMode.COOL,      PRESET_NORMAL): Mode.POOL_COOL,
    (HVACMode.HEAT_COOL, PRESET_NORMAL): Mode.POOL_AUTO,
    (HVACMode.HEAT,      PRESET_SPA):    Mode.SPA,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gulfstream climate entity."""
    coordinator: GulfstreamCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GulfstreamClimate(coordinator, entry)])


class GulfstreamClimate(CoordinatorEntity[GulfstreamCoordinator], ClimateEntity):
    """Climate entity representing a Gulfstream pool heat pump."""

    _attr_has_entity_name = True
    _attr_name = None  # use device name as entity name
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_target_temperature_step = 1.0
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]
    _attr_preset_modes = [PRESET_NORMAL, PRESET_SPA]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )

    def __init__(
        self, coordinator: GulfstreamCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        device_key = entry.data[CONF_DEVICE_KEY]
        device_name = entry.data[CONF_DEVICE_NAME]
        self._attr_unique_id = f"{device_key}_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_key)},
            name=device_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def current_temperature(self) -> float | None:
        """Current water temperature."""
        return self.coordinator.data.water_temp if self.coordinator.data else None

    @property
    def target_temperature(self) -> float | None:
        """Current setpoint."""
        return self.coordinator.data.setpoint if self.coordinator.data else None

    @property
    def min_temp(self) -> float:
        """Minimum settable temperature."""
        if self.coordinator.data:
            return float(self.coordinator.data.min_heat)
        return 50.0

    @property
    def max_temp(self) -> float:
        """Maximum settable temperature."""
        if self.coordinator.data:
            return float(self.coordinator.data.max_heat)
        return 104.0

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Current HVAC mode."""
        if not self.coordinator.data:
            return None
        api_mode = int(self.coordinator.data.mode)
        hvac_mode, _ = _MODE_TO_HA.get(api_mode, (HVACMode.OFF, PRESET_NORMAL))
        return hvac_mode

    @property
    def preset_mode(self) -> str | None:
        """Current preset mode."""
        if not self.coordinator.data:
            return None
        api_mode = int(self.coordinator.data.mode)
        _, preset = _MODE_TO_HA.get(api_mode, (HVACMode.OFF, PRESET_NORMAL))
        return preset

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set a new HVAC mode (always clears spa preset)."""
        preset = PRESET_NORMAL
        api_mode = _HA_TO_MODE.get((hvac_mode, preset), Mode.OFF)
        await self._async_set_mode(api_mode)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set spa or normal preset."""
        if preset_mode == PRESET_SPA:
            api_mode = Mode.SPA
        else:
            # Return to pool heat when leaving spa
            current_hvac = self.hvac_mode or HVACMode.HEAT
            if current_hvac == HVACMode.OFF:
                current_hvac = HVACMode.HEAT
            api_mode = _HA_TO_MODE.get((current_hvac, PRESET_NORMAL), Mode.POOL_HEAT)
        await self._async_set_mode(api_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        await self.hass.async_add_executor_job(
            self.coordinator.device.set_heat_setpoint, int(temp)
        )
        await self.coordinator.async_request_refresh()

    async def _async_set_mode(self, mode: Mode) -> None:
        await self.hass.async_add_executor_job(
            self.coordinator.device.set_mode, mode
        )
        await self.coordinator.async_request_refresh()
