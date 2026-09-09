#!/usr/bin/env python3
"""Plexarm — the SessionStart agent-roster sync.

Fires when a session starts. Reads the Claude Code agent definitions this
session can see, and `POST`s their aliases to `/records/sync-agents`, which
CREATES any that are not already in the account's roster.

WHY IT EXISTS. `public.agents` is the table `agent_ids` points at on all six
record tables, and `validate_reference_arrays` refuses any element that is not
live in the account. So an agent that has no row cannot be named on a record —
the work it did is attributable to nobody. Before this hook the only ways to
populate the roster were the Postgres superuser password and, after
`plexarm_open_agent` shipped, one call per agent by hand. Measured 2026-08-11:
the roster held 13 rows and the machine had 30 agent files.

WHY A HOOK AND NOT AN MCP TOOL. `plexarm_*` tools execute at `api.plexarm.com`,
which cannot read `~/.claude/agents/`. A tool would have to be handed the roster
by an agent that had already read the files, which makes discovery depend on the
model remembering to do it — non-negotiable #5 is the opposite of that. **The
filesystem is only visible from the user's machine, so the hook is the only
place this can run at all.** That is a fact about the deployment, not a
preference between two designs.

════════════════════════════════════════════════════════════════════════════════
IT IS CREATE-ONLY, AND THE REASON IS NOT CAUTION
════════════════════════════════════════════════════════════════════════════════
What one session can see is SESSION-DEPENDENT. Measured 2026-08-11 across every
repo on the operator's machine:

    session in repository A  ->  13 central + 17 repo-local
    session in repository B  ->  13 central +  0
    session in repository C  ->  13 central +  0

So an absent alias is evidence of nothing. A sync that retired what it could not
see would retire all 17 of repository A's agents on the next session in
repository B and re-create them on the session after — 17 rows flapping forever,
with no defect anywhere in the code. The server enforces this too (`core/records/schemas.py`); the hook
simply never asks for anything else.

════════════════════════════════════════════════════════════════════════════════
RULE 1 — THE HOST IS COMPILED IN. Inherited verbatim from `stop_record_guard.py`
════════════════════════════════════════════════════════════════════════════════
The host and path below are read from nowhere else — not from the repo, and
**not from the environment either**. A repo can ship `.claude/settings.json`,
which sets environment variables for hooks, so an env-var override of the host is
credential exfiltration through a longer pipe. This hook sends a `plx_` token; a
redirected host is a stolen token.

The one environment variable it *does* read is `PLEXARM_AGENT_SYNC`, and it can
only ever turn the sync **off**. The asymmetry is the point: a repo that sets it
suppresses a feature, which leaks nothing. There is no value of it that changes
where a byte goes.

════════════════════════════════════════════════════════════════════════════════
RULE 2 — AN ALIAS OUT OF A REPO FILE IS ATTACKER-CONTROLLED TEXT
════════════════════════════════════════════════════════════════════════════════
`<repo>/.claude/agents/*.md` is a file inside a repository the user may merely
have cloned. Therefore, structurally rather than carefully:

  * **No `subprocess`, no `os.system`, no shell anywhere in this file.**
  * Every alias is validated against `_ALIAS` — a pinned copy of the API's own
    pattern, which is also `agents_alias_format` — **before** it is used.
  * Every body is built with `json.dumps`, never by string formatting.
  * `display_name` and `role` are length-clamped here and bounded again by the
    server's pydantic model and by `agents_display_name_nonempty` /
    `agents_role_len`. They are values, never identifiers, and reach no SQL text.

⚠️ **A RESIDUAL RISK THIS FEATURE CREATES, NAMED RATHER THAN GLOSSED.** Opening a
session in a hostile cloned repo adds that repo's agent aliases to your roster.
The damage is bounded — rows are additive, soft-deletable, capped at
`MAX_AGENTS_SENT` per call, and creating one requires the caller to be an account
admin — but it is real roster pollution and no check here prevents it. It is
filed as a finding with its re-eval trigger rather than fixed, because the fix
(honouring Claude Code's workspace-trust state) needs a signal the hook cannot
currently read.

════════════════════════════════════════════════════════════════════════════════
IT CANNOT BLOCK, AND IT FAILS OPEN ON EVERYTHING
════════════════════════════════════════════════════════════════════════════════
`SessionStart` cannot deny a session. The worst failure available to this file is
that the roster does not get synced this session — which is the state you are in
without it — and the next session tries again. Every exception path below
returns rather than raises, and the process exits 0 unconditionally.

**It is silent when it creates nothing**, which after the first run is every run.
Printing "0 agents synced" on every session start would be a per-session tax paid
in the user's attention for no information.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import sys

# ─────────────────────────────────────────────────────────────────────────────
# COMPILED-IN CONFIGURATION. See RULE 1.
# ─────────────────────────────────────────────────────────────────────────────
API_HOST = "api.plexarm.com"
API_PATH = "/records/sync-agents"

#: The `userConfig` key `api_token`, as Claude Code exposes it to a hook
#: process. Same constant, same reason, as `stop_record_guard.py`: a shell-form
#: command that *references* `${user_config.api_token}` does not run at all, and
#: the exec form would put the secret in the process table.
#: ⛔ PRIMARY IS `PLEXARM_TOKEN` AS OF 1.4.1 — full reasoning in
#: `stop_record_guard.py`. The plugin option lives in `Claude Code-credentials`,
#: which the client rebuilds on its own ~8-hourly OAuth refresh, dropping
#: tenants it does not know about (`anthropics/claude-code` #62442, closed as
#: not planned). Environment first; the plugin option is the compatibility tail.
TOKEN_ENV = "PLEXARM_TOKEN"
TOKEN_ENV_FALLBACK = "CLAUDE_PLUGIN_OPTION_API_TOKEN"

#: The one environment variable read, and it can only disable. See RULE 1.
#: Any value other than the two below leaves the sync ON — an unrecognised
#: value must not silently disable a feature.
DISABLE_ENV = "PLEXARM_AGENT_SYNC"
DISABLE_VALUES = frozenset({"off", "0"})

#: Connect and read separately, **in the client** — the `timeout` in `hooks.json`
#: is an outer belt only, because `http.client`'s default is 600 seconds.
CONNECT_TIMEOUT_SECONDS = 2.0
READ_TIMEOUT_SECONDS = 3.0

#: Which `SessionStart` sources sync. `startup` is a new session; `resume`,
#: `clear` and `compact` continue one whose roster was already synced, so they
#: would pay a network round trip for a call whose answer cannot have changed.
SYNC_ON_SOURCES = frozenset({"startup"})

#: Bounds. `MAX_AGENTS_SENT` mirrors the server's `MAX_SYNC_AGENTS`; over it the
#: server refuses the whole request, so the hook stops reading rather than
#: sending something certain to be rejected. It is deliberately NOT a silent
#: truncation — see `_discover`, which reports the overflow.
MAX_AGENTS_SENT = 300
MAX_AGENT_FILE_BYTES = 256 * 1024
MAX_DISPLAY_NAME = 200
MAX_ROLE = 120

#: A pinned copy of `backend/core/records/resolution.py::_ALIAS`, which is also
#: `agents_alias_format` in the schema. Copied rather than imported because a
#: plugin hook runs on a user's machine and must not import server code
#: (`backend/CLAUDE.md`, placement rule 4). Gate 57 already asserts this pattern
#: is byte-identical to the API's; the new gate asserts this file uses the same
#: literal as its sibling hook rather than a second spelling of it.
_ALIAS = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$")

#: The frontmatter block: `---` on line 1, to the next `---` on its own line.
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)

#: ⚠️ THE THREE KEYS, EACH ANCHORED AND SINGLE-LINE.
#:
#: `name` is the AUTHORITY for the alias, not the filename. Claude Code's own
#: documentation is explicit — *"identity comes only from the `name` frontmatter
#: field"*, and *"the filename doesn't have to match"* — and `name` is what a
#: `SubagentStart` hook receives as `agent_type`. Keying on the filename would
#: work today (measured 2026-08-11: they are equal in all 30 files) and would
#: silently mis-key the day someone renames a file.
#:
#: `plexarm_display_name` and `plexarm_role` are a CONTRACT, not an inference.
#: An earlier draft parsed the persona out of the `description` prose with an
#: anchored regex: 22 of 30 files matched and none matched wrongly, but the 8
#: misses included `capital-manager`, whose text names **Lior** and then says he
#: *"reports to Idris (Head of Trading)"* — so a slightly looser pattern returns
#: another agent's job title as this agent's role, confidently and silently.
#: That is `R-CORPUS` exactly. The keys were added to all 30 files instead, and
#: **undocumented frontmatter keys were verified tolerated by measurement**, not
#: assumed: two probe agents, one with these keys and one without, both loaded
#: on Claude Code v2.1.227.
#:
#: A file without them syncs as an alias with no persona, which is recoverable —
#: `display_name` and `role` are editable through `plexarm_update`. A WRONG one
#: is not recoverable, because nothing would ever flag it.
_KEY_NAME = re.compile(r"^name:[ \t]*(?P<v>\S{1,64})[ \t]*$", re.M)
_KEY_DISPLAY_NAME = re.compile(r"^plexarm_display_name:[ \t]*(?P<v>.{1,200}?)[ \t]*$", re.M)
_KEY_ROLE = re.compile(r"^plexarm_role:[ \t]*(?P<v>.{1,120}?)[ \t]*$", re.M)


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

    ⚠️ **ONLY THE FRONTMATTER IS SEARCHED, NEVER THE BODY.** The regexes are
    applied to the block between the leading `---` fences and nothing else. An
    agent's system prompt is prose that can contain any line at all, including
    a line reading `name: something-else`; matching against the whole file would
    let a body line override the real identity.
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
        # `agents_display_name_nonempty` / `agents_role_nonempty` refuse an empty
        # string; sending None is the same fact and does not need a refusal.
        return value[:cap] or None

    return {
        "alias": alias,
        "display_name": _optional(_KEY_DISPLAY_NAME, MAX_DISPLAY_NAME),
        "role": _optional(_KEY_ROLE, MAX_ROLE),
    }


def _agent_dirs(cwd: str) -> list[str]:
    """The directories Claude Code itself reads agent definitions from.

    User-level first, then project-level, matching the client's own two
    locations. `CLAUDE_PROJECT_DIR` is preferred over the payload's `cwd` when
    set, because a session started in a subdirectory has a `cwd` below the
    project root and would miss `<root>/.claude/agents`.
    """
    home = os.path.expanduser("~")
    project = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    dirs = [os.path.join(home, ".claude", "agents")]
    if project:
        dirs.append(os.path.join(os.path.abspath(project), ".claude", "agents"))
    # De-duplicate while preserving order — a session whose project IS the home
    # directory would otherwise read the same tree twice and send every alias
    # twice, which the server would report as half `created` and half
    # `unchanged`: correct, and confusing.
    seen: set[str] = set()
    unique: list[str] = []
    for directory in dirs:
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

    Duplicate aliases across the two locations are kept as-is and sent. The
    server's unique constraint decides, and reports the loser as `unchanged`,
    which is the only place an alias collision becomes visible at all — the
    global-roster decision is a bet that collisions do not happen, and this is
    the instrument that reads the bet.
    """
    agents: list[dict] = []
    for directory in _agent_dirs(cwd):
        if not os.path.isdir(directory):
            continue
        try:
            # ⚠️ `followlinks=False`, AND IT IS NOT A PERFORMANCE SETTING.
            # A repository can ship `.claude/agents/anything -> /` and this walk
            # would descend the whole filesystem reading every `.md` in it. The
            # directories that legitimately ARE symlinks — `~/.claude/agents`
            # on the machine this was measured on, and two sibling repos' agent
            # dirs — are the ROOT of a walk, which `os.walk` follows regardless
            # of this flag. So nothing real is lost: measured after the change,
            # the repo with no local agents still discovers 13 and the one with
            # 17 local agents still discovers 30.
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
                        agents.append(parsed)
        except OSError:
            continue
    return agents, False


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


def notice(answer: dict) -> str | None:
    """What to tell the session, or None to stay silent.

    ⚠️ **SILENT UNLESS SOMETHING CHANGED.** `unchanged` is the steady state and
    is not news. `created` is worth one line because a new agent becoming
    attributable is a fact the session can act on immediately.

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

    source = payload.get("source")
    if isinstance(source, str) and source not in SYNC_ON_SOURCES:
        return 0

    token = (
        os.environ.get(TOKEN_ENV) or os.environ.get(TOKEN_ENV_FALLBACK) or ""
    ).strip()
    if not token:
        # Silent, deliberately. `session_start_credential_check.py` is the file
        # that reports a missing credential, and it runs on this same event —
        # two hooks saying the same thing is how a real notice gets tuned out.
        return 0

    cwd = payload.get("cwd")
    agents, overflowed = _discover(cwd if isinstance(cwd, str) else "")
    if not agents:
        return 0

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
