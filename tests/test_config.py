"""Settings resolution, secret references and the token cache."""

from __future__ import annotations

import json
import stat
import subprocess

import pytest

from skylight_cli import config
from skylight_cli.auth import Credentials
from skylight_cli.config import Settings, TokenCache, load_dotenv, resolve_secret
from skylight_cli.errors import SkylightError


def test_load_dotenv_ignores_comments_and_blanks(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n# a comment\nSKYLIGHT_EMAIL=me@example.com\n"
        "SKYLIGHT_PASSWORD='quoted secret'\nnot a pair\n",
        encoding="utf-8",
    )
    assert load_dotenv(env_file) == {
        "SKYLIGHT_EMAIL": "me@example.com",
        "SKYLIGHT_PASSWORD": "quoted secret",
    }


def test_load_dotenv_tolerates_a_missing_file(tmp_path) -> None:
    assert load_dotenv(tmp_path / "nope.env") == {}


def test_config_paths_prefer_the_working_directory(isolated_environment) -> None:
    paths = config.config_paths()
    assert paths[0] == config.config_dir() / ".env"
    assert paths[1] == isolated_environment / ".env"


def test_settings_read_the_environment() -> None:
    settings = Settings.load({"SKYLIGHT_EMAIL": "me@example.com", "SKYLIGHT_FRAME_ID": "77"})
    assert settings.email == "me@example.com"
    assert settings.frame_id == "77"
    assert settings.password is None


def test_settings_fall_back_to_the_user_config_file(isolated_environment) -> None:
    user_config = config.config_dir()
    user_config.mkdir(parents=True)
    (user_config / ".env").write_text("SKYLIGHT_EMAIL=from-config@example.com\n")

    assert Settings.load({}).email == "from-config@example.com"


def test_a_local_dotenv_overrides_the_user_config(isolated_environment) -> None:
    user_config = config.config_dir()
    user_config.mkdir(parents=True)
    (user_config / ".env").write_text("SKYLIGHT_EMAIL=from-config@example.com\n")
    (isolated_environment / ".env").write_text("SKYLIGHT_EMAIL=from-cwd@example.com\n")

    assert Settings.load({}).email == "from-cwd@example.com"


def test_the_real_environment_wins_over_every_file(isolated_environment) -> None:
    (isolated_environment / ".env").write_text("SKYLIGHT_EMAIL=from-cwd@example.com\n")
    assert Settings.load({"SKYLIGHT_EMAIL": "from-env@example.com"}).email == "from-env@example.com"


def test_settings_treat_blank_values_as_unset() -> None:
    assert Settings.load({"SKYLIGHT_EMAIL": ""}).email is None


def test_resolve_secret_passes_plain_values_through() -> None:
    assert resolve_secret("hunter2") == "hunter2"
    assert resolve_secret(None) is None
    assert resolve_secret("") is None or resolve_secret("") == ""


def test_resolve_secret_shells_out_for_op_references(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="resolved-secret\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_secret("op://Personal/Skylight/password") == "resolved-secret"
    assert calls == [["op", "read", "op://Personal/Skylight/password"]]


def test_resolve_secret_explains_a_missing_op_cli(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise FileNotFoundError("op")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SkylightError, match="not found on PATH"):
        resolve_secret("op://Personal/Skylight/password")


def test_resolve_secret_surfaces_an_op_failure(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="item not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SkylightError, match="item not found"):
        resolve_secret("op://Personal/Nope/password")


def test_resolve_secret_reports_a_locked_vault(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 60)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SkylightError, match="unlock it first"):
        resolve_secret("op://Personal/Skylight/password")


def test_token_cache_round_trips(tmp_path) -> None:
    cache = TokenCache(tmp_path / "token.json")
    assert cache.load() is None

    cache.save(Credentials("tok-123", refresh_token="ref-456", expires_at=8200.0))
    restored = cache.load()

    assert restored is not None
    assert restored.access_token == "tok-123"
    assert restored.refresh_token == "ref-456"
    assert restored.expires_at == 8200.0


def test_token_cache_is_owner_readable_only(tmp_path) -> None:
    cache = TokenCache(tmp_path / "nested" / "token.json")
    cache.save(Credentials("tok-123"))
    assert stat.S_IMODE(cache.path.stat().st_mode) == 0o600


def test_token_cache_ignores_corrupt_contents(tmp_path) -> None:
    cache = TokenCache(tmp_path / "token.json")
    cache.path.write_text("{ not json", encoding="utf-8")
    assert cache.load() is None

    cache.path.write_text(json.dumps({"refresh_token": "orphan"}), encoding="utf-8")
    assert cache.load() is None


def test_token_cache_clear_is_idempotent(tmp_path) -> None:
    cache = TokenCache(tmp_path / "token.json")
    cache.clear()
    cache.save(Credentials("tok"))
    cache.clear()
    assert not cache.path.exists()
