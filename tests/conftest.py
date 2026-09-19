"""Keep the suite away from the real user's config, cache and working directory."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Point every XDG location at a scratch directory and run from one too.

    Without this, ``Settings.load`` would happily pick up the developer's own
    ``~/.config/skylight-cli/.env`` and the results would depend on the machine.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for variable in ("SKYLIGHT_EMAIL", "SKYLIGHT_PASSWORD", "SKYLIGHT_FRAME_ID"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path
