#!/usr/bin/env python3
"""Plexarm — the SessionStart credential check.

The Stop hook fails open by design: with no credential it checks nothing and
says nothing, and the MCP tools simply fail to connect. Without this hook a
missing or lost credential has no visible symptom at all. The credential can
disappear without the user doing anything wrong, so the property this hook
provides is: when it is not there, you find out in the next session.

────────────────────────────────────────────────────────────────────────────────
HOW IT TELLS YOU
────────────────────────────────────────────────────────────────────────────────
It prints to `SessionStart` stdout, which Claude Code adds to the model's
context in an interactive session; the model then tells the user. It never
blocks the session over a missing credential, and the missing-credential
notice needs no network call. Under `claude -p` the client does not deliver
this context, so a headless session gets no notice.

It is silent when a credential is present, and prints on every session while
one is missing — not once a day, so the notice stops the moment it is fixed.
It checks presence, not validity: a revoked token is reported by the Stop
hook's log, not here.

════════════════════════════════════════════════════════════════════════════════
THE SECOND JOB: TWO CREDENTIALS FOR TWO ACCOUNTS IN ONE SESSION
════════════════════════════════════════════════════════════════════════════════
A `plexarm` MCP server can be configured with its own token — for example by
`claude mcp add … --header "Authorization: Bearer <token>"` in a project
directory — while the plugin's hooks read the credential store. If those two
tokens belong to different accounts, the tools record into one account while
the hooks show the other account's open work.

1. It resolves the credential the hooks hold, through `plexarm_credential.py`.
2. It reads `~/.claude.json` and the project's `.mcp.json` and
   `.claude/settings*.json` for `mcpServers` entries, and collects the literal
   bearer token of every entry that would talk to Plexarm. A value that refers
   to an environment variable is not read.
3. If every token it sees is the same string, it stops and prints nothing. No
   network call. That is the common case.
4. Only for a token different from the hooks' does it ask
   `GET /records/identity` — once per distinct token, cached for the session —
   which account the token belongs to. Two different tokens can belong to the
   same account, so comparing strings alone would refuse sessions where
   nothing is wrong.
5. If the accounts differ, it refuses the session in one sentence naming both
   account slugs and the file the other entry lives in — and nothing else.

The refusal is emitted both as `continue`/`stopReason` and as
`additionalContext`, so it reaches the session whichever channel the client
honours.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import stat
import sys
import tempfile
from datetime import datetime, timezone

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


#: What the model is told when the credential is missing. The second half asks
#: the agent to tell the user, because the user is the only one who can repair
#: it, and the repair is spelled out as commands they can run.
#:
#: `NOTICE` is the case where there is no credential anywhere. `STORE_EMPTY` is
#: the case where only `PLEXARM_TOKEN` is set: the hooks work, but the MCP tools
#: read the store only and will not connect.
#:
#: Both name the three stores in the order `helpers/plexarm-headers` reads them
#: — Keychain, Secret Service, file — and each command prompts for the token,
#: so it never enters the shell's history.
_STORE_COMMANDS = (
    "macOS: `security add-generic-password -a \"$USER\" -s plexarm-api-token -w` "
    "(press return, then paste the token at the prompt); "
    "Linux: `secret-tool store --label=\"Plexarm API token\" service plexarm-api-token`; "
    "or a file: `umask 077; mkdir -p ~/.config/plexarm; read -rs t && printf %s \"$t\" "
    "> ~/.config/plexarm/token`. If you have no Plexarm account, create a free one at "
    "https://plexarm.com first, then create a token at https://plexarm.com/me; it is shown "
    "once. `/plexarm:connect` walks through all of this."
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


#: Where the identity read goes. Compiled in, never read from the repository or
#: the environment — a redirected host is a stolen token.
API_HOST = "api.plexarm.com"
API_PATH = "/records/identity"

#: Connect and read timeouts, kept short because this runs at the start of a
#: session, in front of a person.
CONNECT_TIMEOUT_SECONDS = 2.0
READ_TIMEOUT_SECONDS = 3.0

#: The server name Claude Code would bind our tools to. A second entry under a
#: DIFFERENT name pointing at our API is caught too — see `_plexarm_servers`.
SERVER_NAME = "plexarm"


#: What the model is told when the tools and the hooks belong to different
#: accounts. It names both account slugs and the file to fix, and nothing else
#: — not the other account's items, not a count of them. The instruction to the
#: agent matters: if the client does not halt the session, this sentence is
#: what keeps a record from landing in the wrong account.
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


def _log(state: str | None, outcome: str, note: str = "") -> None:
    """Append one line. Never raises, and never writes a token.

    Nothing passed to `note` may be a credential or derived from one: callers
    pass an outcome word, an account slug or a file path.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{stamp} credential-check:{outcome}" + (f" {note}" if note else "") + "\n"
    targets = [os.path.join(state, "outcomes.log")] if state else []
    durable = _log_target(os.environ)
    if durable:
        targets.append(durable)
    for target in targets:
        _write_file(target, line, append=True)


