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

# Return cached data on transient cloud errors rather than immediately marking
# entities unavailable. Only surface UpdateFailed after this many consecutive
# failures -- enough to survive a brief server hiccup without flapping the UI.
_TRANSIENT_ERROR_THRESHOLD = 3


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
        self._consecutive_errors = 0

    async def _async_update_data(self) -> DeviceState:
        try:
            data = await self.hass.async_add_executor_job(self.device.refresh)
            self._consecutive_errors = 0
            return data
        except AuthenticationError as exc:
            # Credentials are wrong -- not a transient error, surface immediately.
            raise UpdateFailed(f"Authentication failed: {exc}") from exc
        except Exception as exc:
            self._consecutive_errors += 1
            _LOGGER.debug(
                "Poll failed (consecutive #%d): %s", self._consecutive_errors, exc
            )
            # The captouchwifi.com API returns empty bodies and HTTP 500s ~7% of
            # requests. Return the previous state so entities don't flap on every
            # transient blip; only give up after repeated failures.
            if self._consecutive_errors < _TRANSIENT_ERROR_THRESHOLD and self.data is not None:
                _LOGGER.debug("Returning cached state after transient error")
                return self.data
            raise UpdateFailed(
                f"Device unreachable after {self._consecutive_errors} consecutive errors: {exc}"
            ) from exc
