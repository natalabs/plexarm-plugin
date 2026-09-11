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
- **Not a network call, for the missing-credential notice.** There is nothing
  to call *with* — that is the failure — so the notice below is still emitted
  from the environment alone, with no bytes leaving the machine.

  ⛔ **THAT SENTENCE USED TO END *"this file adds nothing to what the plugin
  sends and must never start"*, AND 1.6.1 MADE IT FALSE.** It is corrected
  rather than deleted, because a README that quietly stops being true is the
  defect this plugin's disclosure section exists to prevent. Since 1.6.1 this
  file makes **at most one `GET https://api.plexarm.com/records/identity` per
  distinct token it can see**, and **only when it can see more than one** —
  see the second half of this docstring. `plugin/README.md` discloses it and
  names 1.6.1; `gate_57` asserts that it does.
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

════════════════════════════════════════════════════════════════════════════════
1.6.1 — THE SECOND JOB: TWO CREDENTIALS ON ONE MACHINE, AND A REFUSAL
════════════════════════════════════════════════════════════════════════════════
`itm-…87b10e`, from `fnd-…34b6eb` measured 2026-09-09 on launch day.

A second Plexarm account was created and `/connect`'s Claude Code snippet was
run from a project directory: `claude mcp add … --header "Authorization: Bearer
<the new token>"`. **The shell expands the variable before Claude sees it**, so
that wrote a project-scoped `plexarm` server carrying a LITERAL token into
`~/.claude.json`. The same machine exports `PLEXARM_TOKEN` from a keychain item,
which is what the plugin's `.mcp.json` and all four hooks read.

In the next session in that directory: `plexarm_whoami` answered as the **second**
account with no projects and an empty board, while `stop_record_guard.py` —
reading the environment — listed three `in_progress` items belonging to the
**first** account and told the agent they had been *"moved by this USER"*. The
agent declined to touch them and flagged the mismatch, and that is the only
reason nothing was written to the wrong account.

**It is not a breach.** Both credentials belonged to the same person, on her own
machine, and each path used the token it was configured with. It is a product
gap with a security shape: somebody who works for two companies from one laptop
can have the TOOL surface bound to one account and the HOOK surface to the
other, and the hook then puts the other account's item titles into a session
that is not authorised for them.

────────────────────────────────────────────────────────────────────────────────
WHAT THIS FILE DOES ABOUT IT, AND WHAT IT DELIBERATELY DOES NOT
────────────────────────────────────────────────────────────────────────────────
1. It resolves the credential the HOOKS hold, through `plexarm_credential.py` —
   **the one resolver, the one order**, shared with `stop_record_guard.py`.
2. It reads the config files Claude Code itself reads for `mcpServers` entries
   and pulls out every `plexarm` server's bearer token, expanding `${VAR}` forms
   from the same environment the client would.
3. **If every token it can see is the same string, it stops there and prints
   nothing. No network call, no context, no bytes.** That is the overwhelmingly
   common case and it must stay free — see the paragraph above about a notice
   that fires when things are fine.
4. Only when it sees a token DIFFERENT from the hooks' does it ask
   `GET /records/identity` — once per distinct token, cached for the session —
   for the two slugs behind each.
5. **If the accounts differ, it refuses the session in one sentence naming both
   account slugs and the file the second entry lives in.**

⛔ **STEP 4 IS NOT AVOIDABLE BY COMPARING TOKEN STRINGS, AND THE TEMPTATION TO
TRY IS THE WHOLE REASON THIS PARAGRAPH IS HERE.** Two different tokens can
belong to the same account — a laptop token and a CI token, or one rotated an
hour ago — so a string comparison refuses sessions where nothing is wrong. A
hook that stops somebody working over a false positive is worse than the gap it
closes, and it is the failure mode this file's own header spends forty lines
forbidding.

⚠️ **THE REFUSAL NAMES SLUGS AND A FILE PATH AND NOTHING ELSE.** It does not name
the other account's items, or how many there are. Whether a hook should ever put
another account's work into a session at all is a live question on the item and
is NOT answered here; keeping the refusal to two slugs and a path is what makes
this change safe to ship before that question is settled.

