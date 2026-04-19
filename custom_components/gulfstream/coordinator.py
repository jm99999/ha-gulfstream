# Copyright © 2026 J.L.Mann. Personal use only. See LICENSE for terms.
"""DataUpdateCoordinator for the Gulfstream integration."""

from __future__ import annotations

import logging
import time
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

# Water sensor reads pipe water, not pool water, for the first ~minute after
# circulation starts. Suppress water temp readings until the pump has been
# running for at least this long.
_FLOW_STABILIZATION_SECONDS = 60


class GulfstreamCoordinator(DataUpdateCoordinator[DeviceState]):
    """Polls a single Gulfstream device on a fixed interval.

    Also holds **shadow state** for in-flight commands so the UI can reflect
    the user's intent immediately while the ~5–30 s server→device→server
    round trip plays out. Entities observing this coordinator should read
    ``pending_*`` first and fall back to the actual device state when no
    command is pending. When a command completes (verified, conflicted, or
    timed out), the command handler clears the pending value and triggers
    a refresh — the UI then snaps to whatever the device actually has.

    Tracks pump transitions (CHGF 0 ↔ non-zero) so consumers can tell
    whether the water temperature reading has had time to stabilize.
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
        # Shadow state: user-requested values not yet confirmed by the device.
        # Entities expose these (with assumed_state=True) so the UI renders
        # them as pending until verification clears them.
        self.pending_mode: Mode | None = None
        self.pending_setpoint: int | None = None
        self.pending_locked: bool | None = None
        # Monotonic timestamp when we first observed the pump running (CHGF=0)
        # in the current run. None whenever the pump is currently off.
        self._flow_started_at: float | None = None

    # ------------------------------------------------------------------
    # Shadow-state helpers
    # ------------------------------------------------------------------

    def mark_pending_mode(self, mode: Mode) -> None:
        self.pending_mode = mode
        self.async_update_listeners()

    def clear_pending_mode(self, expected: Mode) -> None:
        """Clear pending_mode only if it still matches what we set.

        Prevents a later command's pending value from being cleared by an
        earlier command's finalizer if they overlap.
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

    def mark_pending_locked(self, locked: bool) -> None:
        self.pending_locked = locked
        self.async_update_listeners()

    def clear_pending_locked(self, expected: bool) -> None:
        if self.pending_locked == expected:
            self.pending_locked = None
            self.async_update_listeners()

    # ------------------------------------------------------------------
    # Water-temp validity
    # ------------------------------------------------------------------

    @property
    def water_temp_valid(self) -> bool:
        """True when the pump has been running long enough to trust the reading.

        The RMT sensor sits in the plumbing downstream of the heat exchanger.
        Before circulation begins it reads the temperature of stagnant pipe
        water (roughly air temperature). The reading doesn't represent pool
        water until fresh water has flushed through the exchanger, which
        takes about a minute. Until then, return False so consumers can
        suppress the reading.
        """
        if self._flow_started_at is None:
            return False
        return (time.monotonic() - self._flow_started_at) >= _FLOW_STABILIZATION_SECONDS

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> DeviceState:
        try:
            data = await self.hass.async_add_executor_job(self.device.refresh)
            self._consecutive_errors = 0
            self._track_flow(data)
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

    def _track_flow(self, data: DeviceState) -> None:
        """Record the first moment we see CHGF=0 (pump running) since last off.

        CHGF=0 means flow is present. When we first observe flow, start the
        stabilization timer. When flow stops (CHGF != 0), clear the timer so
        the next run will restart the 60-second window from zero.
        """
        chgf = data.registers.get("CHGF", 0)
        if chgf == 0:
            if self._flow_started_at is None:
                self._flow_started_at = time.monotonic()
        else:
            self._flow_started_at = None
