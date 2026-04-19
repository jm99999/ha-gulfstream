"""Switch entity for the Gulfstream Pool Heat Pump panel lock."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_KEY, CONF_DEVICE_NAME, DOMAIN, MANUFACTURER, MODEL
from .coordinator import GulfstreamCoordinator

_LOGGER = logging.getLogger(__name__)

# Verification timeout — long enough to cover the full server→device→server
# round trip (typically 5–30 s) with margin for slow server days.
_VERIFY_TIMEOUT = 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gulfstream panel lock switch."""
    coordinator: GulfstreamCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GulfstreamPanelLockSwitch(coordinator, entry)])


class GulfstreamPanelLockSwitch(
    CoordinatorEntity[GulfstreamCoordinator], SwitchEntity
):
    """Locks or unlocks the physical control panel buttons on the heat pump.

    Uses the same shadow-state pattern as the other command-issuing entities:
    while a lock/unlock command is in flight (verified with a 60 s timeout),
    the switch reports the requested value with ``assumed_state = True`` so
    the UI renders it as pending. When the command resolves — verified,
    conflicted, or timed out — the pending value is cleared and the switch
    snaps to whatever the device actually reports.
    """

    _attr_has_entity_name = True
    _attr_name = "Panel Lock"
    _attr_icon = "mdi:lock"

    def __init__(self, coordinator: GulfstreamCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        device_key = entry.data[CONF_DEVICE_KEY]
        device_name = entry.data[CONF_DEVICE_NAME]
        self._attr_unique_id = f"{device_key}_panel_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_key)},
            name=device_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def assumed_state(self) -> bool:
        """True while a lock/unlock command is awaiting confirmation."""
        return self.coordinator.pending_locked is not None

    @property
    def is_on(self) -> bool | None:
        """Pending state if a command is in flight, else actual device state."""
        if self.coordinator.pending_locked is not None:
            return self.coordinator.pending_locked
        return self.coordinator.data.locked if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.pending_locked is not None:
            return {"pending_locked": self.coordinator.pending_locked}
        return {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send_lock(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_lock(False)

    async def _send_lock(self, locked: bool) -> None:
        """Send a lock or unlock command, holding shadow state until confirmed."""
        self.coordinator.mark_pending_locked(locked)
        try:
            fn = self.coordinator.device.lock if locked else self.coordinator.device.unlock
            result = await self.hass.async_add_executor_job(
                fn,
                True,            # verify=True
                _VERIFY_TIMEOUT,
            )
            description = "lock" if locked else "unlock"
            device_key = self.coordinator.device.device_key
            if result.verified:
                pass
            elif result.conflict:
                _LOGGER.warning(
                    "Panel %s command on %s conflicted: register now %s. Not retrying.",
                    description, device_key, result.actual_value,
                )
            else:
                _LOGGER.error(
                    "Panel %s command on %s not confirmed within %ds: %s",
                    description, device_key, _VERIFY_TIMEOUT, result.error,
                )
        finally:
            self.coordinator.clear_pending_locked(locked)
            await self.coordinator.async_request_refresh()
