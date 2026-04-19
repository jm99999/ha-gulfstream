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
    MANUFACTURER,
    MODEL,
    STALE_THRESHOLD_SECONDS,
)
from .coordinator import GulfstreamCoordinator

_LOGGER = logging.getLogger(__name__)

# Device Mode → HVACMode. Spa maps to HEAT because HA's climate model
# has no native spa concept; use the Mode select entity to enter spa.
_MODE_TO_HVAC: dict[int, HVACMode] = {
    Mode.OFF:        HVACMode.OFF,
    Mode.POOL_HEAT:  HVACMode.HEAT,
    Mode.POOL_COOL:  HVACMode.COOL,
    Mode.POOL_AUTO:  HVACMode.HEAT_COOL,
    Mode.SPA:        HVACMode.HEAT,
}

# HVACMode selected via the climate card → device Mode. Climate never
# sends SPA — that's reachable only through the Mode select entity.
_HVAC_TO_MODE: dict[HVACMode, Mode] = {
    HVACMode.OFF:        Mode.OFF,
    HVACMode.HEAT:       Mode.POOL_HEAT,
    HVACMode.COOL:       Mode.POOL_COOL,
    HVACMode.HEAT_COOL:  Mode.POOL_AUTO,
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
    The device supports five operating modes. Climate maps them as follows:

    =====================  ================
    Device mode            HA HVAC mode
    =====================  ================
    Off                    off
    Pool Heat              heat
    Pool Cool              cool       *
    Pool Heat/Cool (Auto)  heat_cool  *
    Spa                    heat  (display-only, set via Mode select entity)
    =====================  ================

    (*) Pool Cool and Pool Heat/Cool are only shown when the device reports
    them as enabled (DF1 / DF2 registers).

    Because HA's climate model has no "spa" mode, Spa is selected through
    the companion Mode select entity. When the device is in Spa mode, this
    climate entity shows HEAT; its target temperature reflects the spa
    setpoint (RSV2); and adjusting the temperature writes RSV2. Picking any
    HEAT/COOL mode in the climate card exits spa and enters the chosen
    pool mode.

    Shadow / pending state
    ----------------------
    Commands take 5–30 s (up to 60 s) to reach the device. While a
    command is in flight, the entity reports the requested value (with
    ``assumed_state = True`` so the UI renders it as pending) rather than
    the stale device-reported value. When the command completes —
    verified, conflicted, or timed out — the pending value is cleared and
    the entity snaps to the actual device state.
    """

    _attr_has_entity_name = True
    _attr_name = None  # entity name = device name (HA primary-feature convention)
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_target_temperature_step = 1.0
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _effective_mode(self) -> Mode | None:
        """Mode to render: pending if set, else actual device mode."""
        if self.coordinator.pending_mode is not None:
            return self.coordinator.pending_mode
        if self.coordinator.data is not None:
            return Mode(int(self.coordinator.data.mode))
        return None

    def _is_spa(self) -> bool:
        return self._effective_mode() == Mode.SPA

    # ------------------------------------------------------------------
    # State / display
    # ------------------------------------------------------------------

    @property
    def assumed_state(self) -> bool:
        """True while a command is in flight awaiting device confirmation."""
        return (
            self.coordinator.pending_mode is not None
            or self.coordinator.pending_setpoint is not None
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
    def hvac_mode(self) -> HVACMode | None:
        mode = self._effective_mode()
        if mode is None:
            return None
        return _MODE_TO_HVAC.get(int(mode), HVACMode.OFF)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Actual operating state of the heat pump.

        Shadow state does not drive hvac_action — this should always
        reflect what the device actually reports.
        """
        data = self.coordinator.data
        if not data:
            return None
        mode = int(data.mode)
        if mode == Mode.OFF:
            return HVACAction.OFF
        # Pool pump off → scheduled idle, not a fault.
        if data.registers.get("CHGF", 0) != 0:
            return HVACAction.IDLE
        # Hardware fault — heat pump stopped itself.
        if data.registers.get("FLT", 0) != 0:
            return HVACAction.IDLE
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
        attrs: dict[str, Any] = {}
        chgf = data.registers.get("CHGF", 0)
        flt = data.registers.get("FLT", 0)
        if chgf != 0 and flt == 0:
            attrs["status"] = "waiting_for_flow"
        elif flt != 0:
            attrs["status"] = "fault"
        if self.coordinator.pending_mode is not None:
            attrs["pending_mode"] = self.coordinator.pending_mode.name
        if self.coordinator.pending_setpoint is not None:
            attrs["pending_setpoint"] = self.coordinator.pending_setpoint
        return attrs

    @property
    def current_temperature(self) -> float | None:
        """Current water temperature — None while it can't be trusted.

        The reading is suppressed when the device is stale, the pump is
        off, or the pump has been running for less than ~60 s (pipe water
        hasn't been flushed yet). Mirrors GulfstreamWaterTempSensor.
        """
        data = self.coordinator.data
        if not data:
            return None
        if not _is_recent(data.last_online, data.server_time, STALE_THRESHOLD_SECONDS):
            return None
        if not self.coordinator.water_temp_valid:
            return None
        return float(data.water_temp)

    @property
    def target_temperature(self) -> float | None:
        """Active setpoint — pending value while a command is in flight,
        otherwise the register that matches the current mode (RSV1 for
        pool modes, RSV2 for spa)."""
        if self.coordinator.pending_setpoint is not None:
            return float(self.coordinator.pending_setpoint)
        return self.coordinator.data.setpoint if self.coordinator.data else None

    @property
    def min_temp(self) -> float:
        if self._is_spa():
            return float(SPA_SETPOINT_MIN)
        return float(POOL_SETPOINT_MIN)

    @property
    def max_temp(self) -> float:
        """Mode-specific maximum: pool 100 °F (app cap), spa 104 °F (hardware cap)."""
        if self._is_spa():
            return float(SPA_SETPOINT_MAX)
        return float(POOL_SETPOINT_MAX)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode. Never selects Spa — use the Mode select entity."""
        api_mode = _HVAC_TO_MODE.get(hvac_mode, Mode.OFF)
        await self._send_mode(api_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature with delivery verification.

        Routes to set_spa_setpoint (RSV2) when the device is in spa mode,
        set_heat_setpoint (RSV1) otherwise. Raises ServiceValidationError
        for values outside the mode's valid range.
        """
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return

        temp_int = int(temp)
        is_spa = self._is_spa()

        if is_spa:
            if not (SPA_SETPOINT_MIN <= temp_int <= SPA_SETPOINT_MAX):
                raise ServiceValidationError(
                    f"Spa setpoint must be {SPA_SETPOINT_MIN}–{SPA_SETPOINT_MAX}°F, got {temp_int}"
                )
        else:
            if not (POOL_SETPOINT_MIN <= temp_int <= POOL_SETPOINT_MAX):
                raise ServiceValidationError(
                    f"Pool setpoint must be {POOL_SETPOINT_MIN}–{POOL_SETPOINT_MAX}°F, got {temp_int}"
                )

        # Optimistic UI: show the target immediately, flagged as assumed.
        self.coordinator.mark_pending_setpoint(temp_int)
        try:
            if is_spa:
                result = await self.hass.async_add_executor_job(
                    self.coordinator.device.set_spa_setpoint,
                    temp_int,
                    True,
                    _VERIFY_TIMEOUT,
                )
            else:
                result = await self.hass.async_add_executor_job(
                    self.coordinator.device.set_heat_setpoint,
                    temp_int,
                    True,
                    _VERIFY_TIMEOUT,
                )
            await self._handle_command_result(result, f"setpoint→{temp_int}°F")
        finally:
            self.coordinator.clear_pending_setpoint(temp_int)
            await self.coordinator.async_request_refresh()

    async def _send_mode(self, mode: Mode) -> None:
        """Send a mode change, holding shadow state until confirmed."""
        self.coordinator.mark_pending_mode(mode)
        try:
            result = await self.hass.async_add_executor_job(
                self.coordinator.device.set_mode,
                mode,
                True,
                _VERIFY_TIMEOUT,
            )
            await self._handle_command_result(result, f"mode→{mode.name}")
        finally:
            self.coordinator.clear_pending_mode(mode)
            await self.coordinator.async_request_refresh()

    async def _handle_command_result(self, result: Any, description: str) -> None:
        """Log command outcomes. Conflict and failure are handled differently."""
        device_key = self.coordinator.device.device_key
        if result.verified:
            return
        if result.conflict:
            _LOGGER.warning(
                "Command '%s' on %s conflicted: another source set the register "
                "to %s instead. Not retrying.",
                description, device_key, result.actual_value,
            )
        else:
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
