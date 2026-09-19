# skylight-cli

A small command line tool for adding chores and list items to a [Skylight Calendar](https://myskylight.com/calendar) from a terminal or a script.

> **Unofficial.**
> Skylight publishes no public API.
> This talks to the same private endpoints at `app.ourskylight.com` that the Skylight apps use, mapped by the community.
> It is not affiliated with, endorsed by, or supported by Skylight, and it can break whenever they ship an update.
> Use it against your own account only.

## Why this exists

The Skylight Calendar syncs calendar events from Google, Apple and Outlook, and it accepts photos by email.
Chores and lists have no such door.
They can only be edited by hand in the Skylight app, which makes them invisible to every other tool you use.

This closes that gap for the two features that matter most day to day, and deliberately stops there.

## Scope

| Supported | Not supported |
| --- | --- |
| Chores: list, add, complete, delete | Meals and recipes |
| Lists: show, add items, check off, delete | Rewards and reward points |
| Family members and frames (read-only) | Messages, the task box, photos |

The unsupported features sit behind a Skylight Plus subscription.
They are left out rather than shipped untested, since there was no way to exercise them.

Photos do not need this tool.
Emailing them as attachments to your frame's `@ourskylight.com` address is an official, supported feature and it works in under a minute.

## Install

Requires Python 3.11 or newer.

```bash
uv tool install git+https://github.com/reisbel/skylight-cli
```

Or, for a checkout you intend to edit:

```bash
git clone https://github.com/reisbel/skylight-cli
cd skylight-cli
uv venv && uv pip install -e ".[dev]"
```

## Log in

```bash
skylight login
```

In a real terminal that prompts for your Skylight email and password.
They are used once, to complete the same OAuth2 login the Skylight app performs, and are never written to disk.
The resulting bearer token is cached at `~/.cache/skylight-cli/token.json` with mode `0600` and refreshed automatically as it expires.

### Without a terminal

Scripts, editors and coding agents often have no TTY at all, so there is nothing to prompt on.
Put the credentials in `~/.config/skylight-cli/.env` instead:

```ini
SKYLIGHT_EMAIL=you@example.com
SKYLIGHT_PASSWORD=your-password
```

Better, keep the password in a vault and reference it.
Any value may be a secret reference, resolved at run time by shelling out to a password manager, so the secret is never written to disk or left in your shell history:

| Reference | Resolved with |
| --- | --- |
| `op://Personal/Skylight/password` | `op read op://Personal/Skylight/password` |
| `lp://Skylight Calendar` | `lpass show --password 'Skylight Calendar'` |
| `cmd://security find-generic-password -s Skylight -w` | the command itself |

```ini
SKYLIGHT_EMAIL=you@example.com
SKYLIGHT_PASSWORD=lp://Skylight Calendar
```

`cmd://` is the escape hatch for anything not listed, including the macOS Keychain, `pass` and Bitwarden.
It is split with shell-like quoting but never handed to a shell, so there is no shell expansion to get wrong.
A value matching no scheme is used as the literal password.

Both `op` and `lpass` need an unlocked session of their own.
`lpass login you@example.com` wants a terminal, so run it once by hand; the `lpass` agent keeps the session alive for later non-interactive calls.

Config is read from `~/.config/skylight-cli/.env` first, then a `.env` in the working directory, then real environment variables, each overriding the last.
The per-user file is the one to use for a globally installed `skylight`, since it does not depend on where you run the command from.
A project-local `.env` is gitignored here.

`skylight logout` deletes the cached token.

## Use

```bash
# What am I working with?
skylight frames                 # households on the account
skylight members                # family members and their colors
skylight lists                  # every shopping and to-do list

# Shopping list
skylight lists show Groceries
skylight lists add Groceries "Milk" "Eggs" "Coffee"
skylight lists done Groceries 5138
skylight lists rm Groceries 5138

# Chores
skylight chores
skylight chores --date 2026-09-20
skylight chores add "Take out the trash" --who Lucas --on 2026-09-20 --at 18:00 --points 5
skylight chores add "Make the bed" --who Camila --repeat monday tuesday wednesday thursday friday
skylight chores done 4471 --on 2026-09-20
skylight chores rm 4471 --all
```

Every command prints an aligned table by default and raw JSON with `--json`, so it composes with `jq` and is easy to drive from a script or an agent.

```bash
skylight --json lists show Groceries | jq -r '.[] | select(.status != "completed") | .label'
```

If the account has more than one frame, pass `--frame <id>` or set `SKYLIGHT_FRAME_ID`.
With a single frame it is detected automatically.

## What can break

The endpoints, the OAuth client id and the login form are all undocumented.
A Skylight release can change any of them without warning.
The failure modes are legible on purpose: a changed login page reports that the CSRF token could not be found, an expired session asks you to log in again, and a `403` says the feature likely needs Skylight Plus.

## Credits

The reverse-engineering is the community's work, not mine.
This client is a narrow, independent implementation built on what those projects documented:

- [TheEagleByte/skylight-api](https://github.com/TheEagleByte/skylight-api) for the OpenAPI description of the endpoint surface.
- [joshuaswarren/pyskylight](https://github.com/joshuaswarren/pyskylight) for working out the OAuth2 PKCE login flow after the old session endpoint was retired.
- [MegaTheLEGEND/skylight_calendar](https://github.com/MegaTheLEGEND/skylight_calendar) for the Home Assistant integration that covers the same ground.

If you want full coverage of the API rather than chores and lists, use `pyskylight`.

## Development

```bash
uv run pytest
uv run ruff check .
```

Tests run entirely against a mocked transport.
Nothing in the suite touches the network or a real Skylight account, and the suite points every XDG path at a scratch directory so it cannot read your own config.

When reinstalling from a local checkout, pass `--reinstall`.
Without it `uv` reuses the cached wheel for an unchanged version number and your edits are silently ignored:

```bash
uv tool install --force --reinstall .
```

## License

MIT
