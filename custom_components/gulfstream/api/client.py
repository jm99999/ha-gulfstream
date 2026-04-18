"""Low-level HTTP client for the Gulfstream pool heat pump cloud API.

Handles authentication, token lifecycle, and raw request/response handling
against the ICM Controls captouchwifi.com backend.
Most callers should use :class:`Device` instead of calling this directly.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from .constants import API_URL, DEFAULT_TIMEOUT, DEVICE_LIST_ADDITIONAL_FIELDS
from .exceptions import (
    AccessDeniedError,
    AuthenticationError,
    CommandError,
    DeviceNotFoundError,
    ServerError,
)
from .models import DeviceInfo, DeviceState, Mode

logger = logging.getLogger(__name__)


class GulfstreamClient:
    """Authenticated client for the Gulfstream pool heat pump cloud API.

    The client manages a single user session against ICM Controls'
    captouchwifi.com backend.  Login produces a token that is reused for
    all subsequent calls.  If a call fails with a token error the client
    will re-authenticate once automatically.

    Example::

        client = GulfstreamClient("myuser", "mypass")
        client.login()

        devices = client.list_devices()
        state = client.get_device_state(devices[0].unique_key)
        print(f"Water temp: {state.water_temp}°F")

    Args:
        username: Account username (same as the Compass WiFi app login).
        password: Account password.
        api_url: Override the API endpoint (for testing).
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        username: str,
        password: str,
        api_url: str = API_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self._username = username
        self._password = password
        self._api_url = api_url
        self._timeout = timeout
        self._token: Optional[str] = None
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json;charset=utf-8",
            "Accept": "application/json, text/plain, */*",
        })

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def login(self) -> str:
        """Authenticate and store the session token.

        Returns:
            The session token string.

        Raises:
            AuthenticationError: If credentials are invalid.
            ServerError: If the server returns an unexpected response.
        """
        data = self._post_raw({"action": "login",
                                "username": self._username,
                                "password": self._password})
        if data.get("result") != "success":
            raise AuthenticationError(
                data.get("message", "Login failed"))
        self._token = data["token"]
        logger.info("Logged in as %s", self._username)
        return self._token

    @property
    def token(self) -> Optional[str]:
        """Current session token, or None if not logged in."""
        return self._token

    def ensure_logged_in(self) -> None:
        """Login if there is no current token."""
        if self._token is None:
            self.login()

    # ------------------------------------------------------------------
    # Device listing
    # ------------------------------------------------------------------

    def list_devices(self) -> List[DeviceInfo]:
        """Get all devices associated with this account.

        Returns:
            List of :class:`DeviceInfo` objects.  Includes devices shared
            with this user (``owned=False``).
        """
        data = self._post({
            "action": "getPasDevices",
            "additionalFields": DEVICE_LIST_ADDITIONAL_FIELDS,
        })
        devices = []
        for d in data.get("devices", []):
            devices.append(DeviceInfo(
                id=d["id"],
                unique_key=d["unique_key"],
                name=d.get("name", ""),
                description=d.get("description", ""),
                model_name=d.get("model_name", ""),
                zipcode=d.get("zipcode", ""),
                owned=d.get("owned") == "1",
                owner=d.get("owner", ""),
                online=d.get("online") == "1",
            ))
        return devices

    # ------------------------------------------------------------------
    # Device state
    # ------------------------------------------------------------------

    def get_device_state(self, device_key: str) -> DeviceState:
        """Fetch the full register dump for a device.

        Args:
            device_key: The device's unique key (e.g. ``"0000CP165478"``).

        Returns:
            A :class:`DeviceState` with all registers and interpreted fields.

        Raises:
            DeviceNotFoundError: If the device is not accessible.
        """
        data = self._post({
            "action": "thermostatGetDetail",
            "thermostatKey": device_key,
            "fields": DEVICE_LIST_ADDITIONAL_FIELDS,
        })
        detail = data.get("detail", {})
        cs = detail.get("currentState", {})

        mode = Mode(cs.get("MD", 0))
        # The app displays whichever setpoint register matches the current mode:
        #   RSV1 = Pool setpoint (used by both Pool Heat and Pool Cool)
        #   RSV2 = Spa setpoint (separate memory)
        active_setpoint = cs.get("RSV2", 0) if mode == Mode.SPA else cs.get("RSV1", 0)
        return DeviceState(
            last_online=detail.get("last_online", ""),
            server_time=detail.get("server_time", ""),
            mode=mode,
            setpoint=active_setpoint,
            water_temp=cs.get("RMT", 0),
            locked=cs.get("HUNC", 0) != 0,
            max_heat=cs.get("MXH", 104),
            min_heat=cs.get("MNH", 50),
            registers=cs,
        )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def set_mode_data(self, device_key: str, mode_data: List[int]) -> dict:
        """Send a thermostatSetModeData command.

        This is the low-level mechanism for changing mode and setpoints.
        The ``mode_data`` array must contain exactly 6 integers that map
        to consecutive controller registers.

        See :meth:`Device.set_heat_setpoint` for a safer high-level API.

        Args:
            device_key: Device unique key.
            mode_data: 6-element integer list ``[mode, ?, setpoint, ?, ?, ?]``.

        Returns:
            Raw response dict from the server.
        """
        if len(mode_data) != 6:
            raise ValueError("mode_data must be exactly 6 integers")
        return self._post({
            "action": "thermostatSetModeData",
            "thermostatKey": device_key,
            "modeData": mode_data,
        })

    def set_fields(self, device_key: str, fields: Dict[str, Any]) -> dict:
        """Send a thermostatSetFields command.

        Writes individual register values.  Note: in testing, the server
        accepts this but changes may not propagate to the device reliably.

        Args:
            device_key: Device unique key.
            fields: Dict mapping register codes to values,
                e.g. ``{"DFG": 0, "CAL": 2}``.

        Returns:
            Raw response dict from the server.
        """
        return self._post({
            "action": "thermostatSetFields",
            "thermostatKey": device_key,
            "fields": fields,
        })

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def get_alert_method(self, device_key: str) -> dict:
        """Get the alert notification method settings for a device.

        Returns:
            Dict with ``email``, ``mobile``, ``text`` flags (0 or 1).
        """
        return self._post({
            "action": "thermostatGetAlertMethod",
            "thermostatKey": device_key,
        })

    # ------------------------------------------------------------------
    # Internal HTTP
    # ------------------------------------------------------------------

    def _post(self, payload: dict, retry_auth: bool = True) -> dict:
        """Authenticated POST -- injects the token automatically.

        On token errors, re-authenticates once and retries.
        """
        self.ensure_logged_in()
        payload["token"] = self._token
        data = self._post_raw(payload)

        # Handle token expiry
        if (data.get("result") == "failed"
                and "token" in data.get("message", "").lower()
                and retry_auth):
            logger.info("Token expired, re-authenticating")
            self.login()
            payload["token"] = self._token
            data = self._post_raw(payload)

        self._check_response(data)
        return data

    def _post_raw(self, payload: dict) -> dict:
        """Send a POST request and return the parsed JSON response.

        Raises ServerError on HTTP failures or invalid JSON.
        """
        try:
            resp = self._session.post(
                self._api_url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ServerError(f"HTTP request failed: {exc}") from exc

        if not resp.text.strip():
            raise ServerError("Server returned empty response")

        try:
            return resp.json()
        except ValueError as exc:
            raise ServerError(
                f"Server returned invalid JSON: {resp.text[:200]}") from exc

    def _check_response(self, data: dict) -> None:
        """Raise typed exceptions for known failure patterns."""
        if data.get("result") == "success":
            return

        msg = data.get("message", "")

        if "does not have access" in msg:
            raise DeviceNotFoundError(msg)
        if "does not own" in msg:
            raise AccessDeniedError(msg)
        if "does not exist" in msg:
            raise CommandError(msg)
        if "Invalid User Credentials" in msg:
            raise AuthenticationError(msg)
        if "unknown action" in msg:
            raise CommandError(msg)
        if data.get("result") == "failed":
            raise CommandError(msg)
