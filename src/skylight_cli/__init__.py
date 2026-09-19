"""Unofficial client for Skylight Calendar chores and lists."""

from .client import SkylightClient
from .errors import (
    SkylightAPIError,
    SkylightAuthError,
    SkylightError,
    SkylightNotFoundError,
)

__version__ = "0.2.0"

__all__ = [
    "SkylightClient",
    "SkylightAPIError",
    "SkylightAuthError",
    "SkylightError",
    "SkylightNotFoundError",
    "__version__",
]
