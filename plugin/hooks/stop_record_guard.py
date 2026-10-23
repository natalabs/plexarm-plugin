#!/usr/bin/env python3
"""Plexarm — the Stop / SubagentStop record guard.

Fires when Claude Code is about to finish a session (`Stop`) or a subagent
(`SubagentStop`). It asks the Plexarm API one question — "is there work in
THIS project that this user moved to in progress and has not put down?" — and,
if the answer is yes, returns `{"decision":"block","reason":…}` so the agent
gets one more turn to close it or say it is not theirs.

`README.md` lists every network call this plugin makes, and this is one.

════════════════════════════════════════════════════════════════════════════════
RULE 1 — THE MARKER HAS EXACTLY ONE KEY, `project`
════════════════════════════════════════════════════════════════════════════════
The project comes from the repository (`CLAUDE.md` or `.plexarm`), which the
user may merely have cloned. Do not generalise the marker into `key=value`
pairs: any key that could choose a host or an endpoint would let a cloned
repository send the user's token somewhere else.

  * `_MARKER` is an anchored, full-line pattern that admits no other key.
  * The host and path are compiled in below and read from nowhere else — not
    from the repository and not from the environment, which a repository can
    set for hooks.
  * The look-back window is not sent at all; the server owns it and reports it
    back.

The request body has exactly one key, `project`, and the heartbeat has none.

════════════════════════════════════════════════════════════════════════════════
RULE 2 — THE ALIAS NEVER REACHES A SHELL
════════════════════════════════════════════════════════════════════════════════
The alias is text out of a repository file, so it is untrusted.

  * There is no shell anywhere in this file, and the alias never reaches a
    process. The only exec is the credential-store fallback: an absolute path,
    a fixed argument list, a fixed `PATH` and a timeout.
  * The value is validated against `_ALIAS` — the API's own project-alias
    pattern — before it is used for anything.
  * Every JSON body is built with `json.dumps`, never by string formatting.

════════════════════════════════════════════════════════════════════════════════
RULE 3 — IT FAILS OPEN BY CONSTRUCTION
════════════════════════════════════════════════════════════════════════════════
On `Stop` and `SubagentStop` a non-zero exit blocks, with stderr handed to the
model as the reason, so an unhandled exception would be an accidental block.
`main()` therefore wraps everything in `except BaseException`, and the only
`print` of a block decision sits on one deliberate line. No parse error,
missing payload field or disk error can produce a block.

One input can still block unhelpfully: a valid alias that names a different
project the same person also works in (for example a `CLAUDE.md` copied from
another repository). The block reason therefore names the alias and the file
it was read from, and tells the agent what to do if neither is right.

════════════════════════════════════════════════════════════════════════════════
WHAT THIS WRITES TO DISK
════════════════════════════════════════════════════════════════════════════════
Session-lifetime scratch state in the OS temp directory — never under `$HOME`,
never into the repository:

    <tmp>/plexarm-hook/<sha256(session_id)>/
        blocked_ids      the agent instances already blocked once
        binding.json     the parsed marker/sidecar, per cwd
        answer.json      the API's answer, shared across the session's agents
        outcomes.log     one line per firing — what happened

`outcomes.log` is how to tell a hook that never ran from one that ran and had
nothing to say; an install with no credential logs `not-checked:no-credential`
on every line. Set `PLEXARM_HOOK_LOG` to a `.log` file in `~/.config/plexarm/`
to get the same lines somewhere durable.

════════════════════════════════════════════════════════════════════════════════
WHAT IT DELIBERATELY DOES NOT DO
════════════════════════════════════════════════════════════════════════════════
It does not read the transcript: talking about recording is not recording, and
the API answers about records.

It does not walk up to a repository root, search recursively, follow
symlinks, or read a large file. Only `cwd` from the payload is read.

It cannot tell "this session did nothing" from "an earlier session left this
open". The server's window, the once-per-agent guard and the way out in the
block reason bound that.

It does not retry. The failure direction is already safe.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import socket
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# COMPILED-IN CONFIGURATION. Not read from the repository or the environment.
# See RULE 1.
#
# The one thing the environment may decide is whether this hook runs at all
# (`switched_on` below). That can never change where a token is sent.
# ─────────────────────────────────────────────────────────────────────────────
API_HOST = "api.plexarm.com"
API_PATH = "/records/loose-ends"

#: THE CREDENTIAL, through the shared resolver in `plexarm_credential.py`: the
#: OS credential store first, then `PLEXARM_TOKEN`, and nothing else. No
#: credential ever reaches this hook's command line.
#:
#: The import is wrapped and the same order repeated below it, because this
#: hook blocks on a non-zero exit and an import error at module scope would
#: escape before `main()` can catch it. Keep this copy reading in the same
#: order as the module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from plexarm_credential import (  # noqa: E402
        STORE_SERVICE,
        TOKEN_ENV,
        resolve_token,
        resolve_token_with_source,
        store_token,
    )
except BaseException:  # noqa: BLE001 - a blocking hook must not fail on an import
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

#: Connect and read timeouts, set in the HTTP client; the `timeout` in
#: `hooks.json` is only an outer bound.
CONNECT_TIMEOUT_SECONDS = 2.0
READ_TIMEOUT_SECONDS = 3.0

#: How long the agents of one session may reuse one answer: they share a
#: session, a user and a project, so the server would give each the same one.
#:
#: The answer is cached, never the decision — each agent is still blocked on
#: its own. The cache is dropped the moment a block is issued, because a block
#: asks for a write and the answer is expected to change.
ANSWER_TTL_SECONDS = 90

#: Read caps. A `CLAUDE.md` is prose and a `.plexarm` is one word; anything past
#: these is not the file we are looking for.
MAX_CLAUDE_MD_BYTES = 512 * 1024
MAX_SIDECAR_BYTES = 4 * 1024

#: THE ONLY KEY. Anchored, full-line, and it admits no second key — see RULE 1.
#: The value is captured loosely and validated strictly, so a malformed marker
#: (for example the template pasted unedited, `<project-alias>`) is recorded as
#: `not-checked:malformed` rather than looking like a repository with no marker.
#: The value stops at the first whitespace, so a second key can never match.
_MARKER = re.compile(
    r"^[ \t]*<!--[ \t]*plexarm:project=([^\s]{1,64})[ \t]*-->[ \t]*$",
    re.MULTILINE,
)

#: The API's project-alias pattern, copied because a hook runs on the user's
#: machine and cannot import server code. Keep it identical to the API's.
_ALIAS = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$")

#: Every outcome this hook can have, enumerated in one place. A non-200 answer
#: never blocks and is always recorded as a check that did not run, so a
#: server-side limit can never turn into a silent pass. Keep every outcome
#: literal in this file listed here. A detail may be appended after a `-`; the
#: base token is what is enumerated.
OUTCOMES = frozenset(
    {
        "blocked",
        "checked-clean",
        "not-checked:re-entry",
        "not-checked:already-blocked",
        "not-checked:unbound",
        "not-checked:disagree",
        "not-checked:malformed",
        "not-checked:no-credential",
        "not-checked:network",
        "not-checked:http",
        "not-checked:bad-payload",
        "not-checked:internal-error",
        "not-checked:switched-off",
    }
)

#: The two events this file is registered for. `SessionEnd` cannot block.
#: `Stop` fires once, for the main session only, with no `agent_id`; the
#: subagents are reached through `SubagentStop`.
STOP = "Stop"
SUBAGENT_STOP = "SubagentStop"


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
HOOK_NAME = "stop_record_guard"
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


# ─────────────────────────────────────────────────────────────────────────────
# THE STATE DIRECTORY AND THE LOG
#
# `<tmp>/plexarm-hook/<sha256(session_id)>/` holds this session's scratch state.
# Both directories must be real directories, owned by this user, that nobody
# else can write; otherwise the hook reads and writes no state. Files are
# written without following a symlink.
#
# `PLEXARM_HOOK_LOG` is honoured only for a `.log` file directly in
# `~/.config/plexarm/` or in `<tmp>/plexarm-hook/`; any other path is ignored.
#
# These functions are copied verbatim into every hook that keeps state. Keep
# the copies identical.
# ─────────────────────────────────────────────────────────────────────────────
STATE_ROOT = "plexarm-hook"
LOG_ENV = "PLEXARM_HOOK_LOG"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _private_dir(path: str) -> bool:
    """A real directory, not a link, owned by this user and writable by no one else."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return True
    return info.st_uid == getuid() and not info.st_mode & 0o022


