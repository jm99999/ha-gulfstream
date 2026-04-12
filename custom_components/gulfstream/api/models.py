"""Data models for the Gulfstream pool heat pump API.

All models are plain dataclasses -- no ORM, no validation framework.

Key classes:

- :class:`DeviceInfo` -- lightweight record from the device list
- :class:`DeviceState` -- full register snapshot from a device
- :class:`CommandResult` -- promise/future that tracks whether a command
  was applied on the device, including detection of conflicts when another
  user or app changes the same setting concurrently

Conflict detection
------------------

The cloud server has no locking, transactions, or last-writer-wins
semantics.  Multiple users (family members, cron scripts, the mobile app) can
send conflicting commands at nearly the same time.  The server accepts all of
them with ``result: "success"`` but only one will actually take effect on the
device -- and which one is unpredictable due to the polling-based architecture.

:class:`CommandResult` handles this by recording the register value *before*
the command was sent.  During verification it can distinguish three outcomes:

1. **Verified** -- the register now holds the value we sent.
2. **Conflict** -- the register changed, but to a *different* value than we
   sent.  Another user or the device itself overwrote our change.
3. **Timeout** -- the register didn't change at all within the allowed window.
   The command was likely lost in the server-to-device queue.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .constants import Mode, REG_SETPOINT, REG_WATER_TEMP, REG_MODE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Device info (from getPasDevices)
# ---------------------------------------------------------------------------

@dataclass
class DeviceInfo:
    """Lightweight device record returned by the device-list endpoint.

    This is what you get *before* fetching full details.  Use
    :meth:`GulfstreamClient.list_devices` to obtain these.

    Attributes:
        id: Server-side numeric ID (e.g. ``"79331"``).
        unique_key: Device key used in all API calls (e.g. ``"0000CP165478"``).
        name: User-assigned device name (e.g. ``"Gulf"``).
        description: User-assigned description (e.g. ``"Pool"``).
        model_name: Backend model identifier (e.g. ``"ICM_POOL_AND_SPA"``).
        zipcode: Location zip code (used for weather display in the app).
        owned: True if the authenticated user owns this device.
            False if it was shared with them by another user.
        owner: Username of the device owner.
        online: True if the device's WiFi module is currently connected
            to the server (typically polls every 3--10 seconds).
    """
    id: str
    unique_key: str
    name: str
    description: str
    model_name: str
    zipcode: str
    owned: bool
    owner: str
    online: bool


# ---------------------------------------------------------------------------
# Device state (from thermostatGetDetail)
# ---------------------------------------------------------------------------

@dataclass
class DeviceState:
    """Full snapshot of a device's current state.

    Contains both interpreted high-level fields and the raw register
    dictionary for advanced use.  Obtained via
    :meth:`Device.refresh` or :meth:`GulfstreamClient.get_device_state`.

    The ``registers`` dict contains every register the backend returned
    (typically 200+), keyed by their short code (e.g. ``"MD"``, ``"RSV1"``).
    See ``discovery/decompiled/register-map.md`` for the full register
    reference.

    Attributes:
        last_online: Timestamp of the device's last server check-in,
            e.g. ``"2026-04-11 21:51:29"``.
        server_time: The server's current time when this state was fetched.
        mode: Current operating mode (:class:`Mode` enum).
        setpoint: Displayed heat setpoint in degrees (RSV1 register).
        water_temp: Current water temperature in degrees (RSV2 register).
        locked: True if the physical control panel buttons are locked.
        max_heat: Maximum allowed heat setpoint (hardware limit, typically 104).
        min_heat: Minimum allowed heat setpoint (hardware limit, typically 50).
        registers: Raw register dump -- every register code and its integer
            value.  Use this for advanced access to fields not exposed as
            top-level attributes.
    """
    last_online: str
    server_time: str
    mode: Mode
    setpoint: int
    water_temp: int
    locked: bool
    max_heat: int
    min_heat: int
    registers: Dict[str, int] = field(default_factory=dict)

    @property
    def is_online(self) -> bool:
        """True if the device was online within the last 60 seconds.

        Compares ``last_online`` against ``server_time``.  If either
        timestamp is missing or unparseable, returns False.
        """
        try:
            last = datetime.strptime(self.last_online, "%Y-%m-%d %H:%M:%S")
            server = datetime.strptime(self.server_time, "%Y-%m-%d %H:%M:%S")
            return (server - last).total_seconds() < 60
        except (ValueError, TypeError):
            return False

    @property
    def mode_text(self) -> str:
        """Human-readable mode string (e.g. ``"Pool Heat"``)."""
        labels = {
            Mode.OFF: "Off",
            Mode.POOL_HEAT: "Pool Heat",
            Mode.POOL_COOL: "Pool Cool",
            Mode.POOL_AUTO: "Pool Heat/Cool",
            Mode.SPA: "Spa",
        }
        return labels.get(self.mode, f"Unknown ({self.mode})")


# ---------------------------------------------------------------------------
# CommandResult -- the "promise" for command verification
# ---------------------------------------------------------------------------

class CommandStatus(Enum):
    """Lifecycle states of a command sent to the device.

    A command progresses from PENDING to one of the terminal states:

    .. code-block:: text

        PENDING ──┬──> VERIFIED   (device applied our value)
                  ├──> CONFLICT   (register changed, but not to our value)
                  ├──> FAILED     (timed out or server rejected)
                  └──> ERROR      (exception during verification)
    """
    PENDING = "pending"
    VERIFIED = "verified"
    CONFLICT = "conflict"
    FAILED = "failed"
    ERROR = "error"


@dataclass
class CommandResult:
    """Tracks a command from submission through device-level verification.

    Returned immediately by command methods on :class:`Device`.  The caller
    can choose how to handle it:

    **Fire-and-forget** -- ignore the result entirely.  Appropriate for
    non-critical changes where you'll notice on the next status read if
    it didn't take::

        device.set_heat_setpoint(88)

    **Block until verified** -- waits for the device to confirm, with
    timeout.  Use when you need certainty before proceeding::

        result = device.set_heat_setpoint(88, verify=True, timeout=30)
        if result.verified:
            print("Applied!")
        elif result.conflict:
            print(f"Someone else changed it to {result.actual_value}")
        else:
            print(f"Failed: {result.error}")

    **Check later** -- get the promise, do other work, then poll or wait::

        result = device.set_heat_setpoint(88)
        # ... do other work ...
        result.check()       # single poll
        result.wait(30)      # or block

    Conflict detection
    ~~~~~~~~~~~~~~~~~~

    When multiple users (family members, app, cron script) change the same
    setting concurrently, this class detects the conflict:

    - If the register holds our target value → VERIFIED
    - If the register changed from the pre-command value but holds a
      *different* value than we sent → CONFLICT
    - If the register never changed → FAILED (timeout)

    The :attr:`actual_value` field always holds the last observed register
    value, so the caller can decide how to respond.

    Attributes:
        action: The API action that was sent (e.g. ``"thermostatSetModeData"``).
        device_key: Unique key of the target device.
        sent_at: Unix timestamp when the command was sent.
        server_accepted: True if the server returned ``result: "success"``.
            Note: this does NOT mean the device received the command.
        status: Current lifecycle state (:class:`CommandStatus`).
        error: Human-readable error message, set on FAILED/ERROR/CONFLICT.
        actual_value: The last observed value of the target register, or
            None if never checked.  Useful for conflict resolution.
        value_before: The register value *before* the command was sent.
            Used to detect whether the register changed at all.
        history: List of ``(timestamp, value)`` tuples observed during
            verification polling.  Useful for debugging glitchy behavior.
    """
    # What was sent
    action: str
    device_key: str
    sent_at: float = field(default_factory=time.time)
    server_accepted: bool = False

    # Verification state
    status: CommandStatus = CommandStatus.PENDING
    error: Optional[str] = None
    actual_value: Optional[Any] = None
    value_before: Optional[Any] = None
    history: List[tuple] = field(default_factory=list)

    # Internal: how to verify this command took effect.
    # These are set by Device._send_mode_data() and friends.
    _verify_register: Optional[str] = None
    _verify_value: Optional[Any] = None
    _verify_fn: Optional[Callable[[], Optional[DeviceState]]] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def verified(self) -> bool:
        """True if the device confirmed it applied our value."""
        return self.status == CommandStatus.VERIFIED

    @property
    def conflict(self) -> bool:
        """True if another user or process changed the same setting.

        When True, :attr:`actual_value` holds what the register was set
        to (by the other party), and :attr:`error` describes the conflict.
        """
        return self.status == CommandStatus.CONFLICT

    @property
    def failed(self) -> bool:
        """True if the command failed, timed out, or conflicted."""
        return self.status in (
            CommandStatus.FAILED, CommandStatus.ERROR, CommandStatus.CONFLICT)

    @property
    def pending(self) -> bool:
        """True if the command hasn't been resolved yet."""
        return self.status == CommandStatus.PENDING

    def check(self) -> bool:
        """Poll the device once to see if the command was applied.

        Fetches the current state from the server and compares the target
        register against the expected value.  Detects three outcomes:

        - **Match** → VERIFIED (returns True)
        - **Changed to a different value** → CONFLICT (returns False)
        - **Unchanged** → still PENDING (returns False)

        Does nothing if already resolved (verified/failed/conflict).

        Returns:
            True if the command is now verified, False otherwise.
        """
        if not self.pending:
            return self.verified

        if self._verify_fn is None or self._verify_register is None:
            return False

        with self._lock:
            if not self.pending:
                return self.verified
            try:
                state = self._verify_fn()
                if state is None:
                    return False

                actual = state.registers.get(self._verify_register)
                self.actual_value = actual
                self.history.append((time.time(), actual))

                if actual == self._verify_value:
                    # The register holds exactly what we sent.
                    self.status = CommandStatus.VERIFIED
                    logger.info(
                        "Command verified: %s=%s on %s",
                        self._verify_register, actual, self.device_key)
                    return True

                if (self.value_before is not None
                        and actual != self.value_before
                        and actual != self._verify_value):
                    # The register changed, but NOT to our value.
                    # Another user or process changed it concurrently.
                    self.status = CommandStatus.CONFLICT
                    self.error = (
                        f"Conflict: {self._verify_register} changed from "
                        f"{self.value_before} to {actual}, but we sent "
                        f"{self._verify_value}. Another user or the app "
                        f"may have changed this setting concurrently."
                    )
                    logger.warning(
                        "Command conflict on %s: expected %s=%s, "
                        "got %s (was %s before command)",
                        self.device_key, self._verify_register,
                        self._verify_value, actual, self.value_before)
                    return False

            except Exception as exc:
                self.status = CommandStatus.ERROR
                self.error = str(exc)
                logger.error(
                    "Error verifying command on %s: %s",
                    self.device_key, exc)
            return False

    def wait(self, timeout: float = 60, interval: float = 5) -> bool:
        """Block until the command is verified, conflicts, or times out.

        Polls the device every ``interval`` seconds.  Returns as soon as
        the outcome is determined -- either the value matches (VERIFIED),
        a conflict is detected (CONFLICT), or time runs out (FAILED).

        Args:
            timeout: Maximum seconds to wait.  The server delivers
                commands to devices within ~5--30 seconds when
                working normally, but can silently drop commands.
                A timeout of 30--60 seconds is recommended.
            interval: Seconds between verification polls.  The device
                checks in every ~5 seconds on average, so polling faster
                than that adds server load without benefit.

        Returns:
            True if the command was verified, False if it timed out,
            failed, or conflicted.
        """
        if not self.pending:
            return self.verified

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.check():
                return True
            if not self.pending:
                # Resolved to conflict or error -- stop waiting
                return False
            remaining = deadline - time.time()
            time.sleep(min(interval, max(0.1, remaining)))

        if self.pending:
            self.status = CommandStatus.FAILED
            self.error = (
                f"Verification timed out after {timeout:.0f}s. "
                f"The server accepted the command but the device did not "
                f"reflect the change. Last seen value: "
                f"{self._verify_register}={self.actual_value}"
            )
            logger.warning(
                "Command timed out on %s: %s=%s after %.0fs "
                "(expected %s, last saw %s)",
                self.device_key, self._verify_register, self._verify_value,
                timeout, self._verify_value, self.actual_value)
        return False
