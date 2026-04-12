"""Switch entity for the Gulfstream Pool Heat Pump panel lock."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
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
    """Set up the Gulfstream panel lock switch."""
    coordinator: GulfstreamCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GulfstreamPanelLockSwitch(coordinator, entry)])


class GulfstreamPanelLockSwitch(
    CoordinatorEntity[GulfstreamCoordinator], SwitchEntity
):
    """Locks or unlocks the physical control panel buttons on the heat pump."""

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
    def is_on(self) -> bool | None:
        """True when the panel is locked."""
        return self.coordinator.data.locked if self.coordinator.data else None

    async def async_turn_on(self, **kwargs) -> None:
        """Lock the physical panel."""
        await self.hass.async_add_executor_job(self.coordinator.device.lock)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Unlock the physical panel."""
        await self.hass.async_add_executor_job(self.coordinator.device.unlock)
        await self.coordinator.async_request_refresh()
