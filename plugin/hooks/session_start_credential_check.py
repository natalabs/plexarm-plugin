#!/usr/bin/env python3
"""Plexarm — the SessionStart credential check. It exists because the guard
died for two days and nothing said a word.

MEASURED 2026-08-10. `stop_record_guard.py` logged `not-checked:no-credential`
**407 times in one day**, with the plugin installed, enabled and validating
perfectly. Zero successful checks between 2026-08-08T16:54Z and
2026-08-10T15:24Z. Nobody noticed, and nobody could have: the guard fails open
by design, so there is no symptom for the user, and server-side the calls simply
stop, which is indistinguishable from *"the plugin was uninstalled"* or *"no
sessions ended today"*. The heartbeat that exists to separate those needs the
same credential, so it went quiet too.

THE ROOT CAUSE IS NOT OURS TO FIX, WHICH IS EXACTLY WHY THIS FILE EXISTS.
The credential is stored by the client in the macOS keychain item
`Claude Code-credentials` — writing the plugin's config moves that item's
modification date to the second. **That item was created fresh at
2026-08-10T05:45:20Z**, and a creation date means the previous item was
destroyed; whatever it held went with it. The first `no-credential` line that
day is 05:44:50Z. We do not control that store, we cannot subscribe to it
changing, and a user who re-authenticates, upgrades or resets the client can
empty it at any time without doing anything wrong.

So the property this file provides is not *"the credential is always there"* —
that is not available. It is **"when it is not there, you find out in the next
session rather than in two days."**

────────────────────────────────────────────────────────────────────────────────
WHY `SessionStart` STDOUT, AND NOT ANY OF THE OBVIOUS ALTERNATIVES
────────────────────────────────────────────────────────────────────────────────
- **Not `systemMessage` from the Stop hook.** Tried first, 2026-08-10, and
  **measured NOT to arrive**: a Stop hook that printed
  `{"systemMessage": "..."}` fired (it wrote a marker file) and the string
  reached neither the transcript nor `--output-format stream-json`. A channel
  that is silent when it fails is the exact bug being fixed.
- **Not blocking.** Turning a missing credential into a block would make a
  broken credential stop the user working. That is worse than silence, and it
  contradicts the whole design of `stop_record_guard.py`, where failing open is
  structural.
- **Not a network call.** There is nothing to call *with* — that is the failure.
  And `README.md` states exactly what this plugin sends; this file adds nothing
  to it and must never start.
- **`SessionStart` stdout is injected into the model's context** — verified by
  running it, not from documentation: a probe line asking the model to echo a
  token came back echoed. The model then tells the user, which is the only path
  from here to a human that exists.

  ⚠️ **IN AN INTERACTIVE SESSION. IT IS NOT DELIVERED UNDER `claude -p`, AND
  THAT SENTENCE DID NOT SAY SO UNTIL 2026-08-14.** Measured that day, three
  runs: the hook fires and emits the notice correctly — `--output-format
  stream-json` carries the full `additionalContext` in a `hook_response` system
  event — but **no `user` message ever carries it into the conversation**, and a
  headless model asked twice to echo any sentence containing `CREDENTIAL`
  replied `NONE` both times. Interactive delivery is confirmed the same day by
  direct observation of a real session's context.

  So the one path from here to a human **does not exist for a headless agent**:
  cron runs, CI, `-p` automation and anything driving the SDK get a guard that
  is off with no notice at all. That is this file's own two silent days, scoped
  down to a surface nobody has checked. It is recorded here rather than fixed
  because there is nothing in this hook's contract to fix — `SessionStart`
  stdout is the channel the client offers — and a second channel is the same
  decision that `stop_record_guard.py`'s `systemMessage` attempt already lost.

────────────────────────────────────────────────────────────────────────────────
IT IS SILENT WHEN HEALTHY, AND LOUD EVERY SESSION WHEN NOT
────────────────────────────────────────────────────────────────────────────────
A credential that is present costs nothing: no output, no context, no bytes in
anybody's window. A credential that is missing prints on **every** session until
it is fixed — deliberately not once a day, because a rate limit re-introduces
exactly the window this file exists to close, and because the noise stops the
moment the thing is repaired. If that is annoying, it is annoying about
something that is broken.

⚠️ IT CHECKS PRESENCE, NOT VALIDITY, AND SAYS SO IN ITS OWN NOTICE. A token that
exists but has been revoked produces `not-checked:http 401` in the guard's
`outcomes.log` — a different failure with a different signature, not this one.
Verifying validity here would mean a network call at the start of every session,
which is a much larger promise than this file is allowed to make.
"""

from __future__ import annotations

import json
import os
import sys

