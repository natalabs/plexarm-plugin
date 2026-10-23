#!/usr/bin/env python3
"""Plexarm — the credential resolver: one lookup order, shared by every hook.

The order:

1. The OS credential store — macOS Keychain, then Linux Secret Service, then a
   0600 file in the caller's home directory owned by the caller.
2. `PLEXARM_TOKEN` in the environment.

The store comes first because the MCP tools read the store and nothing else
(see `helpers/plexarm-headers`). Reading it first keeps the hooks and the tools
on the same credential. The environment stays as a second source for
containerised and CI installs, where there is no store.

A repository can set environment variables for hooks, so nothing else here is
read from the environment: not the token file's path, and not `HOME` (the home
directory comes from the password database). Every store read runs an absolute
path with a fixed environment, no shell and a timeout.

The hooks import this module from their own directory, and each one keeps a
copy of the same order as a fallback so that a broken install never turns
into a failing hook. Keep every copy reading in the same order.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess

#: The variable Plexarm owns. Second, and the only environment name read.
TOKEN_ENV = "PLEXARM_TOKEN"

#: The Keychain / Secret Service item. `helpers/plexarm-headers` carries the same
#: string; an item stored for one is found by the other.
STORE_SERVICE = "plexarm-api-token"

#: Relative to the caller's home. Deliberately not configurable, so a
#: repository cannot point the hooks at a different token file.
STORE_FILE = ".config/plexarm/token"

#: A locked keychain can block on an unlock prompt, and a SessionStart hook
#: runs in front of a person.
STORE_TIMEOUT_SECONDS = 3.0

#: Only the caller may read the file; a group- or world-readable file is refused.
_ALLOWED_FILE_MODES = frozenset({0o600, 0o400})


def _home() -> str:
    """The caller's home from the password database, not from `$HOME`.

    A repository can set `HOME` for a hook, and that would choose which token
    file is read. The `pwd` import is function-local because `pwd` does not
    exist on Windows.
    """
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError, OSError):
        # No home to find means no file store on this platform; the file is
        # one optional source, so the caller moves on to the next one.
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

    Absolute path, no shell, fixed environment and a hard timeout, because a
    hook's `PATH` can be set by the repository.
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
        # An absent, broken or slow store answers "not here" and the next
        # source is tried. This must not raise: the Stop hook treats a
        # non-zero exit as a block.
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

    The source is returned because `session_start_credential_check.py` tells
    the user something different when only the environment holds a token: the
    MCP tools read the store only, so the hooks work and the tools do not.

    Keep the two reads written out rather than looped, so the environment
    names this module reads stay visible to a reader and to static checks. A
    blank or whitespace-only value is not a credential.
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

    `environ` is passed in rather than read from `os` so callers can be tested
    with a constructed environment.
    """
    token, _ = resolve_token_with_source(environ)
    return token
