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
    assert args.date is None


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