⚠️ **WHETHER `"continue": false` ACTUALLY HALTS A `SessionStart` SESSION IS NOT
MEASURED FROM HERE, AND SAYING SO IS THE POINT.** The refusal is emitted BOTH as
`continue`/`stopReason` and as `additionalContext`, because `additionalContext`
is the channel this file has verified by running it (see above) and the other is
the one the client documents. If only the second works, the refusal is an
instruction to a model rather than a stop — which is weaker, and is what the
sentence tells the model to do. **It is also not delivered under `claude -p` at
all**, exactly as the missing-credential notice is not: a headless agent with two
credentials gets no warning, on the same terms and for the same reason.

⚠️ **IT FAILS OPEN ON EVERYTHING EXCEPT A MEASURED MISMATCH.** An unreadable
config file, a token it cannot expand, a network error, a 401, a 404 from an API
older than this hook — every one of those leaves silently. The only path to a
refusal is two successful identity reads that disagree.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import sys
import tempfile
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# THE ONE RESOLVER. See `plexarm_credential.py` for the order and for why the
# import is wrapped rather than trusted.
#
# ⛔ THE FALLBACK IS NOT A SECOND ORDER. It is the same order, retyped, so that a
# broken install degrades to the 1.5.1 behaviour instead of printing a traceback
# where a session's context belongs. `gate_57` asserts the two agree — the
# enforceable form of the thing the import was supposed to give us.
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
#: ⚠️ The repair is spelled out in full. *"Reconfigure the plugin"* is what the
#: operator was told on 2026-08-10 and the honest reply was *"I don't know what
#: that means"* — a remedy nobody can execute is not a remedy.
#:
#: ⛔ THE 1.6.x REMEDIES ARE GONE AND MUST NOT COME BACK. They named
#: `/plugin configure plexarm@plexarm` and
#: `claude plugin install … --config api_token=…`, which wrote into the
#: client's shared `Claude Code-credentials` item. 1.7.0 removes
#: `userConfig.api_token` entirely, so both commands now repair **nothing** —
#: and the second one measured worse than nothing on 2026-08-14: run against
#: an already-installed plugin it prints a green `already installed`, exits 0
#: and silently discards `--config`. A remedy that reports success and changes
#: nothing is the worst shape a remedy can have. `gate_57` asserts the store
#: command is named here and that `--config api_token=` is not.
#:
#: ⛔ AND THE NOTICE IS DRIVEN OFF THE STORE, NOT OFF WHAT THESE HOOKS
#: RESOLVED. From 1.7.0 the MCP tools read the credential store and nothing
#: else, while these hooks fall back to `PLEXARM_TOKEN`. A machine with an
#: empty store and a good environment variable therefore has WORKING HOOKS and
#: DEAD TOOLS — and a notice keyed on the hooks' own resolution would stay
#: silent through exactly that. `STORE_EMPTY` below is that case; `NOTICE` is
#: the case where there is nothing anywhere.
#:
#: ⚠️ Both name the same three commands in the same order — Keychain, Secret
#: Service, file — because that is the order `bin/plexarm-headers` reads them
#: in, and a notice that listed them differently would teach the wrong model.
#: All three are the interactive-prompt form: a token typed after the prompt
#: does not enter the shell's history, and `-w` with no value is what makes
#: `security` prompt.
_STORE_COMMANDS = (
    "macOS: `security add-generic-password -a \"$USER\" -s plexarm-api-token -w` "
    "(press return, then paste the token at the prompt); "
    "Linux: `secret-tool store --label=\"Plexarm API token\" service plexarm-api-token`; "
    "or a file: `umask 077; mkdir -p ~/.config/plexarm; read -rs t && printf %s \"$t\" "
    "> ~/.config/plexarm/token`. Create a token at https://plexarm.com/me; it is shown once."
)

_RECONNECT = (
    "Then reconnect: `/mcp` -> plexarm -> Reconnect, or start a new session. "
    "The helper runs once per connect, so a session already running keeps what "
    "it started with."
)

NOTICE = (
    "Plexarm has NO CREDENTIAL on this machine: the credential store is empty "
    "and PLEXARM_TOKEN is not set. So the `plexarm_*` MCP tools will not "
    "connect this session, and the record guard is checking nothing and cannot "
    "tell anyone when work is left open — which has no other symptom. Say so "
    "in your first reply and give the user this: store the token once — "
    + _STORE_COMMANDS
    + " "
    + _RECONNECT
    + " (This checks only that a credential is present, not that it still works.)"
)

