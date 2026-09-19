"""Settings resolution and the token cache."""

from __future__ import annotations

import json
import stat

from skylight_cli.auth import Credentials
from skylight_cli.config import Settings, TokenCache, load_dotenv


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


def test_settings_read_the_environment() -> None:
    settings = Settings.load({"SKYLIGHT_EMAIL": "me@example.com", "SKYLIGHT_FRAME_ID": "77"})
    assert settings.email == "me@example.com"
    assert settings.frame_id == "77"
    assert settings.password is None


def test_settings_treat_blank_values_as_unset() -> None:
    assert Settings.load({"SKYLIGHT_EMAIL": ""}).email is None


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
    mode = stat.S_IMODE(cache.path.stat().st_mode)
    assert mode == 0o600


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
