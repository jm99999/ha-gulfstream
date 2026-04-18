"""Mode select entity for the Gulfstream Pool Heat Pump.

Exposes the device's full 5-mode enum (Off / Pool Heat / Pool Cool /
Pool Heat-Cool / Spa) so the user can reach Spa mode — which HA's
climate entity can't represent natively.

Options are filtered by what the device reports as enabled (DF1 controls
Pool Cool visibility, DF2 controls Pool Heat/Cool visibility). Off, Pool
Heat and Spa are always available.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
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
)
from .coordinator import GulfstreamCoordinator

_LOGGER = logging.getLogger(__name__)

# Verification timeout — long enough to cover the full server→device→server
# round trip (typically 5–30 s) with margin for slow server days.
_VERIFY_TIMEOUT = 60

_LABEL_OFF = "Off"
_LABEL_POOL_HEAT = "Pool Heat"
_LABEL_POOL_COOL = "Pool Cool"
_LABEL_POOL_AUTO = "Pool Heat/Cool"
_LABEL_SPA = "Spa"

_LABEL_TO_MODE: dict[str, Mode] = {
    _LABEL_OFF:       Mode.OFF,
    _LABEL_POOL_HEAT: Mode.POOL_HEAT,
    _LABEL_POOL_COOL: Mode.POOL_COOL,
    _LABEL_POOL_AUTO: Mode.POOL_AUTO,
    _LABEL_SPA:       Mode.SPA,
}

_MODE_TO_LABEL: dict[int, str] = {int(m): label for label, m in _LABEL_TO_MODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gulfstream mode select entity."""
    coordinator: GulfstreamCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GulfstreamModeSelect(coordinator, entry)])


class GulfstreamModeSelect(
    CoordinatorEntity[GulfstreamCoordinator], SelectEntity
):
    """Select the heat pump's operating mode, including Spa."""

    _attr_has_entity_name = True
    _attr_name = "Mode"
    _attr_icon = "mdi:pool-thermometer"

    def __init__(
        self, coordinator: GulfstreamCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        device_key = entry.data[CONF_DEVICE_KEY]
        device_name = entry.data[CONF_DEVICE_NAME]
        self._attr_unique_id = f"{device_key}_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_key)},
            name=device_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def assumed_state(self) -> bool:
        """True while a mode change is awaiting device confirmation."""
        return self.coordinator.pending_mode is not None

    @property
    def options(self) -> list[str]:
        """Available options filtered by device-reported DF1/DF2 flags."""
        opts = [_LABEL_OFF, _LABEL_POOL_HEAT]
        data = self.coordinator.data
        if data and data.pool_cool_enabled:
            opts.append(_LABEL_POOL_COOL)
        if data and data.pool_heat_cool_enabled:
            opts.append(_LABEL_POOL_AUTO)
        opts.append(_LABEL_SPA)
        return opts

    @property
    def current_option(self) -> str | None:
        """Pending mode if a command is in flight, else actual device mode."""
        if self.coordinator.pending_mode is not None:
            return _MODE_TO_LABEL.get(int(self.coordinator.pending_mode))
        if not self.coordinator.data:
            return None
        return _MODE_TO_LABEL.get(int(self.coordinator.data.mode))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.pending_mode is not None:
            return {"pending_mode": self.coordinator.pending_mode.name}
        return {}

    async def async_select_option(self, option: str) -> None:
        """Change operating mode, holding shadow state until confirmed."""
        mode = _LABEL_TO_MODE.get(option)
        if mode is None:
            _LOGGER.warning("Unknown mode option: %s", option)
            return

        self.coordinator.mark_pending_mode(mode)
        try:
            result = await self.hass.async_add_executor_job(
                self.coordinator.device.set_mode,
                mode,
                True,
                _VERIFY_TIMEOUT,
            )
            if result.verified:
                pass
            elif result.conflict:
                _LOGGER.warning(
                    "Mode command on %s conflicted: register now %s, we sent %s. "
                    "Not retrying.",
                    self.coordinator.device.device_key,
                    result.actual_value,
                    int(mode),
                )
            else:
                _LOGGER.error(
                    "Mode command on %s was not confirmed within %ds: %s",
                    self.coordinator.device.device_key,
                    _VERIFY_TIMEOUT,
                    result.error,
                )
        finally:
            self.coordinator.clear_pending_mode(mode)
            await self.coordinator.async_request_refresh()
