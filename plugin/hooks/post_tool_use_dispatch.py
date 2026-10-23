#!/usr/bin/env python3
"""Plexarm — the dispatch capture hook.

Runs after Claude Code's `Agent` tool call (registered `async`, so it never
delays the session). Off until you switch it on in your Plexarm profile: it
first asks the Plexarm API whether you have, with no body,

    GET https://api.plexarm.com/records/dispatch/consent
    Authorization: Bearer <your token>

and only on the answer `{"on": true}` does it read what Claude Code passed it
and send which agent started which:

    POST https://api.plexarm.com/records/dispatch
    Authorization: Bearer <your token>
    {"call_id": ..., "dispatcher": ..., "dispatched": ...}

Three fields and nothing else: the tool call's id, the type of the agent that
made the call (`null` on the main session), and the type of the agent started.
It never reads, keeps or sends the prompt, the description, the output, the
session id or any path. It uses only a token from your credential store, never
one from the environment. It prints nothing, so it adds nothing to the
session's context. Every failure is silent, sends nothing, and exits 0.

Turn it off with `PLEXARM_HOOKS_OFF=post_tool_use_dispatch`, or in
`switches.json`.
"""

from __future__ import annotations

import http.client
import json
import os
import random
import re
import signal
import socket
import sys
import time


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
API_PATH = "/records/dispatch"
CONSENT_PATH = "/records/dispatch/consent"


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
HOOK_NAME = "post_tool_use_dispatch"
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


#: Bounded in the client: an `async` hook has no enforced timeout.
CONNECT_TIMEOUT_SECONDS = 2.0
READ_TIMEOUT_SECONDS = 3.0

#: The tool names a dispatch arrives under (`Task` is the former name).
DISPATCH_TOOLS = frozenset({"Agent", "Task"})

#: The server's own bounds (`DispatchRequest`); a value outside them is not sent.
_CALL_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
#: Identifier characters only (a plugin agent's type carries a `:`); anything
#: else — a control byte, a space — is not sent.
_AGENT_TYPE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")

#: A busy server answers 503; the call id makes a replay harmless, so a 503 is
#: retried a bounded number of times with jitter. Nothing else is retried.
RETRY_STATUS = 503
RETRY_DELAYS_SECONDS = (0.5, 1.5)


def _agent_type(value: object) -> str | None:
    if isinstance(value, str) and _AGENT_TYPE.fullmatch(value):
        return value
    return None


def body_from(payload: object) -> dict | None:
    """The request body, or None when this call is not a dispatch to send.

    Reads four keys of the payload — `tool_name`, `tool_use_id`, `agent_type`
    and `tool_input.subagent_type` — and copies three. Nothing else is touched.
    """
    if not isinstance(payload, dict) or payload.get("tool_name") not in DISPATCH_TOOLS:
        return None
    call_id = payload.get("tool_use_id")
    if not isinstance(call_id, str) or not _CALL_ID.fullmatch(call_id):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    dispatched = _agent_type(tool_input.get("subagent_type"))
    if dispatched is None:
        # No type, no row: never a guessed one.
        return None
    if "agent_type" in payload and payload["agent_type"] is not None:
        dispatcher = _agent_type(payload["agent_type"])
        if dispatcher is None:
            return None
    else:
        dispatcher = None
    return {"call_id": call_id, "dispatcher": dispatcher, "dispatched": dispatched}


def consented(token: str) -> bool:
    """`GET /records/dispatch/consent`, asked once and never retried.

    `True` only for a 200 whose body is a JSON object with `"on": true` — the
    JSON literal, not `1`, not `"true"`. Other keys are ignored: they cannot
    grant anything, and a field the server adds later must not switch every
    installed copy off. Anything else, an older server's 404 and a busy
    server's 503 included, is "off".
    """
    status, raw = exchange("GET", CONSENT_PATH, token, None)
    if status != 200:
        return False
    try:
        answer = json.loads(raw)
    except ValueError:
        return False
    return type(answer) is dict and answer.get("on") is True


def send(token: str, body: dict) -> int:
    """`POST /records/dispatch`, retrying only a 503. Returns the last status."""
    status = post(token, body)
    for delay in RETRY_DELAYS_SECONDS:
        if status != RETRY_STATUS:
            break
        time.sleep(delay * (0.5 + random.random()))
        status = post(token, body)
    return status


def post(token: str, body: dict) -> int:
    """One attempt. Returns the status, 0 for no answer."""
    return exchange("POST", API_PATH, token, body)[0]


def connection() -> http.client.HTTPConnection:
    return http.client.HTTPSConnection(API_HOST, timeout=CONNECT_TIMEOUT_SECONDS)


def exchange(method: str, path: str, token: str, body: dict | None) -> tuple[int, bytes]:
    """One request. Returns the status and at most 4096 bytes of the answer;
    `(0, b"")` for no answer."""
    conn = connection()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "plexarm-plugin-hook",
    }
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        conn.connect()
        if conn.sock is not None:
            conn.sock.settimeout(READ_TIMEOUT_SECONDS)
        conn.request(method, path, body=encoded, headers=headers)
        response = conn.getresponse()
        return response.status, response.read(4096)
    except (OSError, socket.timeout, http.client.HTTPException):
        return 0, b""
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - closing must never raise upward
            pass


#: An `async` hook has no enforced timeout; this bounds the whole run (DNS,
#: the credential store, a slow answer) where the platform allows it.
WALL_CLOCK_SECONDS = 8


def main() -> int:
    timed = hasattr(signal, "SIGALRM")
    if timed:
        signal.signal(signal.SIGALRM, lambda signum, frame: os._exit(0))
        signal.alarm(WALL_CLOCK_SECONDS)
    try:
        return run()
    finally:
        if timed:
            signal.alarm(0)


def run() -> int:
    # The order is the consent: switch, credential, question — and only then
    # stdin, which carries the agent's prompt and output. Nothing before the
    # answer reads it, not even to discard it.
    if not switched_on(HOOK_NAME, os.environ):
        return 0
    # Only a token from the credential store: `PLEXARM_TOKEN` can be planted
    # by a repository's tracked settings, and this hook sends on its behalf.
    token, source = resolve_token_with_source(os.environ)
    if not token or source != "store":
        return 0
    if not consented(token):
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "null")
    except (OSError, ValueError):
        return 0
    body = body_from(payload)
    del payload
    if body is None:
        return 0
    send(token, body)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a capture hook must never fail loudly
        sys.exit(0)