#: The variable Claude Code populates from the plugin's `userConfig.api_token`.
#: ⚠️ IT IS RETYPED FROM `stop_record_guard.py` RATHER THAN IMPORTED, and that
#: is not laziness: a hook runs as a bare script under whatever interpreter the
#: client picks, with no package root, so an import across these two files would
#: be a runtime failure on a customer's machine rather than a shared constant.
#: `gate_57` asserts the two spellings are identical, which is the enforceable
#: form of the thing an import would have given us.
#:
#: ⛔ PRIMARY IS `PLEXARM_TOKEN` AS OF 1.4.1 — see the long note in
#: `stop_record_guard.py` for why, in full. Short form: the plugin option is
#: stored in `Claude Code-credentials`, a shared blob the client REBUILDS on its
#: own ~8-hourly OAuth refresh, dropping tenants it does not know about
#: (upstream `anthropics/claude-code` #62442, closed as not planned).
#: `PLEXARM_TOKEN` comes from a keychain item Plexarm owns.
#:
#: ⚠️ ENVIRONMENT FIRST. The reverse order lets a stale plugin-option value beat
#: a good environment one and produce a false NO-CREDENTIAL notice — which is
#: precisely this file's own failure mode and would be invisible.
TOKEN_ENV = "PLEXARM_TOKEN"
TOKEN_ENV_FALLBACK = "CLAUDE_PLUGIN_OPTION_API_TOKEN"

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
# ─────────────────────────────────────────────────────────────────────────────
HOOK_NAME = "session_start_credential_check"
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


#: What the model is told when the credential is absent. TWO SENTENCES, and the
#: second is the one doing the work — an agent that merely knows the guard is
#: off will not mention it, and the user is the only one who can repair it.
#:
#: ⚠️ The repair command is spelled out in full. *"Reconfigure the plugin"* is
#: what the operator was told on 2026-08-10 and the honest reply was *"I don't
#: know what that means"* — a remedy nobody can execute is not a remedy.
#:
#: ⛔ THE UNINSTALL IS NOT OPTIONAL, AND OMITTING IT IS WHY THIS NOTICE RAN FOR
#: FOUR DAYS WITHOUT REPAIRING ANYTHING. Measured 2026-08-14 against the
#: installed plugin: `claude plugin install plexarm@plexarm --scope user
#: --config api_token=…` on an ALREADY-INSTALLED plugin prints
#: `✔ Plugin "plexarm@plexarm" is already installed (scope: user)`, **exits 0**,
#: and never reaches the step that stores `--config`. Nothing is written; the
#: keychain item is not touched; `installed_plugins.json` gains no config key.
#: The command reports success with a tick and does nothing, which is the worst
#: shape a remedy can have — the agent tells the user it is fixed and the next
#: session prints this notice again.
#:
#: Uninstall-then-install was then measured end to end: settings.json returns
#: byte-identical (`enabledPlugins` is dropped by the uninstall and restored by
#: the install), and a fresh headless session logged `heartbeat=401` against a
#: deliberately bogus token — a 401 proves the credential RESOLVED and was sent,
#: where the broken path logs `no-credential`. That contrast is the check.
#:
#: ⚠️ It must stay ONE shell line joined by `&&`. Two lines pasted separately is
#: how a user ends up uninstalled with no credential at all if the second is
#: mistyped, and `&&` means a failed uninstall stops rather than proceeding.
#:
#: ⚠️ `/plugin configure` LEADS, AND IT IS THE CLIENT'S OWN RECOMMENDATION
#: RATHER THAN OURS. Installing without a value prints: *"1 userConfig option
#: not yet set (1 required) — run `/plugin configure plexarm@plexarm` in Claude
#: Code, or pass `--config KEY=VALUE`."* It is one step, it does not touch
#: `settings.json`, and it cannot leave the user uninstalled if they stop
#: half-way. **It was not verified from here** — an interactive slash command
#: cannot be driven from a hook's test — so the shell line, which WAS measured
#: end to end, is given second rather than dropped. Naming only the unverified
#: one would repeat this file's own bug in the other direction.
NOTICE = (
    "Plexarm's record guard is installed but has NO CREDENTIAL, so it is "
    "checking nothing and cannot tell anyone when work is left open — this "
    "happens on its own when the client's stored credentials are reset, and it "
    "has no other symptom. Say so in your first reply, and give BOTH of these "
    "(they are alternatives; the first is one step): (1) run `/plugin configure "
    "plexarm@plexarm` in Claude Code and paste a token from "
    "https://plexarm.com/me; or (2) in a terminal, as ONE line — `claude plugin "
    "uninstall plexarm@plexarm --scope user && claude plugin install "
    "plexarm@plexarm --scope user --config api_token=<that token>`. In (2) the "
    "uninstall is REQUIRED: `claude plugin install` on an already-installed "
    "plugin prints a green `already installed`, exits 0 and silently discards "
    "`--config`, so the install on its own reports success and repairs nothing. "
    "(This checks only that a credential is present, not that it still works.)"
)


def main() -> int:
    """Print at most one object, and **always exit 0**.

    Same door as `stop_record_guard.py`: every failure — unreadable stdin, a
    disk error, an exception nobody thought of — leaves through the
    `except BaseException` and takes the silent path. A `SessionStart` hook
    cannot block a session, but it can waste one by printing garbage where
    context belongs, and there is exactly one `print` in this program.
    """
    try:
        try:
            sys.stdin.read()
        except BaseException:  # noqa: BLE001 - draining stdin must never decide anything
            pass

        if not switched_on(HOOK_NAME, os.environ):
            return 0

        if (
            os.environ.get(TOKEN_ENV) or os.environ.get(TOKEN_ENV_FALLBACK) or ""
        ).strip():
            return 0

        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": NOTICE,
                    }
                }
            )
        )
        return 0
    except BaseException:  # noqa: BLE001 - see the docstring; this IS the control
        return 0


if __name__ == "__main__":
    sys.exit(main())
