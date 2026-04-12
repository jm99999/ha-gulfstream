"""Config flow for the Gulfstream Pool Heat Pump integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .api.client import GulfstreamClient
from .api.exceptions import AuthenticationError, ServerError
from .api.models import DeviceInfo
from .const import CONF_DEVICE_KEY, CONF_DEVICE_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)


class GulfstreamConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Gulfstream Pool Heat Pump."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._password: str = ""
        self._devices: list[DeviceInfo] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial credentials step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            try:
                devices = await self.hass.async_add_executor_job(
                    _login_and_list_devices, username, password
                )
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except ServerError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during login")
                errors["base"] = "unknown"
            else:
                self._username = username
                self._password = password
                self._devices = devices

                if len(devices) == 1:
                    return self._create_entry(devices[0])
                if len(devices) == 0:
                    errors["base"] = "no_devices"
                else:
                    return await self.async_step_device()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle device selection when the account has multiple devices."""
        if user_input is not None:
            device_key = user_input[CONF_DEVICE_KEY]
            device = next(d for d in self._devices if d.unique_key == device_key)
            return self._create_entry(device)

        device_options = {
            d.unique_key: f"{d.name or d.unique_key} ({d.description or d.model_name})"
            for d in self._devices
        }

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_KEY): vol.In(device_options)}
            ),
        )

    def _create_entry(self, device: DeviceInfo) -> ConfigFlowResult:
        """Create the config entry for the selected device."""
        return self.async_create_entry(
            title=device.name or device.unique_key,
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_DEVICE_KEY: device.unique_key,
                CONF_DEVICE_NAME: device.name or device.unique_key,
            },
        )


def _login_and_list_devices(username: str, password: str) -> list[DeviceInfo]:
    """Synchronous helper: login and return all devices."""
    client = GulfstreamClient(username, password)
    client.login()
    return client.list_devices()
