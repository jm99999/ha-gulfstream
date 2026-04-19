# Copyright © 2026 J.L.Mann. Personal use only. See LICENSE for terms.
"""Exceptions for the Gulfstream pool heat pump API client."""


class GulfstreamError(Exception):
    """Base exception for all Gulfstream API errors."""


class AuthenticationError(GulfstreamError):
    """Login failed -- invalid credentials or expired token."""


class DeviceNotFoundError(GulfstreamError):
    """Device not in the user's account or does not exist."""


class AccessDeniedError(GulfstreamError):
    """User does not have access to the requested resource (e.g. shared user
    trying to modify alerts on a device they don't own)."""


class CommandError(GulfstreamError):
    """The server rejected a command (e.g. invalid field name)."""


class ServerError(GulfstreamError):
    """The server returned an unexpected response (empty body, HTTP error, etc.)."""


class VerificationTimeout(GulfstreamError):
    """A command was sent but the device did not reflect the change within
    the allowed timeout period."""
