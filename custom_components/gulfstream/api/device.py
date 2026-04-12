"""High-level device interface with command verification.

This module provides the :class:`Device` class, which wraps a
:class:`GulfstreamClient` and a specific device key to offer a clean API
for reading state and sending commands with optional verification.

Every command method returns a :class:`CommandResult` that acts as a
promise -- the caller can fire-and-forget, poll it later, or block
until the change is verified on the device hardware.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from .client import GulfstreamClient  # noqa: F401 (re-exported by __init__)
from .constants import (
    DEFAULT_VERIFY_INTERVAL,
    DEFAULT_VERIFY_TIMEOUT,
    Mode,
    REG_LOCK_DISABLE,
    REG_MODE,
    REG_SETPOINT,
)
from .models import CommandResult, CommandStatus, DeviceInfo, DeviceState

logger = logging.getLogger(__name__)


class Device:
    """Controls a single pool heat pump.

    Create via :meth:`GulfstreamClient.list_devices` and
    :func:`Device.from_info`, or directly with a known device key::

        client = GulfstreamClient("user", "pass")
        client.login()

        # From device list
        devices = client.list_devices()
        dev = Device(client, devices[0].unique_key)

        # Read state
        state = dev.refresh()
        print(f"{state.water_temp}°F, mode={state.mode_text}")

        # Set setpoint (fire-and-forget)
        result = dev.set_heat_setpoint(88)

        # Set setpoint (block until verified)
        result = dev.set_heat_setpoint(88, verify=True, timeout=30)
        if result.verified:
            print("Applied!")

        # Set setpoint (check later)
        result = dev.set_heat_setpoint(88)
        # ... do other work ...
        result.check()  # single poll
        print(result.status)

    Args:
        client: An authenticated :class:`GulfstreamClient`.
        device_key: The device's unique key (e.g. ``"0000CP165478"``).
        info: Optional :class:`DeviceInfo` from the device list.
    """

    def __init__(
        self,
        client: GulfstreamClient,
        device_key: str,
        info: Optional[DeviceInfo] = None,
    ):
        self._client = client
        self.device_key = device_key
        self.info = info
        self._last_state: Optional[DeviceState] = None
        self._last_state_time: float = 0

    @classmethod
    def from_info(cls, client: GulfstreamClient, info: DeviceInfo) -> Device:
        """Create a Device from a :class:`DeviceInfo` returned by
        :meth:`GulfstreamClient.list_devices`."""
        return cls(client, info.unique_key, info=info)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def refresh(self) -> DeviceState:
        """Fetch the latest state from the server.

        Stores the result in :attr:`last_state` and returns it.
        """
        self._last_state = self._client.get_device_state(self.device_key)
        self._last_state_time = time.time()
        return self._last_state

    @property
    def last_state(self) -> Optional[DeviceState]:
        """The most recently fetched state, or None if never refreshed."""
        return self._last_state

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def set_heat_setpoint(
        self,
        temperature: int,
        verify: bool = False,
        timeout: float = DEFAULT_VERIFY_TIMEOUT,
        interval: float = DEFAULT_VERIFY_INTERVAL,
    ) -> CommandResult:
        """Change the heat setpoint.

        Sends ``thermostatSetModeData`` with the new temperature in the
        heat-setpoint position.  The current mode is preserved.

        Args:
            temperature: Target temperature in °F (typically 50--104).
            verify: If True, block until the device confirms the change.
            timeout: Max seconds to wait when ``verify=True``.
            interval: Seconds between verification polls.

        Returns:
            A :class:`CommandResult` that tracks delivery status.

        Example::

            # Fire and forget
            dev.set_heat_setpoint(88)

            # Block until confirmed
            result = dev.set_heat_setpoint(88, verify=True)
            assert result.verified

            # Check later
            result = dev.set_heat_setpoint(88)
            # ... later ...
            if result.wait(timeout=30):
                print("Done")
        """
        current_mode = self._get_current_mode()
        mode_data = [current_mode, 0, temperature, 0, 0, 0]

        result = self._send_mode_data(
            mode_data, verify_register=REG_SETPOINT, verify_value=temperature)

        if verify and result.pending:
            result.wait(timeout=timeout, interval=interval)
        return result

    def set_mode(
        self,
        mode: Mode,
        verify: bool = False,
        timeout: float = DEFAULT_VERIFY_TIMEOUT,
        interval: float = DEFAULT_VERIFY_INTERVAL,
    ) -> CommandResult:
        """Change the operating mode.

        Args:
            mode: Target mode (e.g. ``Mode.POOL_HEAT``, ``Mode.OFF``).
            verify: If True, block until the device confirms the change.
            timeout: Max seconds to wait when ``verify=True``.
            interval: Seconds between verification polls.

        Returns:
            A :class:`CommandResult`.
        """
        current_setpoint = self._get_current_setpoint()
        mode_data = [int(mode), 0, current_setpoint, 0, 0, 0]

        result = self._send_mode_data(
            mode_data, verify_register=REG_MODE, verify_value=int(mode))

        if verify and result.pending:
            result.wait(timeout=timeout, interval=interval)
        return result

    def lock(
        self,
        verify: bool = False,
        timeout: float = DEFAULT_VERIFY_TIMEOUT,
    ) -> CommandResult:
        """Lock the physical control panel on the heat pump.

        Returns:
            A :class:`CommandResult`.
        """
        return self._set_field(
            REG_LOCK_DISABLE, 1, verify=verify, timeout=timeout)

    def unlock(
        self,
        verify: bool = False,
        timeout: float = DEFAULT_VERIFY_TIMEOUT,
    ) -> CommandResult:
        """Unlock the physical control panel on the heat pump.

        Returns:
            A :class:`CommandResult`.
        """
        return self._set_field(
            REG_LOCK_DISABLE, 0, verify=verify, timeout=timeout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_mode_data(
        self,
        mode_data: list,
        verify_register: str,
        verify_value: int,
    ) -> CommandResult:
        """Send thermostatSetModeData and return a trackable CommandResult.

        Snapshots the register value *before* sending so that conflict
        detection can distinguish "changed to a different value" (another
        user) from "didn't change at all" (command lost in queue).
        """
        # Snapshot the current value for conflict detection.
        value_before = self._read_register(verify_register)

        result = CommandResult(
            action="thermostatSetModeData",
            device_key=self.device_key,
            value_before=value_before,
            _verify_register=verify_register,
            _verify_value=verify_value,
            _verify_fn=self._fetch_state_for_verify,
        )

        try:
            resp = self._client.set_mode_data(self.device_key, mode_data)
            result.server_accepted = resp.get("result") == "success"
            if not result.server_accepted:
                result.status = CommandStatus.FAILED
                result.error = resp.get("message", "Server rejected command")
            else:
                logger.info(
                    "Command sent to %s: modeData=%s (was %s=%s, want %s)",
                    self.device_key, mode_data,
                    verify_register, value_before, verify_value)
        except Exception as exc:
            result.status = CommandStatus.ERROR
            result.error = str(exc)
            logger.error("Command failed for %s: %s", self.device_key, exc)

        return result

    def _set_field(
        self,
        register: str,
        value: int,
        verify: bool = False,
        timeout: float = DEFAULT_VERIFY_TIMEOUT,
    ) -> CommandResult:
        """Send thermostatSetFields for a single register."""
        value_before = self._read_register(register)

        result = CommandResult(
            action="thermostatSetFields",
            device_key=self.device_key,
            value_before=value_before,
            _verify_register=register,
            _verify_value=value,
            _verify_fn=self._fetch_state_for_verify,
        )

        try:
            resp = self._client.set_fields(
                self.device_key, {register: value})
            result.server_accepted = resp.get("result") == "success"
            if not result.server_accepted:
                result.status = CommandStatus.FAILED
                result.error = resp.get("message", "Server rejected command")
            else:
                logger.info(
                    "Field %s=%s sent to %s (accepted)",
                    register, value, self.device_key)
        except Exception as exc:
            result.status = CommandStatus.ERROR
            result.error = str(exc)

        if verify and result.pending:
            result.wait(timeout=timeout)
        return result

    def _fetch_state_for_verify(self) -> Optional[DeviceState]:
        """Fetch state for command verification (used by CommandResult).

        Returns None on any error so that verification can retry on the
        next poll instead of aborting.  The server returns empty responses
        ~7% of the time, so transient failures are normal.
        """
        try:
            return self._client.get_device_state(self.device_key)
        except Exception:
            return None

    def _read_register(self, register: str) -> Optional[int]:
        """Read a single register value, using cached state if available.

        This is called *before* sending a command to snapshot the current
        value for conflict detection.  Uses the last cached state if
        available (< 30 seconds old) to avoid an extra API call.
        """
        if self._last_state is not None:
            age = time.time() - self._last_state_time
            if age < 30:
                return self._last_state.registers.get(register)
        try:
            state = self.refresh()
            return state.registers.get(register)
        except Exception:
            return None

    def _get_current_mode(self) -> int:
        """Get the current mode, refreshing if needed."""
        if self._last_state is not None:
            return int(self._last_state.mode)
        state = self.refresh()
        return int(state.mode)

    def _get_current_setpoint(self) -> int:
        """Get the current setpoint, refreshing if needed."""
        if self._last_state is not None:
            return self._last_state.setpoint
        state = self.refresh()
        return state.setpoint
