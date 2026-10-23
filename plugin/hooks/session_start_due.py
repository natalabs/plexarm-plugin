#!/usr/bin/env python3
"""Plexarm — the SessionStart due notice.

Fires when a new session starts (`source == "startup"`). Asks the Plexarm API
once what is due for this person — routine occurrences and one-off reminders
nobody has satisfied yet — and, when there is anything, adds one short notice
to the session's context naming each one. When nothing is due it prints
nothing.

The board and `plexarm_next` already raise what is due to an agent that asks;
this hook makes a plugin session start out knowing. It supplements the board
and is never the only way to see what is due.

════════════════════════════════════════════════════════════════════════════════
WHAT LEAVES THE MACHINE — exactly one request per new session
════════════════════════════════════════════════════════════════════════════════
    GET https://api.plexarm.com/records/board?limit=1
    Authorization: Bearer <your token>

No body, nothing read from the repository, and no session id. Only the
answer's `due` array is read; `limit=1` keeps the rest of the board page as
small as possible and does not narrow `due`.

The notice is not scoped to a project: it lists every due entry this person
can see, across their projects, and never anyone else's.

════════════════════════════════════════════════════════════════════════════════
RULES, shared with the other hooks in this directory
════════════════════════════════════════════════════════════════════════════════
  * THE HOST IS COMPILED IN. Nothing in the repository or the environment can
    redirect where the token goes.
  * IT CANNOT BLOCK AND FAILS OPEN ON EVERYTHING: no credential, a timeout, a
    non-200, a malformed answer — each prints nothing and exits 0.
  * NO TOKEN IN ANY OUTPUT. The token goes into one header and nowhere else.
  * TEXT FROM THE ANSWER IS DATA. Titles are clamped, stripped of control
    characters and quoted; ids are checked against their readable-id shape.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import sys


# ─────────────────────────────────────────────────────────────────────────────
# THE CREDENTIAL, through the shared resolver in `plexarm_credential.py`.
#
# The import is wrapped and the same lookup order repeated below it, so that a
# broken install never turns into a failing hook. Keep this copy reading in the
# same order as the module.
# ─────────────────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from plexarm_credential import (  # noqa: E402
        STORE_SERVICE,
        TOKEN_ENV,
        resolve_token,
        resolve_token_with_source,
        store_token,
    )
except BaseException:  # noqa: BLE001 - a hook must not fail on its own imports
    import subprocess as _subprocess

    TOKEN_ENV = "PLEXARM_TOKEN"
    STORE_SERVICE = "plexarm-api-token"
    STORE_FILE = ".config/plexarm/token"
    STORE_TIMEOUT_SECONDS = 3.0

    def store_token():  # type: ignore[misc]
        command = None
        if os.path.exists("/usr/bin/security"):
            command = ["/usr/bin/security", "find-generic-password", "-s", STORE_SERVICE, "-w"]
        elif os.path.exists("/usr/bin/secret-tool"):
            command = ["/usr/bin/secret-tool", "lookup", "service", STORE_SERVICE]
        if command is not None:
            try:
                completed = _subprocess.run(  # noqa: S603 - absolute path, no shell
                    command,
                    capture_output=True,
                    text=True,
                    timeout=STORE_TIMEOUT_SECONDS,
                    env={"PATH": "/usr/bin:/bin"},
                    stdin=_subprocess.DEVNULL,
                    check=False,
                )
            except (OSError, _subprocess.SubprocessError):
                completed = None
            if completed is not None and completed.returncode == 0 and completed.stdout.strip():
                return completed.stdout.strip()
        try:
            import pwd

            home = pwd.getpwuid(os.getuid()).pw_dir
        except (ImportError, KeyError, OSError):
            return None
        if not home:
            return None
        path = os.path.join(home, STORE_FILE)
        try:
            info = os.stat(path)
            if (info.st_mode & 0o170000) != 0o100000:
                return None
            if (info.st_mode & 0o777) not in (0o600, 0o400):
                return None
            if info.st_uid != os.getuid():
                return None
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip() or None
        except OSError:
            return None

    def resolve_token_with_source(environ):  # type: ignore[misc]
        value = store_token()
        if value:
            return value, "store"
        value = (environ.get(TOKEN_ENV) or "").strip()
        if value:
            return value, "environment"
        return None, None

    def resolve_token(environ):  # type: ignore[misc]
        return resolve_token_with_source(environ)[0]


# ─────────────────────────────────────────────────────────────────────────────
# COMPILED-IN CONFIGURATION. Nothing below is read from the repository or the
# environment — a redirected host is a stolen token.
# ─────────────────────────────────────────────────────────────────────────────
API_HOST = "api.plexarm.com"
API_PATH = "/records/board?limit=1"

# ─────────────────────────────────────────────────────────────────────────────
# THE SWITCHBOARD — is this hook on at all?
#
# `switches.json`, beside this file, carries the shipped default for every hook
# in the plugin. `PLEXARM_HOOKS_OFF` and `PLEXARM_HOOKS_ON` override it on one
# machine: comma-separated hook names, set for example in the `env` block of a
# user's `settings.json`. OFF beats ON beats the file beats default-on. A
# missing, unreadable or malformed file switches nothing off.
#
# This block is copied verbatim into every Python hook in this directory,
# because each hook runs as a standalone script. Keep the copies identical.
# ─────────────────────────────────────────────────────────────────────────────
HOOK_NAME = "session_start_due"
SWITCHES_FILE = "switches.json"
SWITCH_ON_ENV = "PLEXARM_HOOKS_ON"
SWITCH_OFF_ENV = "PLEXARM_HOOKS_OFF"


def switched_on(name: str, environ: dict) -> bool:
    """`False` only when the environment or the shipped file says so."""

    def _names(var: str) -> set:
        return {p.strip() for p in (environ.get(var) or "").split(",") if p.strip()}

    if name in _names(SWITCH_OFF_ENV):
        return False
    if name in _names(SWITCH_ON_ENV):
        return True
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, SWITCHES_FILE), encoding="utf-8") as handle:
            entry = json.load(handle)["hooks"][name]
        return bool(entry["on"])
    except (OSError, ValueError, KeyError, TypeError):
        return True


#: Connect and read timeouts, set in the client; the `timeout` in `hooks.json`
#: is only an outer bound.
CONNECT_TIMEOUT_SECONDS = 2.0
READ_TIMEOUT_SECONDS = 3.0

#: Only a new session asks; `resume`, `clear` and `compact` continue one that
#: already did.
ASK_ON_SOURCES = frozenset({"startup"})

#: The notice names at most this many due entries and says how many more
#: there are.
MAX_NAMED = 5
MAX_TITLE = 120
MAX_ANSWER_BYTES = 512 * 1024

_READABLE_ID = re.compile(r"^(itm|act|gol|pip|fnd|inc|sch)-[a-z0-9-]{1,120}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def fetch(token: str) -> list | None:
    """`GET /records/board?limit=1` and return its `due` array, or None.

    **No retry.** Every failure is the state the session is in without this
    hook, and the next session asks again.
    """
    connection = http.client.HTTPSConnection(API_HOST, timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        connection.connect()
        if connection.sock is not None:
            connection.sock.settimeout(READ_TIMEOUT_SECONDS)
        connection.request(
            "GET",
            API_PATH,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "plexarm-plugin-hook",
            },
        )
        response = connection.getresponse()
        raw = response.read(MAX_ANSWER_BYTES)
        if response.status != 200:
            return None
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            return None
        due = parsed.get("due") if isinstance(parsed, dict) else None
        return due if isinstance(due, list) else None
    except (OSError, socket.timeout, http.client.HTTPException):
        return None
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001 - closing must never raise upward
            pass


def _clean(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    text = _CONTROL.sub(" ", text).strip()
    if not text:
        return None
    return text if len(text) <= MAX_TITLE else text[: MAX_TITLE - 1] + "…"


def notice(due: list) -> str | None:
    """The one block of text for the session, or None to print nothing.

    Each line names what to act on: the subject's id when it has one (what
    `start` and `record` take), else the schedule entry's.

    The reminder's own title leads; the subject's follows only when it says
    something else. The API sends the entry's `title` as null when it is the
    subject's title word for word (every routine occurrence, every reminder
    backfilled from its record), so the duplicate never reaches the context.
    A reminder's note is never on the board; `has_notes` points at where it is.
    """
    lines: list[str] = []
    for entry in due:
        if not isinstance(entry, dict):
            continue
        subject = entry.get("subject") if isinstance(entry.get("subject"), dict) else {}
        ident = subject.get("id") or entry.get("schedule_id")
        if not isinstance(ident, str) or not _READABLE_ID.match(ident):
            continue
        own = _clean(entry.get("title"))
        about = _clean(subject.get("title"))
        line = f"- {ident} — \"{own or about or '(no title)'}\""
        if own and about and own != about:
            line += f" (on \"{about}\")"
        due_at = entry.get("due_at")
        if isinstance(due_at, str) and len(due_at) >= 10:
            line += f", due {due_at[:10]}"
        missed = entry.get("missed")
        if isinstance(missed, int) and not isinstance(missed, bool) and missed > 0:
            line += f", {missed} missed"
        sched = entry.get("schedule_id")
        if entry.get("has_notes") is True and isinstance(sched, str) and _READABLE_ID.match(sched):
            line += f", notes on {sched}"
        lines.append(line)
    if not lines:
        return None
    named = lines[:MAX_NAMED]
    more = len(lines) - len(named)
    head = (
        f"Plexarm: {len(lines)} routine(s) or reminder(s) due for you. Read the "
        "board for the full list. A routine is satisfied by recording what you "
        "did with it as the subject."
    )
    tail = [f"- and {more} more on the board"] if more else []
    return "\n".join([head, *named, *tail])


def main() -> int:
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    if not switched_on(HOOK_NAME, os.environ):
        return 0
    if payload.get("source") not in ASK_ON_SOURCES:
        return 0

    token = resolve_token(os.environ)
    if not token:
        # Silent: `session_start_credential_check.py` reports a missing
        # credential on this same event.
        return 0

    due = fetch(token)
    if not due:
        return 0
    text = notice(due)
    if not text:
        return 0

    # A wrong `hookEventName` is not an error — the context is dropped silently.
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": text,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a SessionStart hook must never fail loudly
        sys.exit(0)
