"""gulfstream -- Python client for the Gulfstream pool heat pump cloud API.

Reverse-engineered from the Compass WiFi Heat Pump Navigator Android app
(com.icmcontrols.gulfstream) and confirmed against the live API at
captouchwifi.com (ICM Controls' cloud backend).

Quick start::

    from gulfstream import GulfstreamClient, Device, Mode

    # Connect
    client = GulfstreamClient("username", "password")
    client.login()

    # List devices
    devices = client.list_devices()
    for d in devices:
        print(f"{d.name} ({'online' if d.online else 'offline'})")

    # Control a device
    dev = Device(client, devices[0].unique_key)
    state = dev.refresh()
    print(f"Water: {state.water_temp}°F  Setpoint: {state.setpoint}°F  Mode: {state.mode_text}")

    # Change setpoint (fire-and-forget)
    result = dev.set_heat_setpoint(88)

    # Change setpoint (wait for device to confirm)
    result = dev.set_heat_setpoint(88, verify=True, timeout=30)
    if result.verified:
        print("Applied!")
    elif result.conflict:
        print(f"Another user changed it to {result.actual_value}")
    else:
        print(f"Failed: {result.error}")

    # Change setpoint (check later)
    result = dev.set_heat_setpoint(88)
    # ... do other work ...
    if result.wait(timeout=30):
        print("Applied!")
"""

from .client import GulfstreamClient
from .constants import Mode, DefrostMode
from .device import Device
from .exceptions import (
    AccessDeniedError,
    AuthenticationError,
    CommandError,
    DeviceNotFoundError,
    GulfstreamError,
    ServerError,
    VerificationTimeout,
)
from .models import CommandResult, CommandStatus, DeviceInfo, DeviceState

__version__ = "0.1.0"

__all__ = [
    "GulfstreamClient",
    "Device",
    "Mode",
    "DefrostMode",
    "DeviceInfo",
    "DeviceState",
    "CommandResult",
    "CommandStatus",
    "GulfstreamError",
    "AuthenticationError",
    "DeviceNotFoundError",
    "AccessDeniedError",
    "CommandError",
    "ServerError",
    "VerificationTimeout",
]