def _state_dir(session_id: str) -> str | None:
    """`<tmp>/plexarm-hook/<sha256(session_id)>/`, or None.

    The session id is hashed rather than used as a path component, because it
    arrives in a payload.
    """
    try:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
        base = os.path.join(tempfile.gettempdir(), STATE_ROOT)
        path = os.path.join(base, digest)
        for directory in (base, path):
            try:
                os.mkdir(directory, 0o700)
            except FileExistsError:
                pass
            if not _private_dir(directory):
                return None
        return path
    except OSError:
        return None


def _password_home() -> str | None:
    """The caller's home from the password database, never `$HOME`."""
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_dir or None
    except (ImportError, KeyError, OSError):
        return None


def _log_target(environ) -> str | None:
    """`PLEXARM_HOOK_LOG`, when it names a `.log` file in an allowed directory."""
    value = (environ.get(LOG_ENV) or "").strip()
    if not value.endswith(".log"):
        return None
    try:
        folder = os.path.realpath(os.path.dirname(os.path.abspath(value)))
        roots = [os.path.join(tempfile.gettempdir(), STATE_ROOT)]
        home = _password_home()
        if home:
            roots.append(os.path.join(home, ".config", "plexarm"))
        if any(folder == os.path.realpath(root) for root in roots):
            return os.path.join(folder, os.path.basename(value))
    except (OSError, ValueError):
        return None
    return None


