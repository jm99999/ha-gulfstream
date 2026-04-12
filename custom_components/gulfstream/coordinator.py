"""DataUpdateCoordinator for the Gulfstream integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.device import Device
from .api.exceptions import AuthenticationError, ServerError
from .api.models import DeviceState
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class GulfstreamCoordinator(DataUpdateCoordinator[DeviceState]):
    """Polls a single Gulfstream device on a fixed interval."""

    def __init__(self, hass: HomeAssistant, device: Device) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.device = device

    async def _async_update_data(self) -> DeviceState:
        try:
            return await self.hass.async_add_executor_job(self.device.refresh)
        except AuthenticationError as exc:
            raise UpdateFailed(f"Authentication failed: {exc}") from exc
        except ServerError as exc:
            raise UpdateFailed(f"Server error: {exc}") from exc
        except Exception as exc:
            raise UpdateFailed(f"Unexpected error: {exc}") from exc
