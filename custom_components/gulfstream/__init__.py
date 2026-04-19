# Copyright © 2026 J.L.Mann. Personal use only. See LICENSE for terms.
"""The Gulfstream Pool Heat Pump integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api.client import GulfstreamClient
from .api.device import Device
from .api.exceptions import AuthenticationError, ServerError
from .const import CONF_DEVICE_KEY, DOMAIN
from .coordinator import GulfstreamCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gulfstream from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    device_key = entry.data[CONF_DEVICE_KEY]

    try:
        client, device = await hass.async_add_executor_job(
            _create_and_login, username, password, device_key
        )
    except AuthenticationError as exc:
        _LOGGER.error("Authentication failed for Gulfstream: %s", exc)
        raise ConfigEntryNotReady(f"Authentication failed: {exc}") from exc
    except ServerError as exc:
        _LOGGER.error("Server error setting up Gulfstream: %s", exc)
        raise ConfigEntryNotReady(f"Cannot connect to server: {exc}") from exc

    coordinator = GulfstreamCoordinator(hass, device)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


def _create_and_login(
    username: str, password: str, device_key: str
) -> tuple[GulfstreamClient, Device]:
    """Synchronous helper: create client, login, return device."""
    client = GulfstreamClient(username, password)
    client.login()
    device = Device(client, device_key)
    return client, device
