# Copyright © 2026 J.L.Mann. Personal use only. See LICENSE for terms.
"""Binary sensor entities for the Gulfstream Pool Heat Pump."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_KEY,
    CONF_DEVICE_NAME,
    DOMAIN,
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
    """Set up Gulfstream binary sensor entities."""
    coordinator: GulfstreamCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        GulfstreamOnlineSensor(coordinator, entry),
        GulfstreamFlowSensor(coordinator, entry),
    ])


class _GulfstreamBinarySensor(
    CoordinatorEntity[GulfstreamCoordinator], BinarySensorEntity
):
    """Base class for Gulfstream binary sensors."""

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


class GulfstreamOnlineSensor(_GulfstreamBinarySensor):
    """Indicates whether the heat pump WiFi module is connected to the cloud.

    The WiFi module polls the server every 3–10 seconds. We use
    DeviceState.is_online (last_online within 60 s of server_time) as the
    authoritative test so clock drift on either side doesn't cause false
    positives.
    """

    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: GulfstreamCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_KEY]}_online"

    @property
    def is_on(self) -> bool | None:
        """True when the WiFi module checked in within the last 60 seconds."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.is_online

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        return {
            "last_online": self.coordinator.data.last_online,
            "server_time": self.coordinator.data.server_time,
        }


class GulfstreamFlowSensor(_GulfstreamBinarySensor):
    """Indicates whether the pool circulation pump is running.

    Reads the CHGF register (flow indicator):
      CHGF = 0  → water is flowing → pump is running  → is_on = True
      CHGF = 8  → no flow          → pump is off      → is_on = False

    The pool pump typically runs on its own timer (roughly half the day).
    When it's off the heat pump cannot condition water and goes idle — this
    is completely normal and should NOT be surfaced as a fault.
    """

    _attr_name = "Pool Pump"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: GulfstreamCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_KEY]}_flow"

    @property
    def is_on(self) -> bool | None:
        """True when water is flowing through the heat exchanger."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.registers.get("CHGF", 0) == 0

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        return {
            "chgf": self.coordinator.data.registers.get("CHGF"),
            "gen10": self.coordinator.data.registers.get("GEN10"),
        }
