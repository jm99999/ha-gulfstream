"""Climate entity for the Gulfstream Pool Heat Pump."""

from __future__ import annotations

import logging
from datetime import datetime
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
    FAULT_CODE_UNKNOWN,
    FAULT_CODES,
    MANUFACTURER,
    MODEL,
    PRESET_NORMAL,
    PRESET_SPA,
    STALE_THRESHOLD_SECONDS,
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

# Verification timeout for commands sent to the heat pump.
# The device polls the server every 3–10 s; 30 s gives ~5 chances for delivery.
_VERIFY_TIMEOUT = 30


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gulfstream climate entity."""
    coordinator: GulfstreamCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GulfstreamClimate(coordinator, entry)])


class GulfstreamClimate(CoordinatorEntity[GulfstreamCoordinator], ClimateEntity):
    """Climate entity representing a Gulfstream pool heat pump.

    Modes
    -----
    The device supports five operating modes:

    =====================  ================  ===========
    Device mode            HA HVAC mode      HA preset
    =====================  ================  ===========
    Off                    off               normal
    Pool Heat              heat              normal
    Pool Cool              cool              normal   *
    Pool Heat/Cool (Auto)  heat_cool         normal   *
    Spa                    heat              spa
    =====================  ================  ===========

    (*) Pool Cool and Pool Heat/Cool are only shown when the device reports
    them as enabled (DF1 / DF2 registers). This is read directly from the
    device state on every poll — no manual configuration required.

    Spa mode is exposed as a preset (not a fifth HVAC mode) because HA's
    climate model does not have a native spa concept. Spa mode heats to a
    separate setpoint (RSV2) — functionally identical to Pool Heat but with
    a different temperature target.

    Command verification
    --------------------
    All mode and setpoint changes are sent with verify=True (timeout 30 s).
    The library polls the device until it confirms the register changed, or
    times out. A timeout or conflict is logged as a warning but does not
    raise an error in HA (the next poll will reconcile state).
    """

    _attr_has_entity_name = True
    _attr_name = None  # entity name = device name (HA primary-feature convention)
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_target_temperature_step = 1.0
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
    def hvac_modes(self) -> list[HVACMode]:
        """Available modes — derived from device registers (DF1/DF2)."""
        modes = [HVACMode.OFF, HVACMode.HEAT]
        data = self.coordinator.data
        if data:
            if data.pool_cool_enabled:
                modes.append(HVACMode.COOL)
            if data.pool_heat_cool_enabled:
                modes.append(HVACMode.HEAT_COOL)
        return modes

    @property
    def current_temperature(self) -> float | None:
        """Current water temperature — None when offline or no water flow.

        Mirrors the same suppression logic as GulfstreamWaterTempSensor so
        the thermostat card doesn't display a stale or pipe-water reading.
        """
        data = self.coordinator.data
        if not data:
            return None
        if not _is_recent(data.last_online, data.server_time, STALE_THRESHOLD_SECONDS):
            return None
        fault_state = FAULT_CODES.get(data.registers.get("FLT", 0), FAULT_CODE_UNKNOWN)
        if fault_state == "no_flow":
            return None
        return float(data.water_temp)

    @property
    def target_temperature(self) -> float | None:
        """Active setpoint — RSV1 (pool) or RSV2 (spa) depending on mode."""
        return self.coordinator.data.setpoint if self.coordinator.data else None

    @property
    def min_temp(self) -> float:
        if self.coordinator.data:
            return float(self.coordinator.data.min_heat)
        return 50.0

    @property
    def max_temp(self) -> float:
        if self.coordinator.data:
            return float(self.coordinator.data.max_heat)
        return 104.0

    @property
    def hvac_mode(self) -> HVACMode | None:
        if not self.coordinator.data:
            return None
        api_mode = int(self.coordinator.data.mode)
        hvac_mode, _ = _MODE_TO_HA.get(api_mode, (HVACMode.OFF, PRESET_NORMAL))
        return hvac_mode

    @property
    def preset_mode(self) -> str | None:
        if not self.coordinator.data:
            return None
        api_mode = int(self.coordinator.data.mode)
        _, preset = _MODE_TO_HA.get(api_mode, (HVACMode.OFF, PRESET_NORMAL))
        return preset

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode. Always clears spa preset."""
        api_mode = _HA_TO_MODE.get((hvac_mode, PRESET_NORMAL), Mode.OFF)
        await self._async_set_mode(api_mode)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Switch between normal (pool) and spa presets."""
        if preset_mode == PRESET_SPA:
            api_mode = Mode.SPA
        else:
            # Leaving spa: restore pool heat (or current non-spa hvac mode)
            current_hvac = self.hvac_mode or HVACMode.HEAT
            if current_hvac == HVACMode.OFF:
                current_hvac = HVACMode.HEAT
            api_mode = _HA_TO_MODE.get((current_hvac, PRESET_NORMAL), Mode.POOL_HEAT)
        await self._async_set_mode(api_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature with delivery verification.

        Routes to set_spa_setpoint (RSV2) when in spa mode, or
        set_heat_setpoint (RSV1) for all other modes.
        """
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return

        if self.preset_mode == PRESET_SPA:
            result = await self.hass.async_add_executor_job(
                self.coordinator.device.set_spa_setpoint,
                int(temp),
                True,           # verify=True
                _VERIFY_TIMEOUT,
            )
        else:
            result = await self.hass.async_add_executor_job(
                self.coordinator.device.set_heat_setpoint,
                int(temp),
                True,           # verify=True
                _VERIFY_TIMEOUT,
            )

        if not result.verified:
            _LOGGER.warning(
                "Setpoint command to %s not confirmed within %ds: %s",
                self.coordinator.device.device_key,
                _VERIFY_TIMEOUT,
                result.error,
            )
        await self.coordinator.async_request_refresh()

    async def _async_set_mode(self, mode: Mode) -> None:
        result = await self.hass.async_add_executor_job(
            self.coordinator.device.set_mode,
            mode,
            True,           # verify=True
            _VERIFY_TIMEOUT,
        )
        if not result.verified:
            _LOGGER.warning(
                "Mode command to %s not confirmed within %ds: %s",
                self.coordinator.device.device_key,
                _VERIFY_TIMEOUT,
                result.error,
            )
        await self.coordinator.async_request_refresh()


def _is_recent(last_online: str, server_time: str, threshold: int) -> bool:
    """Return True if last_online is within threshold seconds of server_time."""
    try:
        last = datetime.strptime(last_online, "%Y-%m-%d %H:%M:%S")
        server = datetime.strptime(server_time, "%Y-%m-%d %H:%M:%S")
        return (server - last).total_seconds() < threshold
    except (ValueError, TypeError):
        return False
