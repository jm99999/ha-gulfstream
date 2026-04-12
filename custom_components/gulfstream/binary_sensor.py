"""Binary sensor entities for the Gulfstream Pool Heat Pump."""

from __future__ import annotations

from datetime import datetime

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
    async_add_entities([GulfstreamOnlineSensor(coordinator, entry)])


class GulfstreamOnlineSensor(
    CoordinatorEntity[GulfstreamCoordinator], BinarySensorEntity
):
    """Indicates whether the heat pump WiFi module is connected to the cloud.

    The WiFi module polls the server every 3–10 seconds. We consider the
    connection stale — and report ``off`` (Disconnected) — if the device
    hasn't checked in within STALE_THRESHOLD_SECONDS (default 3 minutes).

    This is stricter than a simple "is the last_online timestamp recent?"
    check: we compare last_online against server_time so that clock drift
    on either end doesn't cause false positives.
    """

    _attr_has_entity_name = True
    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: GulfstreamCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        device_key = entry.data[CONF_DEVICE_KEY]
        device_name = entry.data[CONF_DEVICE_NAME]
        self._attr_unique_id = f"{device_key}_online"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_key)},
            name=device_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def is_on(self) -> bool | None:
        """True when the WiFi module checked in within the stale threshold."""
        if not self.coordinator.data:
            return None
        return _is_recent(
            self.coordinator.data.last_online,
            self.coordinator.data.server_time,
            STALE_THRESHOLD_SECONDS,
        )

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        return {
            "last_online": self.coordinator.data.last_online,
            "server_time": self.coordinator.data.server_time,
            "stale_threshold_seconds": STALE_THRESHOLD_SECONDS,
        }


def _is_recent(last_online: str, server_time: str, threshold: int) -> bool:
    """Return True if last_online is within threshold seconds of server_time."""
    try:
        last = datetime.strptime(last_online, "%Y-%m-%d %H:%M:%S")
        server = datetime.strptime(server_time, "%Y-%m-%d %H:%M:%S")
        return (server - last).total_seconds() < threshold
    except (ValueError, TypeError):
        return False
