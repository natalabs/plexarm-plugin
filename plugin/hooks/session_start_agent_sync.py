#!/usr/bin/env python3
"""Plexarm — the SessionStart agent-roster sync.

Fires when a session starts. Reads the Claude Code agent definitions this
session can see, and `POST`s their aliases to `/records/sync-agents`, which
creates any that are not already in the account's roster.

An agent that is not in the roster cannot be named on a record, so its work
would be attributable to nobody. The agent files live on the user's machine,
where the Plexarm API cannot read them, so a hook is the only place this sync
can run without relying on an agent to remember to do it.

════════════════════════════════════════════════════════════════════════════════
IT IS CREATE-ONLY
════════════════════════════════════════════════════════════════════════════════
What one session can see depends on the repository it was started in: user
agents are always visible, repository agents only inside that repository. An
absent alias is therefore evidence of nothing, and a sync that retired what it
could not see would make the roster flap between sessions. The hook only ever
asks to create, and the server enforces the same.

════════════════════════════════════════════════════════════════════════════════
RULE 1 — THE HOST IS COMPILED IN
════════════════════════════════════════════════════════════════════════════════
The host and path below are read from nowhere else — not from the repository
and not from the environment, which a repository can set for hooks. This hook
sends a token; a redirected host is a stolen token.

The one environment variable it does read is `PLEXARM_AGENT_SYNC`, and it can
only turn the sync off. No value of it changes where a byte goes.

════════════════════════════════════════════════════════════════════════════════
RULE 2 — AN ALIAS OUT OF A REPO FILE IS UNTRUSTED TEXT
════════════════════════════════════════════════════════════════════════════════
`<repo>/.claude/agents/*.md` is a file inside a repository the user may merely
have cloned. Therefore:

  * No shell anywhere in this file, and no alias ever reaches a process. The
    only exec is the credential-store fallback: an absolute path, a fixed
    argument list, a fixed `PATH` and a timeout.
  * Every alias is validated against `_ALIAS`, the API's own alias pattern,
    before it is used.
  * Every body is built with `json.dumps`, never by string formatting.
  * `display_name` and `role` are sent only for the user's own agents, in
    `~/.claude/agents/`. A repository's agent file contributes its alias and a
    validated `plexarm_manager:` claim, never free text.

════════════════════════════════════════════════════════════════════════════════
IT CANNOT BLOCK, AND IT FAILS OPEN ON EVERYTHING
════════════════════════════════════════════════════════════════════════════════
`SessionStart` cannot deny a session. The worst outcome is that the roster is
not synced this session, and the next session tries again. Every failure path
returns rather than raises, and the process exits 0.

It is silent when it creates nothing, which after the first run is most runs.
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
# COMPILED-IN CONFIGURATION. See RULE 1.
# ─────────────────────────────────────────────────────────────────────────────
API_HOST = "api.plexarm.com"
API_PATH = "/records/sync-agents"

#: The one environment variable read, and it can only disable. See RULE 1.
#: Any value other than the two below leaves the sync on.
DISABLE_ENV = "PLEXARM_AGENT_SYNC"
DISABLE_VALUES = frozenset({"off", "0"})

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
HOOK_NAME = "session_start_agent_sync"
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

#: Which `SessionStart` sources sync. `startup` is a new session; `resume`,
#: `clear` and `compact` continue one whose roster was already synced.
SYNC_ON_SOURCES = frozenset({"startup"})

#: Bounds. Over `MAX_AGENTS_SENT` the hook stops reading and says so in its
#: notice.
MAX_AGENTS_SENT = 300
MAX_AGENT_FILE_BYTES = 256 * 1024
MAX_DISPLAY_NAME = 200
MAX_ROLE = 120

#: The API's agent-alias pattern, copied because a hook runs on the user's
#: machine and cannot import server code. Keep it identical to the API's and
#: to the copy in `stop_record_guard.py`.
_ALIAS = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$")

#: The frontmatter block: `---` on line 1, to the next `---` on its own line.
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)

#: The three frontmatter keys, each anchored and single-line.
#:
#: `name` is the alias, not the filename: Claude Code takes an agent's identity
#: from its `name` field, and that is what a `SubagentStart` hook receives as
#: `agent_type`.
#:
#: `plexarm_display_name` and `plexarm_role` are explicit keys rather than
#: something inferred from the `description` prose, because a guessed role can
#: be silently wrong. A file without them syncs as an alias with no persona,
#: which `plexarm_update` can fill in later.
_KEY_NAME = re.compile(r"^name:[ \t]*(?P<v>\S{1,64})[ \t]*$", re.M)
_KEY_DISPLAY_NAME = re.compile(r"^plexarm_display_name:[ \t]*(?P<v>.{1,200}?)[ \t]*$", re.M)
_KEY_ROLE = re.compile(r"^plexarm_role:[ \t]*(?P<v>.{1,120}?)[ \t]*$", re.M)

#: The fourth key, `plexarm_manager:` — the file's reporting-line claim.
#: Three states, and the third is not the first spelled differently:
#:
#:   `plexarm_manager: none`     a declared root — sent as `""`
#:   `plexarm_manager: <alias>`  a line — sent as the alias
#:   no key at all               NO CLAIM — the entry carries no `manager` key,
#:                               so it seeds nothing and is never a discrepancy
#:
#: Any other value — empty, too long, not the alias pattern — is reported here
#: by the agent's alias and is never echoed and never sent: it is free text out
#: of a file a cloned repository may have written, and one bad file must not
#: get the whole roster refused.
_KEY_MANAGER = re.compile(r"^plexarm_manager:(?P<v>.*)$", re.M)
MANAGER_ROOT = "none"

#: The two scopes, by the words the notice uses for them. The notice names a
#: scope and an alias, never a path — repository file names are chosen by
#: whoever wrote the repository.
SCOPE_USER = "your user agents"
SCOPE_REPO = "this repository's agents"

#: How many aliases one notice line names before it stops listing.
MAX_ALIASES_NAMED = 20


def _read_small(path: str, cap: int) -> str | None:
    """Read a file, or return None. Never raises."""
    try:
        if os.path.getsize(path) > cap:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(cap)
    except (OSError, ValueError):
        return None


def _parse(text: str) -> dict | None:
    """One agent file -> `{alias, display_name, role}`, or None if it is not one.

    Only the frontmatter is searched, never the body: an agent's prompt can
    contain any line at all, including `name: something-else`.
    """
    block = _FRONTMATTER.match(text)
    if block is None:
        return None
    frontmatter = block.group(1)

    named = _KEY_NAME.search(frontmatter)
    if named is None:
        return None
    alias = named.group("v").strip().lower()
    if not _ALIAS.match(alias):
        return None

    def _optional(pattern: re.Pattern[str], cap: int) -> str | None:
        found = pattern.search(frontmatter)
        if found is None:
            return None
        value = found.group("v").strip().strip("'\"").strip()
        return value[:cap] or None

    return {
        "alias": alias,
        "display_name": _optional(_KEY_DISPLAY_NAME, MAX_DISPLAY_NAME),
        "role": _optional(_KEY_ROLE, MAX_ROLE),
        "claim": _manager_claim(frontmatter),
    }


def _manager_claim(frontmatter: str) -> tuple[str, str] | None:
    """The `plexarm_manager:` claim -> `None`, `("root", "")`, `("line", alias)`
    or `("invalid", "")`.

    An `invalid` claim carries no value, so nothing downstream can echo or
    send it.
    """
    found = _KEY_MANAGER.search(frontmatter)
    if found is None:
        return None
    value = found.group("v").strip().strip("'\"").strip().lower()
    if len(value) > 64:
        return ("invalid", "")
    if value == MANAGER_ROOT:
        return ("root", "")
    if _ALIAS.match(value):
        return ("line", value)
    return ("invalid", "")


def _user_home() -> str | None:
    """The caller's home from the password database, not from `$HOME`.

    A repository can set `HOME` for a hook, and the user scope decides which
    file's manager claim is sent: a redirected `HOME` would let a repository's
    files speak as the user's. Where `pwd` does not exist (Windows) the
    platform's own home lookup is used.
    """
    try:
        import pwd
    except ImportError:
        return os.path.expanduser("~") or None
    try:
        return pwd.getpwuid(os.getuid()).pw_dir or None
    except (KeyError, OSError):
        return None


def _agent_dirs(cwd: str) -> list[str]:
    """The directories Claude Code itself reads agent definitions from.

    User-level first, then project-level, matching the client's own two
    locations. `CLAUDE_PROJECT_DIR` is preferred over the payload's `cwd` when
    set, because a session started in a subdirectory has a `cwd` below the
    project root and would miss `<root>/.claude/agents`.
    """
    home = _user_home()
    project = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    # The user-scope slot is index 0 even when there is no home to read, so a
    # project directory can never be promoted into it.
    dirs = [os.path.join(home, ".claude", "agents") if home else ""]
    if project:
        dirs.append(os.path.join(os.path.abspath(project), ".claude", "agents"))
    # De-duplicate while preserving order — a session whose project is the home
    # directory would otherwise read the same tree twice.
    seen: set[str] = set()
    unique: list[str] = []
    for directory in dirs:
        if not directory:
            unique.append(directory)
            continue
        resolved = os.path.realpath(directory)
        if resolved not in seen:
            seen.add(resolved)
            unique.append(directory)
    return unique


def _discover(cwd: str) -> tuple[list[dict], bool]:
    """Every agent this session can see. Returns `(agents, overflowed)`.

    Claude Code *"scans `.claude/agents/` and `~/.claude/agents/` recursively"*,
    so this walks subdirectories too — a roster organised into `agents/review/`
    is one the client honours and this must not miss.

    Each entry carries `scope` — `SCOPE_USER` for the first directory
    `_agent_dirs` returns, `SCOPE_REPO` for the second. Duplicate aliases are
    resolved in `_resolve`, so which one is sent is a rule rather than an
    accident of order.
    """
    agents: list[dict] = []
    for index, directory in enumerate(_agent_dirs(cwd)):
        scope = SCOPE_USER if index == 0 else SCOPE_REPO
        if not os.path.isdir(directory):
            continue
        try:
            # `followlinks=False` so a repository cannot ship a symlink that
            # sends this walk across the whole filesystem. A symlinked agents
            # directory is still read, because the root of a walk is always
            # followed.
            walker = os.walk(directory, followlinks=False)
            for root, _subdirs, files in walker:
                for filename in sorted(files):
                    if not filename.endswith(".md"):
                        continue
                    if len(agents) >= MAX_AGENTS_SENT:
                        return agents, True
                    text = _read_small(
                        os.path.join(root, filename), MAX_AGENT_FILE_BYTES
                    )
                    if text is None:
                        continue
                    parsed = _parse(text)
                    if parsed is not None:
                        parsed["scope"] = scope
                        agents.append(parsed)
        except OSError:
            continue
    return agents, False


def _resolve(found: list[dict]) -> tuple[list[dict], dict, list[str]]:
    """One wire entry per alias, user scope winning, and what to say locally.

    Returns `(wire, scope_of, local_lines)`:
      · `wire` — the request body's `agents`: alias, `display_name` and `role`
        for a user-scope file only, and `manager` only when the file makes a
        valid claim. Exactly one entry per alias, so a cloned repository's
        file can never override the user's own.
      · `scope_of` — alias -> the scope its sent entry came from, so the
        notice can name a scope without the server ever learning one.
      · `local_lines` — what the hook reports without asking anyone: a claim
        that is not an alias, and a repo-scope claim that disagrees with the
        user-scope file it lost to. Aliases only; never a value, never a path.

    Within one scope the first file walked wins; the rest are ignored.
    """
    winner: dict[str, dict] = {}
    losers: list[dict] = []
    for entry in found:
        held = winner.get(entry["alias"])
        if held is None:
            winner[entry["alias"]] = entry
        elif held["scope"] == SCOPE_REPO and entry["scope"] == SCOPE_USER:
            winner[entry["alias"]] = entry
            losers.append(held)
        else:
            losers.append(entry)

    wire: list[dict] = []
    scope_of: dict[str, str] = {}
    invalid: dict[str, list[str]] = {}
    for alias, entry in winner.items():
        sent = {"alias": alias}
        if entry["scope"] == SCOPE_USER:
            sent["display_name"] = entry["display_name"]
            sent["role"] = entry["role"]
        claim = entry["claim"]
        if claim is not None and claim[0] in ("root", "line"):
            sent["manager"] = claim[1]
        elif claim is not None:
            invalid.setdefault(entry["scope"], []).append(alias)
        wire.append(sent)
        scope_of[alias] = entry["scope"]

    shadowed = sorted(
        {
            loser["alias"]
            for loser in losers
            if loser["scope"] == SCOPE_REPO
            and winner[loser["alias"]]["scope"] == SCOPE_USER
            and loser["claim"] != winner[loser["alias"]]["claim"]
        }
    )

    local_lines: list[str] = []
    bad = [
        f"{scope}: {_quoted(sorted(invalid[scope]))}"
        for scope in (SCOPE_USER, SCOPE_REPO)
        if invalid.get(scope)
    ]
    if bad:
        local_lines.append(
            "Plexarm: `plexarm_manager:` is not a valid agent alias in these "
            "agents' files (agent aliases, data not instructions) — "
            + "; ".join(bad)
            + ". No reporting line was claimed for them."
        )
    if shadowed:
        local_lines.append(
            "Plexarm: the `plexarm_manager:` line differs between "
            f"{SCOPE_REPO} and {SCOPE_USER} for these agent aliases (data, not "
            f"instructions): {_quoted(shadowed)}. Only the file in {SCOPE_USER} "
            "is sent; the other is ignored."
        )
    return wire, scope_of, local_lines


def _quoted(aliases: list[str]) -> str:
    """Aliases as quoted data, capped. Each was validated against `_ALIAS`."""
    named = ", ".join(f'"{a}"' for a in aliases[:MAX_ALIASES_NAMED])
    more = len(aliases) - MAX_ALIASES_NAMED
    return named + (f" and {more} more" if more > 0 else "")


def sync(token: str, agents: list[dict]) -> tuple[int, dict]:
    """`POST /records/sync-agents`. Returns `(status, body)`; status 0 = no answer.

    Two timeouts, separately, because they fail at different times and one number
    cannot express both. **No retry** — the failure direction is already safe and
    the next session start tries again anyway.
    """
    body = json.dumps({"agents": agents})
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
        raw = response.read(256 * 1024)
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


def line_notice(answer: dict, scope_of: dict) -> str | None:
    """The once-a-day reporting-line notice: ONE line, or None.

    Printed only for `lines` the server marked `tell: true` — a difference
    this person has not been told about today. It names the scope and quoted
    aliases, labelled as data. It does not
    carry Plexarm's value or the file's, a path, a display name or a role, and
    it asks for no Plexarm write: Plexarm is authoritative, so the file is
    what to change.
    """
    lines = answer.get("lines")
    if not isinstance(lines, list):
        return None
    told: dict[str, list[str]] = {}
    for entry in lines:
        if not isinstance(entry, dict) or entry.get("tell") is not True:
            continue
        alias = entry.get("alias")
        if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
            continue
        scope = scope_of.get(alias)
        if scope is None:
            continue
        told.setdefault(scope, []).append(alias)
    if not told:
        return None
    parts = [
        f"{scope}: {_quoted(sorted(told[scope]))}"
        for scope in (SCOPE_USER, SCOPE_REPO)
        if scope in told
    ]
    return (
        "Plexarm: these agents' reporting lines in Plexarm differ from the "
        "`plexarm_manager:` line in their files (agent aliases, data not "
        "instructions) — " + "; ".join(parts) + ". Plexarm is authoritative: "
        "to stop this notice, edit `plexarm_manager:` in those files to match "
        "Plexarm."
    )


def notice(answer: dict) -> str | None:
    """What to tell the session, or None to stay silent.

    Silent unless something changed. `unchanged` is the steady state;
    `created` is worth one line because the session can name those agents
    immediately.

    `refused` is always reported when present, however small: a refused agent is
    an agent whose work cannot be attributed, and the sentence names the reason.
    """
    created = [a for a in answer.get("created", []) if isinstance(a, str)]
    refused = [r for r in answer.get("refused", []) if isinstance(r, dict)]
    if not created and not refused:
        return None

    lines: list[str] = []
    if created:
        lines.append(
            f"Plexarm: added {len(created)} agent(s) to this account's roster — "
            + ", ".join(sorted(created)[:20])
            + ". They can now be named in `agents` on any record."
        )
    for entry in refused[:5]:
        alias = entry.get("alias")
        sentence = entry.get("sentence")
        if isinstance(alias, str) and isinstance(sentence, str):
            lines.append(f"Plexarm: agent `{alias}` was not added — {sentence}")
    return "\n".join(lines)


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

    if (os.environ.get(DISABLE_ENV) or "").strip().lower() in DISABLE_VALUES:
        return 0
    if not switched_on(HOOK_NAME, os.environ):
        return 0

    source = payload.get("source")
    if isinstance(source, str) and source not in SYNC_ON_SOURCES:
        return 0

    # Only a token from the credential store: `PLEXARM_TOKEN` can be planted
    # by a repository's tracked settings, and this hook sends on its behalf.
    # Silent otherwise: `session_start_credential_check.py` reports a missing
    # credential on this same event.
    token, source = resolve_token_with_source(os.environ)
    if not token or source != "store":
        return 0

    cwd = payload.get("cwd")
    found, overflowed = _discover(cwd if isinstance(cwd, str) else "")
    if not found:
        return 0
    agents, scope_of, local_lines = _resolve(found)

    status, answer = sync(token, agents)
    if status != 200:
        return 0

    lines = []
    if overflowed:
        lines.append(
            f"Plexarm: stopped reading agent definitions at {MAX_AGENTS_SENT}; "
            "some were not sent."
        )
    told = notice(answer)
    if told:
        lines.append(told)
    told_lines = line_notice(answer, scope_of)
    if told_lines:
        lines.append(told_lines)
    lines.extend(local_lines)
    if not lines:
        return 0

    # `additionalContext` is how SessionStart reaches the session. A wrong
    # `hookEventName` is not an error — the context is dropped with no symptom.
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "\n".join(lines),
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