def _write_file(path: str, text: str, append: bool) -> None:
    """Write `text` to `path`, mode 0600, refusing a symlink. Never raises."""
    flags = os.O_WRONLY | os.O_CREAT | _NOFOLLOW | (os.O_APPEND if append else os.O_TRUNC)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Recording
# ─────────────────────────────────────────────────────────────────────────────
def _outcome(base: str, detail: str | None = None) -> str:
    """One outcome token, checked against the enumeration at the call site."""
    if base not in OUTCOMES:
        base = "not-checked:internal-error"
    return f"{base}-{detail}" if detail else base


def _record(state: str | None, outcome: str, note: str = "") -> None:
    """Append one line, to the temp state dir and to `PLEXARM_HOOK_LOG` if set.

    Never raises: a failed log write must not block a session.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{stamp} {outcome}" + (f" {note}" if note else "") + "\n"
    targets = [os.path.join(state, "outcomes.log")] if state else []
    durable = _log_target(os.environ)
    if durable:
        targets.append(durable)
    for target in targets:
        _write_file(target, line, append=True)


# ─────────────────────────────────────────────────────────────────────────────
# Reading the two binding sites
# ─────────────────────────────────────────────────────────────────────────────
def _read_small(path: str, cap: int) -> str | None:
    """Read a **regular file** of at most `cap` bytes, or return None.

    `O_NOFOLLOW` plus an `fstat` on the descriptor actually opened, so the file
    checked is the file read. A symlink is refused outright, and so is anything
    that is not a regular file, which could otherwise hang the read.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            return None
        # A single `read` may return fewer bytes than asked for; loop so a
        # `CLAUDE.md` is never truncated before its marker.
        chunks: list[bytes] = []
        remaining = cap
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")
    except OSError:
        return None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _valid(value: str) -> str | None:
    """An alias, or None. The single place a candidate becomes usable."""
    candidate = value.strip()
    return candidate if _ALIAS.fullmatch(candidate) else None


def _from_marker(cwd: str) -> tuple[str | None, bool]:
    """`(alias, present)` from `CLAUDE.md`'s marker.

    `present` distinguishes *"this repository has not opted in"* from *"it opted
    in and the value is wrong"*. Both fail open; only one is a bug to report.
    """
    text = _read_small(os.path.join(cwd, "CLAUDE.md"), MAX_CLAUDE_MD_BYTES)
    if text is None:
        return None, False
    found = _MARKER.findall(text)
    if not found:
        return None, False
    if len(found) > 1:
        # More than one marker is a repository that says two things. There is
        # no tie to break here, so there is no answer to give.
        return None, True
    return _valid(found[0]), True