def _cache_path(state: str, token: str) -> str:
    """One file per token per session.

    The filename is a truncated hash of the token, never the token itself.
    """
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return os.path.join(state, f"identity-{digest}.json")


def _cached_identity(state: str | None, token: str):
    """The account this token belongs to, if this session already asked.

    Cached per session and never longer.
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

    The token never reaches the disk — not as content, not as a filename, not
    in a log line. The mode is set when the file is created, so it never
    exists with looser permissions.
    """
    if not state:
        return
    _write_file(_cache_path(state, token), json.dumps({"account": account}), append=False)


# ─────────────────────────────────────────────────────────────────────────────
# The one network call — `GET /records/identity`
# ─────────────────────────────────────────────────────────────────────────────
def ask_identity(token: str) -> tuple[int, str | None]:
    """`GET /records/identity`. Returns `(status, account_slug_or_None)`.

    Status 0 means no answer at all — no network, a timeout, a body that is not
    JSON. **No retry**: the failure direction is already safe, because every
    non-answer leaves this hook silent.

    It sends the token and no body. The answer carries the account and person
    slugs and nothing else; `plugin/README.md` discloses both fields.
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

    `~/.claude.json` holds the user-scope `mcpServers` map and a per-project one
    under `projects.<absolute cwd>`. `CLAUDE_CONFIG_DIR` is honoured, as the
    client honours it.
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


#: The basename of the helper the plugin ships. An entry naming it sends
#: whatever that helper mints, which is the credential store — see `_bearer`.
HELPER_NAME = "plexarm-headers"


def _bearer(server) -> str | None:
    """The token an `mcpServers` entry would send, or `None`.

    Header lookup is case-insensitive because JSON keys are whatever the person
    typed and `authorization` is as valid as `Authorization`.

    An entry with a `headersHelper` sends what the helper produces, which wins
    over static headers. An entry naming this plugin's helper therefore sends
    the credential store's token.

    A value containing `$` refers to an environment variable. It is never
    expanded: the file may come from a repository, and expanding it would send
    whatever that variable holds.
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
        value = value.strip()
        if "$" in value:
            return None
        if value.lower().startswith("bearer "):
            value = value[len("bearer ") :]
        value = value.strip()
        return value or None
    return None


def _plexarm_servers(mapping) -> list[str]:
    """Every token in this `mcpServers` map that would talk to Plexarm.

    Matched on the name and on the URL: the name collides with the plugin's own
    server, and the URL catches a Plexarm entry under a different name.
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
        token = _bearer(server)
        if token:
            found.append(token)
    return found


def discover(cwd: str, environ) -> list[tuple[str, str]]:
    """`(token, file it came from)` for every Plexarm MCP entry this can see.

    Unreadable, unparseable and absent files contribute nothing and say nothing.
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
        for token in _plexarm_servers(parsed.get("mcpServers")):
            found.append((token, site))
        # `~/.claude.json` keeps project- and local-scope servers under the
        # absolute path of the directory they were added in, and such an entry
        # takes precedence over the plugin's server in that directory.
        projects = parsed.get("projects")
        if isinstance(projects, dict) and cwd:
            for key in (cwd, os.path.realpath(cwd)):
                entry = projects.get(key)
                if isinstance(entry, dict):
                    for token in _plexarm_servers(entry.get("mcpServers")):
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

    #  One read answers both questions: whether there is a credential, and
    #  whether it came from the store the MCP tools read.
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

    Every failure leaves through the `except BaseException` and takes the
    silent path; there is exactly one `print` here.

    The switch is checked first and turns off both jobs: the missing-credential
    notice and the two-account refusal.
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
            # Both channels: `additionalContext` reaches the model, and
            # `continue: false` halts the session where the client honours it.
            emitted["continue"] = False
            emitted["stopReason"] = message
        print(json.dumps(emitted))
        return 0
    except BaseException:  # noqa: BLE001 - see the docstring; this IS the control
        return 0


if __name__ == "__main__":
    sys.exit(main())