STORE_EMPTY = (
    "Plexarm's credential store is EMPTY on this machine. The hooks are "
    "working — they fell back to PLEXARM_TOKEN — but the `plexarm_*` MCP tools "
    "read the store and nothing else, so they will NOT connect this session "
    "and no Plexarm tool call will succeed. Say so in your first reply and "
    "give the user this: store the same token once — "
    + _STORE_COMMANDS
    + " "
    + _RECONNECT
    + " Once it is stored you can drop the PLEXARM_TOKEN export; the hooks read "
    "the store first."
)


#: Where the identity read goes. Compiled in, never read from a repository — a
#: repo can set environment variables for hooks through `.claude/settings.json`,
#: so a host override would be credential exfiltration through a longer pipe.
#: `stop_record_guard.py` carries the identical constant for the identical
#: reason and `gate_57` asserts it there.
API_HOST = "api.plexarm.com"
API_PATH = "/records/identity"

#: Two timeouts, separately, because they fail at different times and one number
#: cannot express both. Deliberately tighter than the guard's: this runs at the
#: START of a session, where every millisecond is a person waiting.
CONNECT_TIMEOUT_SECONDS = 2.0
READ_TIMEOUT_SECONDS = 3.0

#: The server name Claude Code would bind our tools to. A second entry under a
#: DIFFERENT name pointing at our API is caught too — see `_plexarm_servers`.
SERVER_NAME = "plexarm"


