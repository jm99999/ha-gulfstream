"""DataUpdateCoordinator for the Gulfstream integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.constants import Mode
from .api.device import Device
from .api.exceptions import AuthenticationError
from .api.models import DeviceState
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

# Return cached data on transient cloud errors rather than immediately marking
# entities unavailable. Only surface UpdateFailed after this many consecutive
# failures -- enough to survive a brief server hiccup without flapping the UI.
_TRANSIENT_ERROR_THRESHOLD = 3


class GulfstreamCoordinator(DataUpdateCoordinator[DeviceState]):
    """Polls a single Gulfstream device on a fixed interval.

    Also holds **shadow state** for in-flight commands so the UI can reflect
    the user's intent immediately while the ~5–30 s server→device→server
    round trip plays out. Entities observing this coordinator should read
    ``pending_mode`` / ``pending_setpoint`` first and fall back to the actual
    device state when no command is pending. When a command completes
    (verified, conflicted, or timed out), the command handler clears the
    pending value and triggers a refresh — the UI then snaps to whatever
    the device actually has.
    """

    def __init__(self, hass: HomeAssistant, device: Device) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.device = device
        self._consecutive_errors = 0
        # Shadow state: values the user requested but haven't been confirmed
        # by the device yet. Entities expose these (plus assumed_state=True)
        # so the UI renders them as pending/grey until verification.
        self.pending_mode: Mode | None = None
        self.pending_setpoint: int | None = None

    def mark_pending_mode(self, mode: Mode) -> None:
        self.pending_mode = mode
        self.async_update_listeners()

    def clear_pending_mode(self, expected: Mode) -> None:
        """Clear pending_mode only if it still matches the value we set.

        Prevents a later command's pending value from being cleared by an
        earlier command's finalizer (if they overlap).
        """
        if self.pending_mode == expected:
            self.pending_mode = None
            self.async_update_listeners()

    def mark_pending_setpoint(self, setpoint: int) -> None:
        self.pending_setpoint = setpoint
        self.async_update_listeners()

    def clear_pending_setpoint(self, expected: int) -> None:
        if self.pending_setpoint == expected:
            self.pending_setpoint = None
            self.async_update_listeners()

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
