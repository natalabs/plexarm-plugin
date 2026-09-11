#!/usr/bin/env python3
"""Plexarm — THE credential resolver. One order, written down once.

────────────────────────────────────────────────────────────────────────────────
WHY THIS FILE EXISTS, MEASURED
────────────────────────────────────────────────────────────────────────────────
`fnd-…34b6eb`, 2026-09-09, launch day. A second Plexarm account was created and
`claude mcp add … --header "Authorization: Bearer <that token>"` was run from a
project directory, following `/connect`'s Claude Code snippet. That wrote a
**project-scoped `plexarm` server carrying the expanded token** into
`~/.claude.json`, while the same machine's hooks read a different credential.

In the next session in that directory: `plexarm_whoami` answered as the SECOND
account with an empty board, while the Stop hook listed three `in_progress`
items belonging to the FIRST account and told the agent they were *"moved by
this USER"*. Nothing was written to the wrong account because the agent
noticed. **The product must not depend on that.**

This module is the first half of the fix: **one function, one order, both
hooks.** The second half is `session_start_credential_check.py`, which compares
what the TOOLS will use against what the HOOKS hold.

────────────────────────────────────────────────────────────────────────────────
THE ORDER, AND WHY THE STORE IS FIRST — 1.7.0
────────────────────────────────────────────────────────────────────────────────
1. **The OS credential store** — macOS Keychain, then Linux Secret Service,
   then a 0600 file owned by the caller.
2. **`PLEXARM_TOKEN`** — the environment. Second, and kept.

⛔ **STORE-FIRST IS NOT A PREFERENCE, IT IS WHAT KEEPS THE HOOKS AND THE TOOLS
ON ONE CREDENTIAL — AND IT IS LOAD-BEARING FROM 1.7.0 ONWARD.**

In 1.6.x the plugin's `.mcp.json` sent `Bearer ${PLEXARM_TOKEN}`: the tools and
the hooks agreed **by construction**, because the header was literally the
hooks' first read. It was never a comparison. `session_start_credential_check`'s
`_config_sites()` has never listed the plugin's own `.mcp.json` and still does
not — it cannot see the plugin entry at all, so it could never have noticed a
divergence there.

1.7.0 removes that static header. The MCP tools now read **the store and
nothing else** (`bin/plexarm-headers`; the client scrubs credential-shaped
variables out of a plugin helper's environment, so it could not read
`PLEXARM_TOKEN` even if it wanted to). If the hooks kept reading the
environment first, then on the machine that motivated this whole redesign —
measured 2026-09-11, `PLEXARM_TOKEN` sha256[:12] `fc72e2ef306d` against a
Keychain item at `899c601a1fa3`, a GUI-launched editor holding a shell snapshot
taken before a rotation — **the tools would authenticate as one credential and
the guard as another, with every gate green.** Silence is what success looks
like for a guard, so nobody would notice.

⚠️ **THE ENVIRONMENT IS STILL READ, AND THAT IS NOT THE FALLBACK `coding-rules`
#2 FORBIDS.** A forbidden fallback sits in the SAME channel as the primary and
makes a broken primary invisible. This one is a different channel (hooks, not
headers), it is announced by the SessionStart notice whenever the store is
empty, and it cannot mask an MCP failure because the MCP side has no
environment path to fall back to. Without it every containerised and CI install
breaks on upgrade.

⛔ **`CLAUDE_PLUGIN_OPTION_API_TOKEN` IS GONE AS OF 1.7.0.** Values given to
`userConfig.api_token` live in the client's shared `Claude Code-credentials`
keychain item, which the client rebuilds on its own ~8-hourly OAuth refresh and
which drops tenants it does not know about (`anthropics/claude-code` #62442,
closed as not planned — so permanent). Measured 2026-09-11: the client writes
that item **through `argv`** once the payload passes ~2 KB
(`[WARN] Keychain payload (2285B JSON) exceeds security -i stdin limit; using
argv`), and `ps` is world-readable. A third resolution order, a third thing to
reconcile, and process-table exposure we control by not contributing to.

⚠️ **THERE IS NO OTHER SOURCE, AND A REPOSITORY MUST NEVER BECOME ONE.** A repo
can set environment variables for hooks through `.claude/settings.json`, so any
variable this file reads is repo-supplied by a longer pipe. That is why the
token FILE's path is **not** read from the environment — not even from `HOME`.
A variable naming the file is not token theft, it is account substitution: the
hooks authenticate as somebody else's account and the user's work is recorded
into it. The home directory comes from the password database instead.
`gate_57` §3b asserts the set of environment names reachable from the guard —
including through this module — is exactly `PLEXARM_TOKEN` and the log path.

────────────────────────────────────────────────────────────────────────────────
THE STORE READ EXECS, AND EVERY EXEC IS ABSOLUTE WITH A SCRUBBED ENVIRONMENT
────────────────────────────────────────────────────────────────────────────────
The hooks' own `PATH` is repo-controlled — measured, same mechanism as above —
so a `shell=True` or PATH-resolved `security` here is the exfiltration the
`bin/plexarm-headers` hardening exists to prevent, rewritten in Python. Every
call passes an absolute path, `env={"PATH": …}`, no shell, and a timeout: a
locked login keychain can block on an unlock prompt, and a hook that hangs in
front of a person is its own defect.

────────────────────────────────────────────────────────────────────────────────
IT IS IMPORTED BY A SIBLING PATH, AND THE CALLERS DO NOT TRUST THE IMPORT
────────────────────────────────────────────────────────────────────────────────
Python puts the script's own directory on `sys.path[0]`, so a sibling import
resolves — measured, by running both hooks from a temporary directory with no
package anywhere. What does not follow is that the import is SAFE to depend on.
`stop_record_guard.py` blocks on a non-zero exit, and an `ImportError` at module
scope escapes before its `except BaseException` ever runs, so it imports this
module inside a `try` and retypes the order as a fallback. **That is not two
orders**: `gate_217` §6 asserts all four copies — this module, the two retyped
fallbacks, and `bin/plexarm-headers` — read in the same order, and it asserts
the READS rather than the definitions, because asserting the definitions was
measured blind.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess

#: The variable Plexarm owns. Second, and the only environment name read.
TOKEN_ENV = "PLEXARM_TOKEN"

#: The Keychain / Secret Service item. `bin/plexarm-headers` carries the same
#: string; an item stored for one is found by the other.
STORE_SERVICE = "plexarm-api-token"

#: Relative to the caller's home. Deliberately not configurable — see the
#: account-substitution paragraph above.
STORE_FILE = ".config/plexarm/token"

#: A locked keychain can block on a GUI unlock prompt. The SessionStart hook
#: runs in front of a person; three seconds is already long there.
STORE_TIMEOUT_SECONDS = 3.0

#: Only the caller may read it. 0640 is a group-readable credential and this
#: machine's default umask (022) produces 0644 from the obvious command, so the
#: refusal is not theatre.
_ALLOWED_FILE_MODES = frozenset({0o600, 0o400})


def _home() -> str:
    """The caller's home from the password database, NOT from `$HOME`.

    A repository can set `HOME` for a hook through `.claude/settings.json`, and
    a redirected `HOME` chooses which token file is read — account
    substitution. `pwd` answers from the passwd entry for the running uid,
    which no environment variable can move.

    The import is function-local on purpose: `pwd` does not exist on Windows,
    and an `ImportError` at module scope in a blocking hook is a traceback at
    the end of every session.
    """
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError, OSError):
        # No passwd entry and no `pwd` module both mean the same thing: this
        # platform has no file store to read. It is one of three sources, not
        # required data, so the next source is tried rather than raising —
        # and on Windows there is no POSIX home to find.
        return ""


def _file_token() -> str | None:
    """The 0600 file, or `None`. Refuses rather than reads a loose one."""
    home = _home()
    if not home:
        return None
    path = pathlib.Path(home) / STORE_FILE
    try:
        info = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    if stat.S_IMODE(info.st_mode) not in _ALLOWED_FILE_MODES:
        return None
    if info.st_uid != os.getuid():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _keychain_token() -> str | None:
    """The OS keychain, or `None`.

    ⚠️ Absolute path, no shell, scrubbed environment, hard timeout. The hooks'
    PATH is repo-controlled; a PATH-resolved `security` here is the measured
    `bin/` exfiltration rewritten in Python.
    """
    if os.path.exists("/usr/bin/security"):
        command = ["/usr/bin/security", "find-generic-password", "-s", STORE_SERVICE, "-w"]
    elif os.path.exists("/usr/bin/secret-tool"):
        command = ["/usr/bin/secret-tool", "lookup", "service", STORE_SERVICE]
    else:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - absolute path, no shell, fixed argv
            command,
            capture_output=True,
            text=True,
            timeout=STORE_TIMEOUT_SECONDS,
            env={"PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # A store that is absent, broken, or slow answers "not here", and the
        # next source is tried. ⛔ THIS MUST NOT RAISE: `stop_record_guard.py`
        # BLOCKS THE SESSION on a non-zero exit, so an exception escaping this
        # module would turn a missing keychain into a blocked agent.
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def store_token() -> str | None:
    """The OS credential store, or `None`. Reads no environment variable."""
    return _keychain_token() or _file_token()


def resolve_token_with_source(environ) -> tuple[str | None, str | None]:
    """`(credential, where it came from)` — `("…", "store")`, `("…",
    "environment")`, or `(None, None)`.

    The source is returned because `session_start_credential_check.py` must
    tell the user something different when the store is empty AND the
    environment rescued the hooks: the MCP tools read the store only, so in
    that state the hooks work and `plexarm_*` does not connect. Reading the
    store twice to find that out would be two chances to block on a keychain
    unlock prompt, so the one read answers both questions.

    ⛔ **THE TWO READS ARE WRITTEN OUT AND NOT LOOPED, AND THAT IS FOR THE
    GATE.** `gate_57` §3b walks this source for `environ.get(...)` and resolves
    a `Name` target against the module's globals — a loop variable resolves to
    nothing, so a `for name in (...)` form would make the environment allowlist
    pass by seeing NOTHING rather than by seeing only what is allowed. A check
    that cannot fail is worse than no check.

    ⚠️ **A WHITESPACE-ONLY VALUE IS NOT A CREDENTIAL.** `PLEXARM_TOKEN=""` and
    `PLEXARM_TOKEN=" "` are what a half-finished shell profile leaves behind,
    and treating either as present sends an empty bearer and gets a 401 whose
    signature says *"revoked"* rather than *"never set"*.
    """
    value = store_token()
    if value:
        return value, "store"
    value = (environ.get(TOKEN_ENV) or "").strip()
    if value:
        return value, "environment"
    return None, None


def resolve_token(environ) -> str | None:
    """The credential this machine's hooks hold, or `None`.

    `environ` is passed in rather than read from `os` so that every caller's
    tests can run with a constructed environment and reach the live API by
    accident in neither direction. `gate_57`'s runners pop the name — a
    developer's real `PLEXARM_TOKEN` leaking in through `dict(os.environ)` is a
    green that depends on whose laptop it ran on, and it has happened once.
    """
    token, _ = resolve_token_with_source(environ)
    return token
