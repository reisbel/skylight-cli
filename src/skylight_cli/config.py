"""Settings, the optional ``.env`` file, and the on-disk token cache."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .auth import Credentials


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Read ``KEY=value`` pairs from a ``.env`` file, if one is there.

    Deliberately minimal: no interpolation, no export keyword, no multi-line
    values. Anything already set in the real environment wins, so a shell export
    always overrides the file.
    """
    path = path or Path.cwd() / ".env"
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


@dataclass
class Settings:
    """Everything the CLI needs to reach a specific Skylight household."""

    email: str | None = None
    password: str | None = None
    frame_id: str | None = None

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = dict(load_dotenv())
        source.update(env if env is not None else os.environ)
        return cls(
            email=source.get("SKYLIGHT_EMAIL") or None,
            password=source.get("SKYLIGHT_PASSWORD") or None,
            frame_id=source.get("SKYLIGHT_FRAME_ID") or None,
        )


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "skylight-cli" / "token.json"


class TokenCache:
    """Stores the bearer token between invocations, readable only by its owner."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_cache_path()

    def load(self) -> Credentials | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        access_token = data.get("access_token")
        if not access_token:
            return None
        return Credentials(
            access_token=str(access_token),
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
        )

    def save(self, credentials: Credentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Create the file with owner-only permissions before anything is written
        # to it, so the token is never briefly world-readable.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "access_token": credentials.access_token,
                    "refresh_token": credentials.refresh_token,
                    "expires_at": credentials.expires_at,
                },
                handle,
            )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
