"""Exception types raised by this package."""

from __future__ import annotations


class SkylightError(Exception):
    """Base class for every error this package raises."""


class SkylightAuthError(SkylightError):
    """Login failed, or the cached session is no longer valid."""


class SkylightNotFoundError(SkylightError):
    """The requested frame, list, item or chore does not exist."""


class SkylightAPIError(SkylightError):
    """The API returned an unexpected error response."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