def _from_sidecar(cwd: str) -> tuple[str | None, bool]:
    """`(alias, present)` from `.plexarm`.

    The whole file is the alias. There is no `key=value` here and there must
    never be one — see RULE 1.
    """
    text = _read_small(os.path.join(cwd, ".plexarm"), MAX_SIDECAR_BYTES)
    if text is None:
        return None, False
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        return None, True
    return _valid(lines[0]), True


def bind(cwd: str) -> tuple[str | None, str, str]:
    """`(alias, source, outcome_base)` — AGREEMENT, not precedence.

        both present and agreeing  -> bind
        exactly one present        -> bind
        present and disagreeing    -> DO NOT BLOCK
        neither present            -> DO NOT BLOCK   (+ the Stop heartbeat)

    There is no precedence between the two sites: a wrong value here means a
    wrong block, so a disagreement is treated as evidence, not a tie to break.

    Invariant: the outcome string is empty exactly when an alias was found.
    `decide` relies on it; keep it true in any new branch.
    """
    marker, marker_present = _from_marker(cwd)
    sidecar, sidecar_present = _from_sidecar(cwd)

    if not marker_present and not sidecar_present:
        return None, "", "not-checked:unbound"
    if marker_present and sidecar_present:
        if marker and sidecar and marker == sidecar:
            return marker, "CLAUDE.md and .plexarm", ""
        if marker is None and sidecar is None:
            return None, "", "not-checked:malformed"
        if marker is None or sidecar is None:
            # One site is unreadable and the other is confident. Two sites that
            # do not agree do not pick a winner.
            return None, "", "not-checked:malformed"
        return None, "", "not-checked:disagree"
    if marker_present:
        return (marker, "CLAUDE.md", "") if marker else (None, "", "not-checked:malformed")
    return (sidecar, ".plexarm", "") if sidecar else (None, "", "not-checked:malformed")


# ─────────────────────────────────────────────────────────────────────────────
# The one network call
# ─────────────────────────────────────────────────────────────────────────────
def ask(token: str, project: str | None) -> tuple[int, dict]:
    """`POST /records/loose-ends`. Returns `(status, body)`; status 0 = no answer.

    `timeout=` on the constructor governs the connect, and the socket is
    re-armed for the read once it exists. No retry.

    `project=None` is the heartbeat: a body with no project, which the server
    answers with a 422. It lets an installed hook in a repository with no
    marker be told apart from no hook at all.
    """
    body = json.dumps({} if project is None else {"project": project})
    connection = http.client.HTTPSConnection(
        API_HOST, timeout=CONNECT_TIMEOUT_SECONDS
    )
    try:
        connection.connect()
        if connection.sock is not None:
            connection.sock.settimeout(READ_TIMEOUT_SECONDS)
        connection.request(
            "POST",
            API_PATH,
            body=body.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "plexarm-plugin-hook",
            },
        )
        response = connection.getresponse()
        raw = response.read(64 * 1024)
        if response.status != 200:
            return response.status, {}
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            return 0, {}
        return 200, parsed if isinstance(parsed, dict) else {}
    except (OSError, socket.timeout, http.client.HTTPException):
        return 0, {}
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001 - closing must never raise upward
            pass


