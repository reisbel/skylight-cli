"""Argument wiring and output formatting."""

from __future__ import annotations

import json

import pytest

from skylight_cli import cli


def parse(argv: list[str]):
    return cli.build_parser().parse_args(argv)


def test_bare_chores_defaults_to_listing() -> None:
    args = parse(["chores"])
    assert args.func is cli.cmd_chores_list
    assert args.after is None
    assert args.before is None
    assert args.include_late is False


def test_the_date_window_works_with_and_without_the_subcommand() -> None:
    # The options live on a parent parser precisely so both spellings parse.
    bare = parse(["chores", "--after", "2026-09-14", "--before", "2026-09-21"])
    explicit = parse(["chores", "list", "--after", "2026-09-14", "--before", "2026-09-21"])
    for args in (bare, explicit):
        assert args.func is cli.cmd_chores_list
        assert args.after == "2026-09-14"
        assert args.before == "2026-09-21"


def test_chores_add_collects_every_option() -> None:
    args = parse(
        [
            "chores",
            "add",
            "Take out trash",
            "--who",
            "Lucas",
            "--on",
            "2026-09-20",
            "--at",
            "18:00",
            "--points",
            "5",
            "--repeat",
            "monday",
            "thursday",
            "--icon",
            "🗑",
        ]
    )
    assert args.func is cli.cmd_chores_add
    assert args.summary == "Take out trash"
    assert args.who == "Lucas"
    assert args.points == 5
    assert args.repeat == ["monday", "thursday"]


def test_chores_add_refuses_to_run_without_an_assignee() -> None:
    # Skylight rejects a chore with no category, so catch it before the request.
    with pytest.raises(SystemExit):
        parse(["chores", "add", "Take out trash"])


def test_chores_done_can_undo() -> None:
    args = parse(["chores", "done", "107378250-2026-09-19-0600", "--undo"])
    assert args.func is cli.cmd_chores_done
    assert args.chore_id == "107378250-2026-09-19-0600"
    assert args.undo is True


def test_lists_add_takes_several_items() -> None:
    args = parse(["lists", "add", "Groceries", "Milk", "Eggs"])
    assert args.func is cli.cmd_lists_add
    assert args.list == "Groceries"
    assert args.items == ["Milk", "Eggs"]


def test_global_flags_apply_before_the_subcommand() -> None:
    args = parse(["--json", "--frame", "77", "lists"])
    assert args.json is True
    assert args.frame == "77"


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit):
        parse([])


def test_emit_writes_json_when_asked(capsys) -> None:
    cli.emit([{"id": "1", "label": "Milk"}], [("id", "id")], as_json=True, empty="none")
    assert json.loads(capsys.readouterr().out) == [{"id": "1", "label": "Milk"}]


def test_emit_renders_an_aligned_table(capsys) -> None:
    rows = [{"id": "1", "label": "Milk"}, {"id": "100", "label": "Eggs"}]
    cli.emit(rows, [("id", "id"), ("item", "label")], as_json=False, empty="none")
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["ID   ITEM", "1    Milk", "100  Eggs"]


def test_emit_shows_a_dash_for_missing_values(capsys) -> None:
    cli.emit([{"id": "1"}], [("id", "id"), ("who", "category_id")], as_json=False, empty="none")
    assert capsys.readouterr().out.splitlines()[1] == "1   -"


def test_emit_reports_emptiness_in_words(capsys) -> None:
    cli.emit([], [("id", "id")], as_json=False, empty="That list is empty.")
    assert capsys.readouterr().out.strip() == "That list is empty."


def test_emit_accepts_a_single_record(capsys) -> None:
    cli.emit({"id": "1", "label": "Milk"}, [("item", "label")], as_json=False, empty="none")
    assert capsys.readouterr().out.splitlines() == ["ITEM", "Milk"]


def test_cell_truncates_long_values() -> None:
    assert cli._cell("x" * 80).endswith("…")
    assert len(cli._cell("x" * 80)) == cli.TRUNCATED
    assert cli._cell(True) == "yes"
    assert cli._cell(None) == "-"


def test_missing_credentials_help_is_actionable() -> None:
    message = cli.missing_credentials_help("SKYLIGHT_PASSWORD")
    assert "SKYLIGHT_PASSWORD is not set" in message
    assert "skylight-cli/.env" in message
    assert "op://" in message
    assert "lp://" in message
    assert "cmd://" in message
    assert "real terminal" in message


def test_login_without_credentials_fails_cleanly(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["login"]) == 1
    assert "op://" in capsys.readouterr().err


def test_a_cached_token_survives_a_locked_password_manager(monkeypatch, tmp_path) -> None:
    """The regression that motivated lazy resolution: a valid token, a locked vault."""
    import subprocess

    from skylight_cli.auth import Credentials
    from skylight_cli.config import TokenCache, config_dir

    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / ".env").write_text("SKYLIGHT_EMAIL=me@example.com\nSKYLIGHT_PASSWORD=lp://X\n")

    cache = TokenCache()
    cache.save(Credentials("tok-123", expires_at=10**12))

    def explode(*args, **kwargs):
        raise AssertionError("must not consult the password manager with a valid token")

    monkeypatch.setattr(subprocess, "run", explode)

    captured = {}

    def fake_frames(self):
        captured["called"] = True
        return [{"id": "77", "name": "Kitchen", "timezone": "America/New_York"}]

    monkeypatch.setattr("skylight_cli.client.SkylightClient.frames", fake_frames)
    assert cli.main(["frames"]) == 0
    assert captured["called"] is True


def test_a_failing_password_manager_is_reported_cleanly(capsys, monkeypatch) -> None:
    """It must print `error: ...`, not dump a traceback."""
    import subprocess

    from skylight_cli.config import config_dir

    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / ".env").write_text("SKYLIGHT_EMAIL=me@example.com\nSKYLIGHT_PASSWORD=lp://X\n")

    def fake_run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="Could not find decryption key.")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cli.main(["login"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert "decryption key" in err
