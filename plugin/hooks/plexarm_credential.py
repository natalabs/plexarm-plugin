#!/usr/bin/env python3
"""Plexarm — THE credential resolver. One order, written down once.

────────────────────────────────────────────────────────────────────────────────
WHY THIS FILE EXISTS, MEASURED
────────────────────────────────────────────────────────────────────────────────
`fnd-…34b6eb`, 2026-09-09, launch day. A second Plexarm account was created and
`claude mcp add … --header "Authorization: Bearer <that token>"` was run from a
project directory, following `/connect`'s Claude Code snippet. That wrote a
**project-scoped `plexarm` server carrying the expanded token** into
`~/.claude.json`. The same machine exports `PLEXARM_TOKEN` from a keychain item,
which is what `.mcp.json` and every hook read.

In the next session in that directory: `plexarm_whoami` answered as the SECOND
account with an empty board, while the Stop hook — reading the environment —
listed three `in_progress` items belonging to the FIRST account and told the
agent they were *"moved by this USER"*. Nothing was written to the wrong account
because the agent noticed and declined. **The product must not depend on that.**

Two credentials on one machine reached the model through two different paths.
This module is the first half of the fix: **one function, one order, both
hooks.** The second half is `session_start_credential_check.py`, which compares
what the TOOLS will use against what the HOOKS hold and refuses the session when
those are different accounts.

────────────────────────────────────────────────────────────────────────────────
THE ORDER, AND WHY IT IS THIS WAY ROUND
────────────────────────────────────────────────────────────────────────────────
1. **`PLEXARM_TOKEN`** — the environment. Primary since 1.4.1.
2. **`CLAUDE_PLUGIN_OPTION_API_TOKEN`** — the plugin's `userConfig.api_token`,
   the compatibility tail.

⛔ **ENVIRONMENT FIRST IS NOT A PREFERENCE.** The plugin option is stored by the
client in the shared `Claude Code-credentials` keychain item, which the client
**rebuilds on its own ~8-hourly OAuth refresh** and drops tenants it does not
know about — `anthropics/claude-code` #62442, closed as not planned. A stale
value there beating a good environment one produces a false NO-CREDENTIAL notice,
which is the failure `session_start_credential_check.py` exists to prevent,
arriving inside the file that prevents it.

⚠️ **THERE IS NO THIRD SOURCE, AND A REPOSITORY MUST NEVER BECOME ONE.** A repo
can set environment variables for hooks through `.claude/settings.json`, so any
variable this file reads is repo-supplied by a longer pipe. `gate_57` asserts the
set of environment names reachable from the guard — including through this
module — is exactly these two plus the log path.

────────────────────────────────────────────────────────────────────────────────
IT IS IMPORTED BY A SIBLING PATH, AND THE CALLERS DO NOT TRUST THE IMPORT
────────────────────────────────────────────────────────────────────────────────
`gate_57`'s constant block said the two hooks *"cannot import from each other: a
hook runs as a bare script with no package root"*. **That is half right, and the
half that is wrong is the half that mattered.** Python puts the script's own
directory on `sys.path` at position 0, so a sibling import resolves — measured,
by running both hooks from a temporary directory with no package anywhere.

What does not follow is that the import is SAFE to depend on. `stop_record_guard.py`
blocks on a non-zero exit, and an `ImportError` at module scope escapes before
its `main()` ever runs, so it imports this module inside a `try` and retypes the
order as a fallback. **That is not two orders**: `gate_57` asserts the fallback
and this module agree, which is the enforceable form of the thing an import was
supposed to give us. `session_start_credential_check.py` does the same for the
same reason, one consequence lighter.
"""

from __future__ import annotations

#: The variable Plexarm owns. Primary. See the order above.
TOKEN_ENV = "PLEXARM_TOKEN"

#: The plugin's `userConfig.api_token`, populated by Claude Code. Compatibility
#: tail only — the client's keychain rebuild drops it without warning.
TOKEN_ENV_FALLBACK = "CLAUDE_PLUGIN_OPTION_API_TOKEN"


def resolve_token(environ) -> str | None:
    """The credential this machine's hooks hold, or `None`.

    `environ` is passed in rather than read from `os` so that every caller's
    tests can run with a constructed environment and reach the live API by
    accident in neither direction. `gate_57`'s runners pop BOTH names — a
    developer's real `PLEXARM_TOKEN` leaking in through `dict(os.environ)` is a
    green that depends on whose laptop it ran on, and it has happened once.

    ⚠️ **A WHITESPACE-ONLY VALUE IS NOT A CREDENTIAL.** `PLEXARM_TOKEN=""` and
    `PLEXARM_TOKEN=" "` are what a half-finished shell profile leaves behind,
    and treating either as present sends an empty bearer and gets a 401 whose
    signature says *"revoked"* rather than *"never set"*.

    ⛔ **THE TWO READS ARE WRITTEN OUT AND NOT LOOPED, AND THAT IS FOR THE
    GATE.** `gate_57` §5 walks this source for `environ.get(...)` calls and
    resolves a `Name` target against the module's globals — a loop variable
    resolves to nothing, so a `for name in (...)` form would make the
    environment allowlist pass by seeing NOTHING rather than by seeing only
    what is allowed. A check that cannot fail is the shape §26 is about.
    """
    value = (environ.get(TOKEN_ENV) or "").strip()
    if value:
        return value
    value = (environ.get(TOKEN_ENV_FALLBACK) or "").strip()
    if value:
        return value
    return None