# ─────────────────────────────────────────────────────────────────────────────
# The block reason — the one channel that reaches a resumed agent
# ─────────────────────────────────────────────────────────────────────────────
def reason(answer: dict, project: str, source: str, cwd: str) -> str:
    """Self-contained, names the alias and its source, and gives the exact call.

    A block does not re-fire `SubagentStart`, so this string is the only
    context a resumed agent gets. It names the exact calls rather than an
    obligation, and gives an explicit way out.

    Work is attributed to the user, not the session, so every agent on one
    machine shares one login and a sibling or earlier session's open work is
    listed too. The text therefore asks the ownership question first, gives
    `moved_at` as the evidence for answering it, and makes "not mine" a named
    branch: a close note for work the agent never did would be a false record.
    The last paragraph covers a repository bound to the wrong project.
    """
    entries = answer.get("entries") or []
    hours = answer.get("within_hours")
    window = f"in the last {hours} hours" if isinstance(hours, int) else "recently"

    lines = [
        f"Work is open in Plexarm and nobody has put it down. "
        f"Project `{project}`, read from {source} in {cwd}.",
        "",
        f"Still `in_progress`, moved there {window} — by this USER, not by this "
        "session. You and every other agent on this machine share one Plexarm "
        "login, so a sibling or an earlier session's work is listed here too:",
    ]
    for entry in entries[:10]:
        item_id = str(entry.get("id", "?"))
        title = str(entry.get("title", ""))[:120]
        moved = str(entry.get("moved_at", ""))
        lines.append(f"  {item_id} — {title} (moved to in_progress at {moved})")
    if answer.get("truncated"):
        lines.append("  … and more.")
    lines += [
        "",
        "FIRST, DECIDE WHETHER EACH ONE IS YOURS. Plexarm attributes work to the "
        "person, never to the session, so a parallel or earlier session's open "
        "work appears here as readily as your own. The timestamp above is the "
        "evidence: if it predates this session, it is not yours.",
        "",
        "If you did the work — close it, saying what you actually did:",
        '  plexarm_close(id="<id>", note="<what you did>")',
        "",
        "If you started it and it is not finished — say where it stands:",
        '  plexarm_update(id="<id>", change={"status": "blocked", '
        '"next_steps": "<what is left>"})',
        "",
        "⚠️ IF YOU DID NOT DO THIS WORK, DO NOT CLOSE IT AND DO NOT GUESS WHAT "
        "WAS DONE. A close note is a claim about work that was performed, and an "
        "invented one is worse than an item left open. Say in your reply which "
        "of these were not yours, and finish.",
        "",
        f"And if `{project}` is not this repository's project, this hook is bound "
        "to the wrong project — say so plainly and finish. Either way it will "
        "not stop you again this session.",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# The decision
# ─────────────────────────────────────────────────────────────────────────────
def _blocked_already(state: str | None, key: str) -> bool:
    if not state or not key:
        return False
    try:
        with open(os.path.join(state, "blocked_ids"), encoding="utf-8") as handle:
            return key in {line.strip() for line in handle if line.strip()}
    except OSError:
        return False


def _remember_block(state: str | None, key: str) -> None:
    if not state or not key:
        return
    _write_file(os.path.join(state, "blocked_ids"), key + "\n", append=True)


def _cached_binding(state: str | None, cwd: str) -> tuple | None:
    """The parse, reused across the session's agents.

    Keyed on `(session, cwd)` and never on the session alone: agents of one
    session can run in different directories.
    """
    if not state:
        return None
    try:
        with open(os.path.join(state, "binding.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    entry = data.get(cwd) if isinstance(data, dict) else None
    if not isinstance(entry, list) or len(entry) != 3:
        return None
    return tuple(entry)


def _cache_binding(state: str | None, cwd: str, value: tuple) -> None:
    if not state:
        return
    path = os.path.join(state, "binding.json")
    try:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data[cwd] = list(value)
        _write_file(path, json.dumps(data), append=False)
    except OSError:
        pass


def _cached_answer(state: str | None, project: str, now: float) -> dict | None:
    if not state:
        return None
    try:
        with open(os.path.join(state, "answer.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("project") != project:
        return None
    if not isinstance(data.get("at"), (int, float)) or now - data["at"] > ANSWER_TTL_SECONDS:
        return None
    answer = data.get("answer")
    return answer if isinstance(answer, dict) else None


def _cache_answer(state: str | None, project: str, answer: dict, now: float) -> None:
    if not state:
        return
    _write_file(
        os.path.join(state, "answer.json"),
        json.dumps({"project": project, "at": now, "answer": answer}),
        append=False,
    )


def _drop_answer(state: str | None) -> None:
    """Dropped the moment a block is issued: the block asks for a write, so the
    answer is expected to change. Without this a stale answer blocks the *next*
    agent for work the blocked one has already closed."""
    if not state:
        return
    try:
        os.remove(os.path.join(state, "answer.json"))
    except OSError:
        pass


def decide(payload: dict, environ: dict, now: float) -> tuple[str | None, str, str]:
    """`(reason_or_None, outcome, note)`. The whole decision, and it is testable.

    Everything before the network call is ordered cheapest-first, and the two
    loop guards come before anything that can fail, so a block can never repeat
    forever.
    """
    event = payload.get("hook_event_name")
    if event not in (STOP, SUBAGENT_STOP):
        return None, _outcome("not-checked:bad-payload"), f"event={event!r}"

    # Guard 0 — the switchboard, before anything else. A hook that is off does
    # nothing but write the one line that says so, which keeps "off"
    # distinguishable from "never ran" in `outcomes.log`.
    if not switched_on(HOOK_NAME, environ):
        return None, _outcome("not-checked:switched-off"), HOOK_NAME

    session_id = str(payload.get("session_id") or "")
    state = _state_dir(session_id) if session_id else None

    # The block is keyed on the agent instance, never on the session: every
    # subagent reports the same `session_id` as the main session. `Stop`
    # carries no `agent_id`, and there the session id is the instance.
    key = str(payload.get("agent_id") or "") if event == SUBAGENT_STOP else session_id

    # Guard 2 — the client's own re-entry flag. Redundant with guard 1 today; it
    # is here because guard 1 depends on a file write succeeding, and a guard
    # that fails open on a full disk is not a guard.
    if payload.get("stop_hook_active"):
        return None, _outcome("not-checked:re-entry"), key

    # Guard 1 — this instance has already been asked once.
    if _blocked_already(state, key):
        return None, _outcome("not-checked:already-blocked"), key

    cwd = str(payload.get("cwd") or "")
    cached = _cached_binding(state, cwd) if cwd else None
    if cached is None:
        project, source, unbound = bind(cwd) if cwd else (None, "", "not-checked:unbound")
        _cache_binding(state, cwd, (project, source, unbound))
    else:
        project, source, unbound = cached

    if project is None:
        # The heartbeat is `Stop`-only, so it is one call per session, not one
        # per subagent.
        if unbound == "not-checked:unbound" and event == STOP:
            token = resolve_token(environ)
            if not token:
                return None, _outcome("not-checked:no-credential"), ""
            status, _ = ask(token, None)
            return None, _outcome("not-checked:unbound"), f"heartbeat={status}"
        return None, _outcome(unbound), cwd

    token = resolve_token(environ)
    if not token:
        return None, _outcome("not-checked:no-credential"), ""

    answer = _cached_answer(state, project, now)
    if answer is None:
        status, answer = ask(token, project)
        if status == 0:
            return None, _outcome("not-checked:network"), project
        if status != 200:
            return None, _outcome("not-checked:http", str(status)), project
        _cache_answer(state, project, answer, now)

    entries = answer.get("entries") or []
    if not entries:
        return None, _outcome("checked-clean"), project

    _remember_block(state, key)
    _drop_answer(state)
    return reason(answer, project, source, cwd), _outcome("blocked"), key


def main() -> int:
    """Read the payload, decide, print at most one object, and **always exit 0**.

    Exit 0 with no output is "do not block". Every failure lands in the
    `except BaseException` below. The only line that can block is the `print`
    in the branch above it.
    """
    state = None
    try:
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw)
        except ValueError:
            _record(None, _outcome("not-checked:bad-payload"), "unparseable stdin")
            return 0
        if not isinstance(payload, dict):
            _record(None, _outcome("not-checked:bad-payload"), "payload is not an object")
            return 0

        session_id = str(payload.get("session_id") or "")
        state = _state_dir(session_id) if session_id else None

        blocking_reason, outcome, note = decide(payload, os.environ, time.time())
        _record(state, outcome, note)

        if blocking_reason is not None:
            print(json.dumps({"decision": "block", "reason": blocking_reason}))
        return 0
    except BaseException as exc:  # noqa: BLE001 - see the docstring; this IS the control
        _record(state, _outcome("not-checked:internal-error"), type(exc).__name__)
        return 0


if __name__ == "__main__":
    sys.exit(main())
