"""Climate entity for the Gulfstream Pool Heat Pump."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.constants import (
    Mode,
    POOL_SETPOINT_MAX,
    POOL_SETPOINT_MIN,
    SPA_SETPOINT_MAX,
    SPA_SETPOINT_MIN,
)
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

# Verification timeout — long enough to cover the full server→device→server
# round trip (typically 5–30 s) with margin for slow server days.
_VERIFY_TIMEOUT = 60


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

    Pool Heat and Pool Cool share the RSV1 setpoint register, so changing
    the setpoint in one mode overwrites the other. Only Spa has its own
    independent setpoint (RSV2).

    HVAC action
    -----------
    Reflects actual operation, not just the mode knob:

    - OFF: unit is switched off
    - IDLE (waiting for flow): mode is on but pool pump is off (CHGF != 0).
      This is a normal scheduled event, not a fault.
    - IDLE (at setpoint): mode is on, pump is running, water temp has reached
      the setpoint
    - HEATING / COOLING: actively conditioning water

    Command verification
    --------------------
    All mode and setpoint changes are sent with verify=True (timeout 60 s).
    The library polls the device until it confirms the register changed, or
    times out. Outcomes are handled differently:

    - VERIFIED: state matches intent
    - CONFLICT: another user/app changed the setting concurrently; log warning,
      refresh to show the actual value, do not retry
    - FAILED: device did not apply the change within timeout; log error,
      refresh — surface the failure and let the user try again
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
    def hvac_action(self) -> HVACAction | None:
        """Actual operating state of the heat pump."""
        data = self.coordinator.data
        if not data:
            return None
        mode = int(data.mode)
        if mode == Mode.OFF:
            return HVACAction.OFF
        # Pool pump is off — heat pump can't run; this is a scheduled normal state.
        if data.registers.get("CHGF", 0) != 0:
            return HVACAction.IDLE
        # Hardware fault — heat pump stopped itself.
        if data.registers.get("FLT", 0) != 0:
            return HVACAction.IDLE
        # Running: determine direction from water temp vs setpoint.
        water = data.water_temp
        setpoint = data.setpoint
        if mode == Mode.POOL_COOL:
            return HVACAction.COOLING if water > setpoint else HVACAction.IDLE
        if mode == Mode.POOL_AUTO:
            if water < setpoint:
                return HVACAction.HEATING
            if water > setpoint:
                return HVACAction.COOLING
            return HVACAction.IDLE
        # POOL_HEAT, SPA
        return HVACAction.HEATING if water < setpoint else HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if not data:
            return {}
        chgf = data.registers.get("CHGF", 0)
        flt = data.registers.get("FLT", 0)
        if chgf != 0 and flt == 0:
            return {"status": "waiting_for_flow"}
        if flt != 0:
            return {"status": "fault"}
        return {}

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
        """Minimum setpoint for the active mode."""
        if self.preset_mode == PRESET_SPA:
            return float(SPA_SETPOINT_MIN)
        return float(POOL_SETPOINT_MIN)

    @property
    def max_temp(self) -> float:
        """Maximum setpoint for the active mode.

        Pool modes are capped at 100°F (app-enforced limit), even though the
        hardware supports 104°F. Spa mode allows the full 104°F range.
        """
        if self.preset_mode == PRESET_SPA:
            return float(SPA_SETPOINT_MAX)
        return float(POOL_SETPOINT_MAX)

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
        set_heat_setpoint (RSV1) for all other modes. Raises
        ServiceValidationError for values outside the mode's valid range.
        """
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return

        temp_int = int(temp)
        if self.preset_mode == PRESET_SPA:
            if not (SPA_SETPOINT_MIN <= temp_int <= SPA_SETPOINT_MAX):
                raise ServiceValidationError(
                    f"Spa setpoint must be {SPA_SETPOINT_MIN}–{SPA_SETPOINT_MAX}°F, got {temp_int}"
                )
            result = await self.hass.async_add_executor_job(
                self.coordinator.device.set_spa_setpoint,
                temp_int,
                True,
                _VERIFY_TIMEOUT,
            )
        else:
            if not (POOL_SETPOINT_MIN <= temp_int <= POOL_SETPOINT_MAX):
                raise ServiceValidationError(
                    f"Pool setpoint must be {POOL_SETPOINT_MIN}–{POOL_SETPOINT_MAX}°F, got {temp_int}"
                )
            result = await self.hass.async_add_executor_job(
                self.coordinator.device.set_heat_setpoint,
                temp_int,
                True,
                _VERIFY_TIMEOUT,
            )

        await self._handle_command_result(result, f"setpoint→{temp_int}°F")
        await self.coordinator.async_request_refresh()

    async def _async_set_mode(self, mode: Mode) -> None:
        result = await self.hass.async_add_executor_job(
            self.coordinator.device.set_mode,
            mode,
            True,
            _VERIFY_TIMEOUT,
        )
        await self._handle_command_result(result, f"mode→{mode.name}")
        await self.coordinator.async_request_refresh()

    async def _handle_command_result(self, result: Any, description: str) -> None:
        """Log the outcome of a command. CONFLICT and FAILED are handled differently."""
        device_key = self.coordinator.device.device_key
        if result.verified:
            return
        if result.conflict:
            # Another user/app beat us to this register. Don't retry — just
            # refresh so the UI shows the actual current value.
            _LOGGER.warning(
                "Command '%s' on %s conflicted: another source set the register "
                "to %s instead. Refreshing state.",
                description, device_key, result.actual_value,
            )
        else:
            # Timed out or server rejected. Surface as error; user must retry.
            _LOGGER.error(
                "Command '%s' on %s was not confirmed within %ds: %s",
                description, device_key, _VERIFY_TIMEOUT, result.error,
            )


def _is_recent(last_online: str, server_time: str, threshold: int) -> bool:
    """Return True if last_online is within threshold seconds of server_time."""
    try:
        last = datetime.strptime(last_online, "%Y-%m-%d %H:%M:%S")
        server = datetime.strptime(server_time, "%Y-%m-%d %H:%M:%S")
        return (server - last).total_seconds() < threshold
    except (ValueError, TypeError):
        return False
