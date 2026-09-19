"""Command line interface.

Every command prints a short human-readable table by default and machine-readable
JSON with ``--json``, which is what makes this usable from scripts and agents.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Sequence
from typing import Any

from . import __version__, auth
from .client import SkylightClient
from .config import Settings, TokenCache, config_dir
from .errors import SkylightAuthError, SkylightError

TRUNCATED = 60


# -- session ---------------------------------------------------------------


def missing_credentials_help(variable: str) -> str:
    """Explain how to supply credentials when there is no terminal to prompt on."""
    return (
        f"{variable} is not set, and there is no terminal here to prompt on "
        "(this happens when skylight runs from a script, an agent or an editor).\n"
        f"Put your credentials in {config_dir() / '.env'}:\n"
        "    SKYLIGHT_EMAIL=you@example.com\n"
        "    SKYLIGHT_PASSWORD=your-password\n"
        "Either value may be an op:// reference instead, resolved through the "
        "1Password CLI so the secret never touches disk:\n"
        "    SKYLIGHT_PASSWORD=op://Personal/Skylight/password\n"
        "Or run `skylight login` once in a real terminal to cache a token."
    )


def open_session(cache: TokenCache, settings: Settings, *, interactive: bool) -> SkylightClient:
    """Return a client, reusing the cached token and refreshing or logging in as needed."""
    credentials = cache.load()

    if credentials and credentials.is_expired():
        if credentials.refresh_token:
            try:
                credentials = auth.refresh(credentials.refresh_token)
                cache.save(credentials)
            except SkylightAuthError:
                credentials = None
        else:
            credentials = None

    if credentials is None:
        credentials = do_login(cache, settings, interactive=interactive)

    return SkylightClient(credentials)


def do_login(cache: TokenCache, settings: Settings, *, interactive: bool) -> Any:
    """Log in from the configured credentials, prompting only when attached to a terminal."""
    email = settings.email
    password = settings.password

    if not email:
        if not interactive:
            raise SkylightError(missing_credentials_help("SKYLIGHT_EMAIL"))
        email = input("Skylight email: ").strip()
    if not password:
        if not interactive:
            raise SkylightError(missing_credentials_help("SKYLIGHT_PASSWORD"))
        password = getpass.getpass(f"Password for {email}: ")

    credentials = auth.login(email, password)
    cache.save(credentials)
    return credentials


# -- output ----------------------------------------------------------------


def emit(rows: Any, columns: Sequence[tuple[str, str]], *, as_json: bool, empty: str) -> None:
    """Print ``rows`` as JSON or as an aligned table of the given columns."""
    if as_json:
        print(json.dumps(rows, indent=2, sort_keys=True, default=str))
        return
    if not rows:
        print(empty)
        return
    if isinstance(rows, dict):
        rows = [rows]

    headers = [title for title, _ in columns]
    table = [[_cell(row.get(key)) for _, key in columns] for row in rows]
    widths = [max(len(headers[i]), *(len(line[i]) for line in table)) for i in range(len(headers))]
    print("  ".join(h.upper().ljust(widths[i]) for i, h in enumerate(headers)).rstrip())
    for line in table:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(line)).rstrip())


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value)
    return text if len(text) <= TRUNCATED else text[: TRUNCATED - 1] + "…"


# -- commands --------------------------------------------------------------


def cmd_login(args: argparse.Namespace, cache: TokenCache, settings: Settings) -> int:
    do_login(cache, settings, interactive=sys.stdin.isatty())
    print(f"Logged in. Token cached at {cache.path}")
    return 0


def cmd_logout(args: argparse.Namespace, cache: TokenCache, settings: Settings) -> int:
    cache.clear()
    print("Cached token removed.")
    return 0


def cmd_frames(args: argparse.Namespace, client: SkylightClient) -> int:
    emit(
        client.frames(),
        [("id", "id"), ("name", "name"), ("timezone", "timezone")],
        as_json=args.json,
        empty="No frames on this account.",
    )
    return 0


def cmd_members(args: argparse.Namespace, client: SkylightClient) -> int:
    frame_id = client.resolve_frame_id(args.frame)
    emit(
        client.members(frame_id),
        [("id", "id"), ("name", "label"), ("color", "color")],
        as_json=args.json,
        empty="No family members on this frame.",
    )
    return 0


def cmd_chores_list(args: argparse.Namespace, client: SkylightClient) -> int:
    frame_id = client.resolve_frame_id(args.frame)
    emit(
        client.chores(frame_id, date=args.date),
        [
            ("id", "id"),
            ("chore", "summary"),
            ("who", "category_id"),
            ("start", "start"),
            ("status", "status"),
            ("points", "reward_points"),
        ],
        as_json=args.json,
        empty="No chores.",
    )
    return 0


def cmd_chores_add(args: argparse.Namespace, client: SkylightClient) -> int:
    frame_id = client.resolve_frame_id(args.frame)
    category_id = client.resolve_member_id(frame_id, args.who) if args.who else None
    created = client.add_chore(
        frame_id,
        args.summary,
        start=args.on,
        start_time=args.at,
        category_id=category_id,
        reward_points=args.points,
        recurrence_set=args.repeat,
        recurring_until=args.until,
        emoji_icon=args.icon,
    )
    if args.json:
        print(json.dumps(created, indent=2, sort_keys=True, default=str))
    else:
        print(f"Added chore {created.get('id', '?')}: {args.summary}")
    return 0


def cmd_chores_done(args: argparse.Namespace, client: SkylightClient) -> int:
    frame_id = client.resolve_frame_id(args.frame)
    category_id = client.resolve_member_id(frame_id, args.who) if args.who else None
    client.complete_chore(frame_id, args.chore_id, instance_date=args.on, category_id=category_id)
    print(f"Marked chore {args.chore_id} complete.")
    return 0


def cmd_chores_rm(args: argparse.Namespace, client: SkylightClient) -> int:
    frame_id = client.resolve_frame_id(args.frame)
    client.delete_chore(frame_id, args.chore_id, apply_to="all" if args.all else None)
    print(f"Deleted chore {args.chore_id}.")
    return 0


def cmd_lists(args: argparse.Namespace, client: SkylightClient) -> int:
    frame_id = client.resolve_frame_id(args.frame)
    emit(
        client.lists(frame_id),
        [("id", "id"), ("list", "label"), ("kind", "kind"), ("color", "color")],
        as_json=args.json,
        empty="No lists on this frame.",
    )
    return 0


def cmd_lists_show(args: argparse.Namespace, client: SkylightClient) -> int:
    frame_id = client.resolve_frame_id(args.frame)
    list_id = client.resolve_list_id(frame_id, args.list)
    emit(
        client.list_items(frame_id, list_id),
        [("id", "id"), ("item", "label"), ("status", "status"), ("section", "section")],
        as_json=args.json,
        empty="That list is empty.",
    )
    return 0


def cmd_lists_add(args: argparse.Namespace, client: SkylightClient) -> int:
    frame_id = client.resolve_frame_id(args.frame)
    list_id = client.resolve_list_id(frame_id, args.list)
    created = [
        client.add_list_item(frame_id, list_id, item, section=args.section) for item in args.items
    ]
    if args.json:
        print(json.dumps(created, indent=2, sort_keys=True, default=str))
    else:
        for item, record in zip(args.items, created, strict=True):
            print(f"Added to {args.list}: {item} (id {record.get('id', '?')})")
    return 0


def cmd_lists_done(args: argparse.Namespace, client: SkylightClient) -> int:
    frame_id = client.resolve_frame_id(args.frame)
    list_id = client.resolve_list_id(frame_id, args.list)
    client.complete_list_item(frame_id, list_id, args.item_id, done=not args.undo)
    print(f"{'Unchecked' if args.undo else 'Checked'} item {args.item_id}.")
    return 0


def cmd_lists_rm(args: argparse.Namespace, client: SkylightClient) -> int:
    frame_id = client.resolve_frame_id(args.frame)
    list_id = client.resolve_list_id(frame_id, args.list)
    client.delete_list_item(frame_id, list_id, args.item_id)
    print(f"Removed item {args.item_id}.")
    return 0


# -- argument parsing ------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skylight",
        description="Unofficial CLI for Skylight Calendar chores and lists.",
    )
    parser.add_argument("--version", action="version", version=f"skylight-cli {__version__}")
    parser.add_argument("--frame", help="Frame id, when the account has more than one")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of a table")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Log in and cache a token").set_defaults(func=cmd_login)
    sub.add_parser("logout", help="Forget the cached token").set_defaults(func=cmd_logout)
    sub.add_parser("frames", help="List households on this account").set_defaults(func=cmd_frames)
    sub.add_parser("members", help="List family members").set_defaults(func=cmd_members)

    chores = sub.add_parser("chores", help="Work with chores")
    chores.set_defaults(func=cmd_chores_list, date=None)
    chores_sub = chores.add_subparsers(dest="chores_command")

    show = chores_sub.add_parser("list", help="Show chores")
    show.add_argument("--date", help="Only chores on this date (YYYY-MM-DD)")
    show.set_defaults(func=cmd_chores_list)

    add = chores_sub.add_parser("add", help="Create a chore")
    add.add_argument("summary", help="What the chore is")
    add.add_argument("--who", help="Family member to assign it to, by name")
    add.add_argument("--on", help="Start date (YYYY-MM-DD)")
    add.add_argument("--at", help="Start time (HH:MM)")
    add.add_argument("--points", type=int, help="Reward points")
    add.add_argument(
        "--repeat",
        nargs="+",
        metavar="DAY",
        help="Repeat on these weekdays, e.g. --repeat monday wednesday",
    )
    add.add_argument("--until", help="Stop repeating after this date (YYYY-MM-DD)")
    add.add_argument("--icon", help="Emoji shown on the frame")
    add.set_defaults(func=cmd_chores_add)

    done = chores_sub.add_parser("done", help="Mark a chore complete")
    done.add_argument("chore_id")
    done.add_argument("--on", help="Which instance, for a repeating chore (YYYY-MM-DD)")
    done.add_argument("--who", help="Family member completing it, by name")
    done.set_defaults(func=cmd_chores_done)

    remove = chores_sub.add_parser("rm", help="Delete a chore")
    remove.add_argument("chore_id")
    remove.add_argument("--all", action="store_true", help="Delete every occurrence")
    remove.set_defaults(func=cmd_chores_rm)

    lists = sub.add_parser("lists", help="Work with shopping and to-do lists")
    lists.set_defaults(func=cmd_lists)
    lists_sub = lists.add_subparsers(dest="lists_command")

    lists_show = lists_sub.add_parser("show", help="Show the items on a list")
    lists_show.add_argument("list", help="List name or id")
    lists_show.set_defaults(func=cmd_lists_show)

    lists_add = lists_sub.add_parser("add", help="Add items to a list")
    lists_add.add_argument("list", help="List name or id")
    lists_add.add_argument("items", nargs="+", help="One or more items")
    lists_add.add_argument("--section", help="Section to file the items under")
    lists_add.set_defaults(func=cmd_lists_add)

    lists_done = lists_sub.add_parser("done", help="Check an item off")
    lists_done.add_argument("list", help="List name or id")
    lists_done.add_argument("item_id")
    lists_done.add_argument("--undo", action="store_true", help="Uncheck it instead")
    lists_done.set_defaults(func=cmd_lists_done)

    lists_rm = lists_sub.add_parser("rm", help="Delete an item")
    lists_rm.add_argument("list", help="List name or id")
    lists_rm.add_argument("item_id")
    lists_rm.set_defaults(func=cmd_lists_rm)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cache = TokenCache()
    settings = Settings.load()

    try:
        if args.func in (cmd_login, cmd_logout):
            return args.func(args, cache, settings)
        with open_session(cache, settings, interactive=sys.stdin.isatty()) as client:
            return args.func(args, client)
    except SkylightError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
