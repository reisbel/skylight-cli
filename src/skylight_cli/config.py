"""Settings, the optional ``.env`` file, and the on-disk token cache."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .auth import Credentials
from .errors import SkylightError

#: Secret references are resolved by shelling out to a password manager.
#: Each scheme maps the part after ``://`` onto the argv that prints the secret.
#: ``cmd://`` is the escape hatch for anything not listed: it runs the reference
#: itself, split with shell-like quoting but never handed to a shell.
SECRET_SCHEMES: dict[str, Callable[[str], list[str]]] = {
    "op://": lambda ref: ["op", "read", ref],
    "lp://": lambda ref: ["lpass", "show", "--password", ref.removeprefix("lp://")],
    "cmd://": lambda ref: shlex.split(ref.removeprefix("cmd://")),
}

#: Told to the user when the helper program is missing, keyed by scheme.
SECRET_TOOLS = {
    "op://": "the 1Password CLI (`op`)",
    "lp://": "the LastPass CLI (`lpass`)",
    "cmd://": "the command given",
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "skylight-cli"


def config_paths() -> list[Path]:
    """Where a ``.env`` may live, in increasing order of precedence.

    The per-user config file comes first so a globally installed ``skylight``
    works from any directory, and a project-local ``.env`` can still override it.
    """
    return [config_dir() / ".env", Path.cwd() / ".env"]


def load_dotenv(path: Path) -> dict[str, str]:
    """Read ``KEY=value`` pairs from one ``.env`` file, if it is there.

    Deliberately minimal: no interpolation, no export keyword, no multi-line
    values.
    """
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


def resolve_secret(value: str | None) -> str | None:
    """Resolve a secret reference through a password manager, else pass it through.

    This keeps the real secret out of the config file, the shell history and the
    process list. A value matching no known scheme is returned unchanged, so a
    literal password still works.
    """
    if not value:
        return value
    scheme = next((s for s in SECRET_SCHEMES if value.startswith(s)), None)
    if scheme is None:
        return value

    command = SECRET_SCHEMES[scheme](value)
    if not command:
        raise SkylightError(f"{value} does not name a command to run")
    tool = SECRET_TOOLS[scheme]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except FileNotFoundError as exc:
        raise SkylightError(
            f"{value} needs {tool}, but `{command[0]}` was not found on PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or f"`{command[0]}` exited with {exc.returncode}"
        raise SkylightError(f"Could not read {value}: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SkylightError(
            f"Timed out reading {value}. If the vault is locked, unlock it first."
        ) from exc

    secret = result.stdout.strip()
    if not secret:
        raise SkylightError(f"{value} resolved to an empty value")
    return secret


@dataclass
class Settings:
    """Everything the CLI needs to reach a specific Skylight household."""

    email: str | None = None
    password: str | None = None
    frame_id: str | None = None

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> Settings:
        source: dict[str, str] = {}
        for path in config_paths():
            source.update(load_dotenv(path))
        source.update(env if env is not None else os.environ)
        return cls(
            email=resolve_secret(source.get("SKYLIGHT_EMAIL") or None),
            password=resolve_secret(source.get("SKYLIGHT_PASSWORD") or None),
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