#: What the model is told when the tools and the hooks belong to DIFFERENT
#: ACCOUNTS. It is built rather than constant because the two slugs and the file
#: path are the entire content — a refusal that said *"two accounts"* without
#: naming them leaves the reader to guess which entry to remove, and the whole
#: measured failure was somebody not knowing there were two.
#:
#: ⛔ IT NAMES SLUGS AND A PATH AND NOTHING ELSE. Not the other account's items,
#: not a count of them. Whether a hook should put another account's work into a
#: session at all is an open question on `itm-…87b10e` and is not answered here.
#:
#: ⚠️ The instruction to the agent is the second half and it is load-bearing on
#: exactly the terms `NOTICE`'s second sentence is: if `"continue": false` does
#: not halt a `SessionStart` session, this sentence is the only thing standing
#: between the divergence and a record written to the wrong account.
def refusal(mine: str, theirs: str, source: str) -> str:
    """One sentence, both account slugs, and the file the second entry is in."""
    return (
        f"Plexarm REFUSES this session: the `plexarm` MCP server Claude Code "
        f"will use here is configured in {source} with a token for account "
        f"`{theirs}`, while this plugin's hooks hold a token for account "
        f"`{mine}` — two accounts in one session means work you record can land "
        f"in the wrong one and this session's hooks can show you the other "
        f"account's open work, so stop now: tell the user this sentence, call "
        f"no Plexarm tool and record nothing, and have them either delete that "
        f"`plexarm` entry from {source} or set {TOKEN_ENV} to a token for "
        f"`{theirs}` before starting again."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Session state — the cache, and the log
# ─────────────────────────────────────────────────────────────────────────────
def _state_dir(session_id: str) -> str | None:
    """`<tmp>/plexarm-hook/<sha256(session_id)>/`, created 0700, or None.

    The same directory `stop_record_guard.py` uses, deliberately: one place to
    look when somebody asks what the hooks did this session. The session id is
    **hashed, not used as a path component** — it arrives in a payload, and a
    value containing `../` would otherwise choose the directory.
    """
    try:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
        path = os.path.join(tempfile.gettempdir(), "plexarm-hook", digest)
        os.makedirs(path, mode=0o700, exist_ok=True)
        return path
    except OSError:
        return None


def _log(state: str | None, outcome: str, note: str = "") -> None:
    """Append one line. Never raises, and never writes a token.

    ⛔ NOTHING PASSED TO `note` MAY BE A CREDENTIAL OR DERIVED FROM ONE IN A
    REVERSIBLE WAY. Every caller below passes an outcome word, an account slug
    or a file path. A truncated hash of a token is fine as a cache FILENAME and
    is not written here, because a log is the artifact people paste into issues.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{stamp} credential-check:{outcome}" + (f" {note}" if note else "") + "\n"
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


def _cache_path(state: str, token: str) -> str:
    """One file per token per session.

    ⚠️ The filename is a truncated SHA-256 of the token and never the token: a
    temp directory is world-listable on most machines even when its contents are
    not, so a credential in a FILENAME is a credential in `ls`.
    """
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return os.path.join(state, f"identity-{digest}.json")


def _cached_identity(state: str | None, token: str):
    """The account this token belongs to, if this session already asked.

    ⚠️ **PER SESSION AND NEVER LONGER.** A token can be revoked and reissued to a
    different person in the same account; the session is the window in which the
    fact being compared cannot change underneath the comparison.
    """
    if not state:
        return None
    try:
        with open(_cache_path(state, token), encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (OSError, ValueError):
        return None
    account = parsed.get("account") if isinstance(parsed, dict) else None
    return account if isinstance(account, str) and account else None


def _remember_identity(state: str | None, token: str, account: str) -> None:
    """Write ONE account slug, in a file only this user can read.

    ⛔ **THE TOKEN NEVER REACHES THE DISK — NOT AS CONTENT, NOT AS A FILENAME,
    NOT IN A LOG LINE.** The filename is a truncated hash (see `_cache_path`)
    and the body is one slug. Caching the token alongside its answer is the
    obvious shape and it would put a live credential in a world-listable temp
    directory for the life of the session, to save a comparison this code
    already does in memory. `gate_217` §9 walks the cache directory after a run
    and asserts no token string appears anywhere in it, filename or content.

    ⚠️ **0600 IS SET WITH `os.open`, NOT WITH A `chmod` AFTERWARDS.** A create
    followed by a chmod leaves a window in which the file exists at whatever the
    umask allowed — short, but real, and on a shared machine the temp directory
    is exactly where somebody would look. `O_CREAT | O_WRONLY | O_TRUNC` with a
    mode is one syscall and has no window. The mode argument is masked by the
    process umask, which can only make it MORE restrictive, never less.
    """
    if not state:
        return
    try:
        descriptor = os.open(
            _cache_path(state, token),
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"account": account}, handle)
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# The one network call — `GET /records/identity`
# ─────────────────────────────────────────────────────────────────────────────
def ask_identity(token: str) -> tuple[int, str | None]:
    """`GET /records/identity`. Returns `(status, account_slug_or_None)`.

    Status 0 means no answer at all — no network, a timeout, a body that is not
    JSON. **No retry**: the failure direction is already safe, because every
    non-answer leaves this hook silent.

    ⚠️ It sends the token and a body of nothing. The response carries two slugs
    and no id — `backend/core/whoami/schemas.py::CredentialIdentity` states why
    it must never carry more, and `plugin/README.md` discloses both fields.
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
        raw = response.read(64 * 1024)
        if response.status != 200:
            return response.status, None
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            return 0, None
        if not isinstance(parsed, dict):
            return 0, None
        account = parsed.get("account")
        return 200, account if isinstance(account, str) and account else None
    except (OSError, socket.timeout, http.client.HTTPException):
        return 0, None
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001 - closing must never raise upward
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Which server entry Claude Code will actually use
# ─────────────────────────────────────────────────────────────────────────────
def _config_sites(cwd: str, environ) -> list[str]:
    """The files a `plexarm` MCP entry can live in, most global first.

    ⚠️ **THIS LIST IS A CLAIM ABOUT ANOTHER PRODUCT AND WILL GO STALE.** It is
    Claude Code's own resolution set as measured on 2026-09-09: `~/.claude.json`
    holds both the user-scope `mcpServers` map and a per-project one under
    `projects.<absolute cwd>`, a repository may ship `.mcp.json`, and
    `.claude/settings.json` is read for completeness because the item named it.
    **Being wrong here fails SILENT and SAFE** — an entry in a file not listed
    is simply not compared, and this hook stays quiet. It never fails loud.

    `CLAUDE_CONFIG_DIR` is honoured because it is how a clean-config install is
    tested, and a check that could not run under the harness that proves installs
    work would be a check nobody could exercise.
    """
    sites: list[str] = []
    config_dir = (environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    if config_dir:
        sites.append(os.path.join(config_dir, ".claude.json"))
    else:
        home = os.path.expanduser("~")
        if home and home != "~":
            sites.append(os.path.join(home, ".claude.json"))
    if cwd:
        sites.append(os.path.join(cwd, ".mcp.json"))
        sites.append(os.path.join(cwd, ".claude", "settings.json"))
        sites.append(os.path.join(cwd, ".claude", "settings.local.json"))
    return sites


def _expand(value: str, environ) -> str | None:
    """`${VAR}`, `$VAR` and `${env:VAR}` resolved from the same environment the
    client would use. `None` when a reference cannot be resolved.

    ⚠️ An unresolvable reference is **not** treated as a literal. `${PLEXARM_TOKEN}`
    with the variable unset is a broken config, not a token called
    `"${PLEXARM_TOKEN}"`, and comparing the literal against the real one would
    manufacture a mismatch out of somebody's typo.
    """
    if "$" not in value:
        return value
    out = value
    for prefix, suffix in (("${env:", "}"), ("${", "}"), ("$", "")):
        while prefix in out:
            start = out.index(prefix)
            after = start + len(prefix)
            if suffix:
                end = out.find(suffix, after)
                if end == -1:
                    return None
                name = out[after:end]
                stop = end + len(suffix)
            else:
                name = ""
                stop = after
                while stop < len(out) and (out[stop].isalnum() or out[stop] == "_"):
                    name += out[stop]
                    stop += 1
                if not name:
                    return None
            resolved = environ.get(name)
            if not resolved:
                return None
            out = out[:start] + resolved + out[stop:]
    return out


#: The basename of the helper the plugin ships. An entry naming it sends
#: whatever that helper mints, which is the credential store — see `_bearer`.
HELPER_NAME = "plexarm-headers"


def _bearer(server, environ) -> str | None:
    """The token an `mcpServers` entry would send, or `None`.

    Header lookup is case-insensitive because JSON keys are whatever the person
    typed and `authorization` is as valid as `Authorization`.

    ⛔ AN ENTRY WITH A `headersHelper` SENDS WHAT THE HELPER MINTS, AND STATIC
    HEADERS LOSE. Read out of the client binary 2026-09-11: header assembly is
    `{...staticHeadersExpanded, ...(helperOutput || {})}` — the helper is
    spread SECOND, so it wins every key it sets. From 1.7.0 that is how the
    plugin's own server is configured, and a user who hand-writes the same
    shape into `~/.claude.json` would otherwise be invisible here: this
    function read a `headers` dict and nothing else, so it returned `None` and
    the two-account comparison silently had one account to compare.

    ⚠️ ONLY OUR HELPER IS CLAIMED. Somebody else's helper mints something this
    hook cannot know, and guessing would manufacture a refusal out of a
    stranger's config. Unknown stays `None`, which is silence — the same
    fail-silent-and-safe rule `_config_sites` documents.
    """
    if not isinstance(server, dict):
        return None
    helper = server.get("headersHelper")
    if isinstance(helper, str) and HELPER_NAME in helper:
        return store_token()
    headers = server.get("headers")
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if not isinstance(key, str) or key.lower() != "authorization":
            continue
        if not isinstance(value, str):
            continue
        expanded = _expand(value.strip(), environ)
        if not expanded:
            return None
        if expanded.lower().startswith("bearer "):
            expanded = expanded[len("bearer ") :]
        expanded = expanded.strip()
        return expanded or None
    return None


def _plexarm_servers(mapping, environ) -> list[str]:
    """Every token in this `mcpServers` map that would talk to Plexarm.

    ⚠️ **MATCHED ON THE NAME *AND* ON THE URL.** The name is what collides with
    the plugin's own server and is the measured case; the URL is what catches the
    same credential wired in under a different name, which diverges just as
    badly and which a name-only check would call fine.
    """
    found: list[str] = []
    if not isinstance(mapping, dict):
        return found
    for name, server in mapping.items():
        if not isinstance(server, dict):
            continue
        url = server.get("url") or server.get("serverUrl") or server.get("httpUrl") or ""
        matches = name == SERVER_NAME or (isinstance(url, str) and API_HOST in url)
        if not matches:
            continue
        token = _bearer(server, environ)
        if token:
            found.append(token)
    return found


def discover(cwd: str, environ) -> list[tuple[str, str]]:
    """`(token, file it came from)` for every Plexarm MCP entry this can see.

    Unreadable, unparseable and absent files contribute nothing and say nothing.
    A hook that complained about somebody's malformed `settings.json` would be
    reporting on a file it has no business having an opinion about.
    """
    found: list[tuple[str, str]] = []
    for site in _config_sites(cwd, environ):
        try:
            with open(site, encoding="utf-8") as handle:
                parsed = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        for token in _plexarm_servers(parsed.get("mcpServers"), environ):
            found.append((token, site))
        # `~/.claude.json` keeps project- and local-scope servers under the
        # absolute path of the directory they were added in. That is the scope
        # the measured failure used, and it BEATS the plugin's server for that
        # directory — which is why a project entry nobody remembers adding can
        # silently drive every tool call in it.
        projects = parsed.get("projects")
        if isinstance(projects, dict) and cwd:
            for key in (cwd, os.path.realpath(cwd)):
                entry = projects.get(key)
                if isinstance(entry, dict):
                    for token in _plexarm_servers(entry.get("mcpServers"), environ):
                        found.append((token, site))
    return found


# ─────────────────────────────────────────────────────────────────────────────
# The decision
# ─────────────────────────────────────────────────────────────────────────────
def decide(payload: dict, environ) -> tuple[str | None, bool]:
    """`(what to say, whether it is a refusal)`. `(None, False)` is silence.

    Every branch that is not *"two identity reads that disagree"* returns
    silence, and the outcome is logged so that *"it did not fire"* and *"it could
    not fire"* are different lines in the same file.
    """
    session_id = str(payload.get("session_id") or "")
    state = _state_dir(session_id) if session_id else None
    cwd = str(payload.get("cwd") or "")

    #  ⛔ ONE READ, TWO QUESTIONS. The source is what separates "no credential
    #  anywhere" from "the hooks are fine and the tools are dead", and reading
    #  the store a second time to find out would be a second chance to block on
    #  a keychain unlock prompt in front of a waiting person.
    token, source = resolve_token_with_source(environ)
    if not token:
        _log(state, "no-credential")
        return NOTICE, False
    if source != "store":
        _log(state, "store-empty")
        return STORE_EMPTY, False

    others = [(other, site) for other, site in discover(cwd, environ) if other != token]
    if not others:
        _log(state, "single-credential")
        return None, False

    mine = _cached_identity(state, token)
    if mine is None:
        status, mine = ask_identity(token)
        if not mine:
            _log(state, "unresolved-hook-identity", f"http={status}")
            return None, False
        _remember_identity(state, token, mine)

    seen: set[str] = set()
    for other, site in others:
        if other in seen:
            continue
        seen.add(other)
        theirs = _cached_identity(state, other)
        if theirs is None:
            status, theirs = ask_identity(other)
            if not theirs:
                _log(state, "unresolved-server-identity", f"http={status}")
                continue
            _remember_identity(state, other, theirs)
        if theirs != mine:
            _log(state, "refused", f"{mine} vs {theirs} in {site}")
            return refusal(mine, theirs, site), True
    _log(state, "same-account")
    return None, False


def main() -> int:
    """Print at most one object, and **always exit 0**.

    Same door as `stop_record_guard.py`: every failure — unreadable stdin, a
    disk error, an exception nobody thought of — leaves through the
    `except BaseException` and takes the silent path. A `SessionStart` hook
    cannot block a session by exiting non-zero, but it can waste one by printing
    garbage where context belongs, and there is exactly one `print` here.

    ⚠️ **THE SWITCH IS CHECKED FIRST AND IT TURNS OFF BOTH JOBS.** Switching
    this hook off gives up the missing-credential notice AND the two-account
    refusal together — they are one script, and `plugin/README.md` says so
    rather than leaving somebody to discover it.
    """
    try:
        try:
            raw = sys.stdin.read()
        except BaseException:  # noqa: BLE001 - reading stdin must never decide anything
            raw = ""

        if not switched_on(HOOK_NAME, os.environ):
            return 0

        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        message, refused = decide(payload, os.environ)
        if message is None:
            return 0

        emitted = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": message,
            }
        }
        if refused:
            # ⚠️ BOTH CHANNELS, AND THE DOCSTRING SAYS WHICH ONE IS MEASURED.
            # `additionalContext` was verified by running it; whether
            # `continue: false` halts a `SessionStart` session is documented by
            # the client and NOT measured from here. Emitting only the
            # documented one would stake the refusal on an unverified channel;
            # emitting only the verified one would decline a real stop if the
            # client honours it.
            emitted["continue"] = False
            emitted["stopReason"] = message
        print(json.dumps(emitted))
        return 0
    except BaseException:  # noqa: BLE001 - see the docstring; this IS the control
        return 0


if __name__ == "__main__":
    sys.exit(main())
