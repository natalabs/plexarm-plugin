#!/usr/bin/env python3
"""Plexarm — the SessionStart agent-roster sync.

Fires when a session starts, after `/clear` and on a resume. Reads the top of the
Claude Code agent definitions this session loaded, and `POST`s their name, role
and manager to `/records/sync-agents`. In an owner's or admin's session, that
creates any agent not already in the account's roster and updates one whose file
differs.

`/plexarm:connect` runs this same file with `--report`, because installing the
plugin, `/reload-plugins` and an MCP reconnect fire no session event. In that
form it reads no standard input, takes the repository from the folder the session
process is in and from nowhere else, and prints what happened in plain lines,
whether or not anything changed.

An agent that is not in the roster cannot be named on a record, so its work
would be attributable to nobody. The agent files live on the user's machine,
where the Plexarm API cannot read them, so a hook is the only place this sync
can run without relying on an agent to remember to do it.

════════════════════════════════════════════════════════════════════════════════
A MISSING FILE MEANS NOTHING
════════════════════════════════════════════════════════════════════════════════
What one session loads depends on where and how it was started: repository
agents only inside that repository, and user agents only when the session
loads user settings. An absent alias is therefore evidence of nothing, so this
sync never retires an agent.

The plugin's own agents, in the `agents/` folder beside this hook's folder, are
the third scope. That folder is found from this file's own path, never from the
environment, and it is read whenever the hook runs: the hook runs only when the
plugin is loaded, and a loaded plugin's agents are loaded with it. A user file
with the same alias wins over a repository's, and a repository's over the plugin's.
When a repository's file replaces a plugin agent, the session is told which.

════════════════════════════════════════════════════════════════════════════════
IT SENDS ONLY THE SCOPES THE SESSION LOADED
════════════════════════════════════════════════════════════════════════════════
Claude Code reads `~/.claude/agents/` when the session loads the `user`
setting source and `<project>/.claude/agents/` when it loads `project`. The
sources are chosen by `--setting-sources` on the command line that started the
session; without the flag a session loads all of them. A hook is told neither,
so this one reads the command line of the Claude Code process that ran it. When
it cannot read that command line, or cannot be sure the process it read is the
session, it sends nothing and says so in one line.

A session nobody is attending fires this hook before any folder trust question
and no person sees what it says. So it sends no repository agents, and only a
start sends at all.

`PLEXARM_AGENT_SYNC` can only turn the sync off. `CLAUDE_CODE_SESSION_ATTENDED`
can only keep repository agents from being sent. `CLAUDE_PID` is compared with the
session's process id, and a mismatch can only stop the sync. No value of any of
them changes where a byte goes.

It is silent when it changes nothing, which after the first run is most runs. It
is not silent when it sent nothing or got no good answer: each of those adds one
fixed line to the session's context saying what to do.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import sys
import threading
import unicodedata

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
            if os.stat(os.path.dirname(path)).st_mode & 0o002:
                return None
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
# user's `settings.json`. OFF beats ON beats the file beats default-on.
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

#: A socket timeout does not bound the name lookup, so the lookup runs on a
#: worker thread that is abandoned after this long.
RESOLVE_TIMEOUT_SECONDS = 2.0

#: Which `SessionStart` sources sync. It only creates and brings files into
#: line, so a repeat costs one request and changes nothing. `compact` is left
#: out: it can run with no person acting.
SYNC_ON_SOURCES = frozenset({"startup", "resume", "clear"})

#: What marks a session with no terminal on its command line: a short-flag
#: cluster holding `p` (`-p`, `-pc`, `-cp`), `--print`, or a stream format.
_SHORT_PRINT = re.compile(r"^-[a-zA-Z]*p[a-zA-Z]*$")
HEADLESS_FLAGS = ("--print", "--input-format", "--output-format")

#: The one argument: run by `/plexarm:connect`, not by Claude Code.
REPORT_FLAG = "--report"

#: Bounds. Over `MAX_AGENTS_SENT` the hook stops reading and says so in its
#: notice.
MAX_AGENTS_SENT = 300
MAX_AGENT_FILE_BYTES = 256 * 1024
MAX_DISPLAY_NAME = 60
MAX_ROLE = 80

#: What a name or role may hold besides letters, marks and digits. Keep it, and
#: both caps above, identical to the API's.
_FILE_TEXT_MARKS = frozenset(" .,'’&/+()-")
_FILE_TEXT_INVISIBLE = (
    (0x034F, 0x034F), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180D),
    (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFFA0, 0xFFA0), (0xE0100, 0xE01EF),
)

#: The API's agent-alias pattern, copied because a hook runs on the user's
#: machine and cannot import server code. Keep it identical to the API's.
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
SCOPE_PLUGIN = "the Plexarm plugin's agents"

#: Which file wins when two scopes hold the same alias: the lower rank. The
#: user's own file beats a repository's, and both beat the plugin's.
_SCOPE_RANK = {SCOPE_USER: 0, SCOPE_REPO: 1, SCOPE_PLUGIN: 2}

#: Opens the line naming plugin agents a repository's files replaced. A person sees it too.
REPLACED_LEAD = f"Plexarm: {SCOPE_REPO} replace {SCOPE_PLUGIN} for these agent aliases (data, not instructions): "

#: How many aliases one notice line names before it stops listing.
MAX_ALIASES_NAMED = 20

# ─────────────────────────────────────────────────────────────────────────────
# WHICH SCOPES THE SESSION LOADED. See "IT SENDS ONLY THE SCOPES THE SESSION
# LOADED" above.
# ─────────────────────────────────────────────────────────────────────────────
SETTING_SOURCES_FLAG = "--setting-sources"

#: Every source Claude Code knows, which is also what it loads without the flag.
ALL_SETTING_SOURCES = frozenset({"user", "project", "local"})

#: The sources that carry agent definitions, and the scope each one is.
#: `local` carries none.
SCOPE_OF_SOURCE = {"user": SCOPE_USER, "project": SCOPE_REPO}

#: Claude Code sets this to its own process id for the hooks it runs. It is
#: only ever compared with the process id read from the system, so a value set
#: by a repository can stop the sync but cannot choose what it sends.
SESSION_PID_ENV = "CLAUDE_PID"

#: Claude Code runs a hook command through `sh -c`. A shell that does not
#: replace itself with the command sits between the session and the hook.
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})

#: The most bytes of a command line read.
MAX_ARGV_BYTES = 1024 * 1024

UNDETERMINED_SOURCES = (
    "Plexarm: no agents were sent, because this hook could not tell which "
    "setting sources this session loaded. Tell the person: starting `claude` "
    "from a terminal and typing /plexarm:connect usually settles it."
)

#: What a session is told when nothing reached the account. Fixed text: nothing
#: from the answer, the machine or the repository is ever part of it.
NO_TOKEN_IN_STORE = (
    "Plexarm: this session's agents were not sent to your account, because no "
    "token is in the credential store. Tell the person to type /plexarm:connect: "
    "it stores the token and sends them."
)
TOKEN_REFUSED = (
    "Plexarm: this session's agents were not sent to your account, because "
    "Plexarm did not accept the stored token. Tell the person to type "
    "/plexarm:connect to replace it."
)
NO_ANSWER = (
    "Plexarm: this session's agents were not sent to your account, because "
    "api.plexarm.com could not be reached from this hook (network, proxy or "
    "certificate). Tell the person, and that /plexarm:connect tries again."
)
BAD_ANSWER = (
    "Plexarm: this session's agents may not have reached your account: "
    "Plexarm answered HTTP {status}. Tell the person, and that /plexarm:connect "
    "tries again."
)

#: Said only under `--report`, where someone asked.
SWITCHED_OFF = "Plexarm: the agent sync is switched off on this machine."
NO_AGENT_FILES = "Plexarm: this session has no agent files to send."


def _process_argv(pid: int) -> list[str] | None:
    """The exact argument vector of process `pid`, or None. Never raises.

    Read from the system itself, never through a shell or `ps`, so an argument
    that contains spaces is still one argument.
    """
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                raw = handle.read(MAX_ARGV_BYTES)
            parts = raw.split(b"\0")
            if parts and parts[-1] == b"":
                parts.pop()
            return [part.decode("utf-8", "replace") for part in parts] or None
        if sys.platform == "darwin":
            import ctypes

            system = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            # CTL_KERN, KERN_PROCARGS2: argc, the executable path, padding,
            # then argc NUL-terminated arguments.
            mib = (ctypes.c_int * 3)(1, 49, pid)
            size = ctypes.c_size_t(0)
            if system.sysctl(mib, 3, None, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
                return None
            size.value = min(size.value, MAX_ARGV_BYTES)
            buffer = ctypes.create_string_buffer(size.value)
            if system.sysctl(mib, 3, buffer, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
                return None
            raw = buffer.raw[: size.value]
            if len(raw) < 4:
                return None
            argc = int.from_bytes(raw[:4], sys.byteorder)
            position = raw.find(b"\0", 4)
            if position < 0:
                return None
            while position < len(raw) and raw[position] == 0:
                position += 1
            argv: list[str] = []
            for _ in range(argc):
                end = raw.find(b"\0", position)
                if end < 0:
                    return None
                argv.append(raw[position:end].decode("utf-8", "replace"))
                position = end + 1
            return argv or None
    except (OSError, ValueError, AttributeError):
        return None
    return None


def _process_parent(pid: int) -> int | None:
    """The parent process id of `pid`, or None. Never raises."""
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/stat", "rb") as handle:
                raw = handle.read(4096)
            # `pid (name) state ppid …`; the name may itself contain `)`.
            fields = raw[raw.rindex(b")") + 1 :].split()
            return int(fields[1])
        if sys.platform == "darwin":
            import ctypes

            system = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            # PROC_PIDTBSDINFO: a 136-byte `proc_bsdinfo`, `pbi_ppid` at 16.
            buffer = ctypes.create_string_buffer(136)
            if system.proc_pidinfo(pid, 3, ctypes.c_uint64(0), buffer, 136) != 136:
                return None
            return int.from_bytes(buffer.raw[16:20], sys.byteorder) or None
    except (OSError, ValueError, IndexError, AttributeError):
        return None
    return None


def _process_cwd(pid: int) -> str | None:
    """The working directory of process `pid`, or None. Never raises."""
    try:
        if sys.platform.startswith("linux"):
            return os.readlink(f"/proc/{pid}/cwd")
        if sys.platform == "darwin":
            import ctypes

            system = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            # PROC_PIDVNODEPATHINFO: two 1176-byte `vnode_info_path`, the
            # working directory first, its path 152 bytes in.
            buffer = ctypes.create_string_buffer(2352)
            if system.proc_pidinfo(pid, 9, ctypes.c_uint64(0), buffer, 2352) != 2352:
                return None
            path = buffer.raw[152:1176].split(b"\0", 1)[0]
            return path.decode("utf-8") if path else None
    except (OSError, ValueError, AttributeError):
        return None
    return None


def _session_process() -> tuple[int, list[str]] | None:
    """The Claude Code process that ran this hook, as `(pid, argv)`, or None.

    That process is this hook's parent, or the parent of an `sh -c` between
    them. When Claude Code says which process it is, the two must agree.
    """
    pid = os.getppid()
    argv = _process_argv(pid)
    if argv is not None and os.path.basename(argv[0]) in _SHELLS and argv[1:2] == ["-c"]:
        parent = _process_parent(pid)
        if parent is None:
            return None
        pid, argv = parent, _process_argv(parent)
    if argv is None:
        return None
    claimed = os.environ.get(SESSION_PID_ENV)
    if claimed is not None and claimed.strip() != str(pid):
        return None
    return pid, argv


def _session_argv() -> list[str] | None:
    """The command line of the Claude Code process that ran this hook, or None."""
    found = _session_process()
    return None if found is None else found[1]


def _session_cwd() -> str | None:
    """The folder the Claude Code process that ran this hook is in, or None."""
    found = _session_process()
    return None if found is None else _process_cwd(found[0])


def _setting_sources(argv: list[str]) -> frozenset | None:
    """The setting sources a command line loads, or None if they cannot be read.

    No flag loads every source. With the flag, its comma-separated list, and
    the last flag wins. A name this hook does not know means its reading of the
    flag is out of date, so it is not guessed at.
    """
    value = None
    for index in range(1, len(argv)):
        token = argv[index]
        if token == "--":
            break
        if token == SETTING_SOURCES_FLAG:
            if index + 1 >= len(argv):
                return None
            value = argv[index + 1]
        elif token.startswith(SETTING_SOURCES_FLAG + "="):
            value = token[len(SETTING_SOURCES_FLAG) + 1 :]
    if value is None:
        return ALL_SETTING_SOURCES
    names = frozenset(name.strip() for name in value.split(",") if name.strip())
    if not names <= ALL_SETTING_SOURCES:
        return None
    return names


#: Claude Code says whether anyone is attending the session; "0" means nobody is.
ATTENDED_ENV = "CLAUDE_CODE_SESSION_ATTENDED"


def _headless(argv: list[str]) -> bool:
    """Whether a command line runs with no terminal. A false yes only means fewer
    agents are sent."""
    for token in argv[1:]:
        if token == "--":
            return False
        if _SHORT_PRINT.match(token):
            return True
        if any(token == flag or token.startswith(flag + "=") for flag in HEADLESS_FLAGS):
            return True
    return False


def _loaded_scopes(argv: list[str] | None) -> frozenset | None:
    """The agent scopes a session's command line loaded, or None if that cannot
    be told."""
    if argv is None:
        return None
    sources = _setting_sources(argv)
    if sources is None:
        return None
    return frozenset(SCOPE_OF_SOURCE[s] for s in sources if s in SCOPE_OF_SOURCE)


def _read_small(path: str, cap: int, follow: bool) -> str | None:
    """Read a regular file, or return None. Never raises."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if not follow:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8", errors="replace") as handle:
            info = os.fstat(handle.fileno())
            if (info.st_mode & 0o170000) != 0o100000 or info.st_size > cap:
                return None
            return handle.read(cap)
    except (OSError, ValueError):
        return None


def _file_text(value: str | None, cap: int) -> str | None:
    """`value` if it may be sent as a name or role, else None."""
    if not value or len(value) > cap or not value[0].isalnum():
        return None
    for char in value:
        if unicodedata.category(char)[0] not in "LMN" and char not in _FILE_TEXT_MARKS:
            return None
        if any(low <= ord(char) <= high for low, high in _FILE_TEXT_INVISIBLE):
            return None
    return value


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
        return _file_text(found.group("v").strip().strip("'\"").strip(), cap)

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
    """The caller's home from the password database, not from `$HOME`."""
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_dir or None
    except (ImportError, KeyError, OSError):
        return None


def _plugin_agents_dir() -> str:
    """The plugin's own `agents/`: beside the folder this file is in. Found
    from this file's path, as `switched_on` finds `switches.json`, and never
    from the environment, which a repository can set for hooks."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "agents")


def _agent_dirs(cwd: str, scopes: frozenset, project_env: bool = True) -> list[tuple[str, str]]:
    """`(scope, directory)` for each place Claude Code reads agent definitions
    from, among the `scopes` this session loaded.

    User-level first, then project-level, matching the client's own two
    locations, then the plugin's own. `CLAUDE_PROJECT_DIR` is preferred over the payload's `cwd` when
    set, because a session started in a subdirectory has a `cwd` below the
    project root and would miss `<root>/.claude/agents`. `--report` passes
    `project_env=False`: it is run from an agent's shell, not by Claude Code, so
    only the folder the session process is in names the repository.
    """
    dirs: list[tuple[str, str]] = []
    if SCOPE_USER in scopes:
        home = _user_home()
        if home:
            dirs.append((SCOPE_USER, os.path.join(home, ".claude", "agents")))
    if SCOPE_REPO in scopes:
        project = (os.environ.get("CLAUDE_PROJECT_DIR") if project_env else None) or cwd
        if project:
            root = os.path.realpath(project)
            repo_agents = os.path.join(root, ".claude", "agents")
            if os.path.realpath(repo_agents) == repo_agents:
                dirs.append((SCOPE_REPO, repo_agents))
    if SCOPE_PLUGIN in scopes:
        dirs.append((SCOPE_PLUGIN, _plugin_agents_dir()))
    # De-duplicate while preserving order — a session whose project is the home
    # directory would otherwise read the same tree twice, once per scope.
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for scope, directory in dirs:
        resolved = os.path.realpath(directory)
        if resolved not in seen:
            seen.add(resolved)
            unique.append((scope, directory))
    return unique


def _discover(cwd: str, scopes: frozenset, project_env: bool = True) -> tuple[list[dict], bool]:
    """Every agent this session loaded. Returns `(agents, overflowed)`.

    Claude Code *"scans `.claude/agents/` and `~/.claude/agents/` recursively"*,
    so this walks subdirectories too — a roster organised into `agents/review/`
    is one the client honours and this must not miss.

    Each entry carries `scope`, the scope of the directory it was read from.
    Duplicate aliases are resolved in `_resolve`, so which one is sent is a
    rule rather than an accident of order.
    """
    agents: list[dict] = []
    for scope, directory in _agent_dirs(cwd, scopes, project_env):
        if not os.path.isdir(directory):
            continue
        # A repository's agent file is read only if it is not a symbolic link.
        follow = scope == SCOPE_USER
        try:
            # A symbolic link below the root is not followed.
            walker = os.walk(directory, followlinks=False)
            for root, _subdirs, files in walker:
                for filename in sorted(files):
                    if not filename.endswith(".md"):
                        continue
                    if len(agents) >= MAX_AGENTS_SENT:
                        return agents, True
                    text = _read_small(
                        os.path.join(root, filename), MAX_AGENT_FILE_BYTES, follow
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
    """One wire entry per alias, the user scope winning over the repository's and
    both over the plugin's, and what to say locally.

    Returns `(wire, scope_of, local_lines)`:
      · `wire` — the request body's `agents`: alias, `display_name`, `role`, and
        `manager` only when the file makes a valid claim. Exactly one entry per
        alias, so a cloned repository's file can never override the user's own.
      · `scope_of` — alias -> the scope its sent entry came from, so the
        notice can name a scope without the server ever learning one.
      · `local_lines` — what the hook reports without asking anyone: a claim
        that is not an alias, a repo-scope claim that disagrees with the
        user-scope file it lost to, and a plugin agent a repository's file
        replaced. Aliases only; never a value, never a path.

    Within one scope the first file walked wins; the rest are ignored.
    """
    winner: dict[str, dict] = {}
    losers: list[dict] = []
    for entry in found:
        held = winner.get(entry["alias"])
        if held is None:
            winner[entry["alias"]] = entry
        elif _SCOPE_RANK[entry["scope"]] < _SCOPE_RANK[held["scope"]]:
            winner[entry["alias"]] = entry
            losers.append(held)
        else:
            losers.append(entry)

    wire: list[dict] = []
    scope_of: dict[str, str] = {}
    invalid: dict[str, list[str]] = {}
    for alias, entry in winner.items():
        sent = {"alias": alias, "display_name": entry["display_name"], "role": entry["role"]}
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
    replaced = sorted(
        {
            loser["alias"]
            for loser in losers
            if loser["scope"] == SCOPE_PLUGIN and winner[loser["alias"]]["scope"] == SCOPE_REPO
        }
    )

    local_lines: list[str] = []
    bad = [
        f"{scope}: {_quoted(sorted(invalid[scope]))}"
        for scope in (SCOPE_USER, SCOPE_REPO, SCOPE_PLUGIN)
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
    if replaced:
        local_lines.append(f"{REPLACED_LEAD}{_quoted(replaced)}. The repository's files are what is sent for them.")
    return wire, scope_of, local_lines


def _quoted(aliases: list[str]) -> str:
    """Aliases as quoted data, capped. Each was validated against `_ALIAS`."""
    named = ", ".join(f'"{a}"' for a in aliases[:MAX_ALIASES_NAMED])
    more = len(aliases) - MAX_ALIASES_NAMED
    return named + (f" and {more} more" if more > 0 else "")


def _addresses(host: str, port: int) -> list:
    """`getaddrinfo` for `host`, or `[]` when it fails or outlasts
    `RESOLVE_TIMEOUT_SECONDS`. The worker thread is a daemon, so a lookup that
    never returns cannot keep the hook running."""
    found: list = []

    def lookup() -> None:
        try:
            found.extend(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except (OSError, UnicodeError):
            pass

    worker = threading.Thread(target=lookup, daemon=True)
    worker.start()
    worker.join(RESOLVE_TIMEOUT_SECONDS)
    return [] if worker.is_alive() else list(found)


def _open_socket(address: tuple, timeout: float, source_address=None) -> socket.socket:
    """`socket.create_connection`, with the name lookup bounded by `_addresses`."""
    addresses = _addresses(address[0], address[1])
    if not addresses:
        raise OSError("the API host did not resolve in time")
    error: OSError = OSError("no address could be reached")
    for family, kind, proto, _name, sockaddr in addresses:
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            error = exc
            sock.close()
    raise error


def _connection() -> http.client.HTTPSConnection:
    """An HTTPS connection to `API_HOST` whose name lookup is bounded too.

    Only the TCP connect uses the looked-up address; the certificate is still
    verified against `API_HOST`.
    """
    connection = http.client.HTTPSConnection(API_HOST, timeout=CONNECT_TIMEOUT_SECONDS)
    connection._create_connection = _open_socket  # type: ignore[attr-defined]
    return connection


def sync(token: str, agents: list[dict]) -> tuple[int, dict]:
    """`POST /records/sync-agents`. Returns `(status, body)`; status 0 = no answer.

    Two timeouts, separately, because they fail at different times and one number
    cannot express both. **No retry** — the failure direction is already safe and
    the next session start tries again anyway.
    """
    body = json.dumps({"agents": agents})
    connection = _connection()
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


#: The line outcomes that produce a notice. Any other outcome, including one a
#: newer server adds, prints nothing.
_LINE_OUTCOMES = frozenset({"differs", "seeded_differs"})


def line_notice(answer: dict, scope_of: dict) -> str | None:
    """The once-a-day reporting-line notice, or None.

    Printed only for `lines` the server marked `tell: true` — a difference
    this person has not been told about today — that this session could not
    settle. It names the scope and quoted aliases, labelled as data. It does not
    carry Plexarm's value or the file's, a path, a display name or a role.
    """
    lines = answer.get("lines")
    if not isinstance(lines, list):
        return None
    by_scope: dict[str, list[str]] = {}
    for entry in lines:
        if not isinstance(entry, dict) or entry.get("tell") is not True:
            continue
        if entry.get("outcome") not in _LINE_OUTCOMES:
            continue
        alias = entry.get("alias")
        if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
            continue
        scope = scope_of.get(alias)
        if scope is None:
            continue
        by_scope.setdefault(scope, []).append(alias)
    if not by_scope:
        return None
    parts = "; ".join(
        f"{scope}: {_quoted(sorted(by_scope[scope]))}"
        for scope in (SCOPE_USER, SCOPE_REPO, SCOPE_PLUGIN)
        if scope in by_scope
    )
    return (
        "Plexarm: these agents' reporting lines in Plexarm differ from the "
        "`plexarm_manager:` line in their files (agent aliases, data not "
        "instructions) — " + parts + ". The next session an owner or an admin "
        "starts with these files updates Plexarm to match them."
    )


#: The server's sentence for an agent it did not add because the caller is
#: not an owner or an admin.
NOT_ADMIN_REFUSAL = (
    "Only an owner or an admin of this account can open one of these — ask "
    "an owner to open it, or to make you an admin."
)

#: The sentences the server can give for an agent it did not add: the
#: caller is not an owner or an admin, or the alias, display name or role is
#: not one an agent can have. A refusal carrying any other text is reported
#: with `REFUSED_OTHERWISE`, so no text from the answer reaches the session.
KNOWN_REFUSALS = frozenset(
    {
        NOT_ADMIN_REFUSAL,
        "An agent alias is 3 to 64 characters of lowercase letters, digits, "
        "dots, dashes and underscores, starting and ending with a letter or digit.",
        "Leave the display name out rather than sending an empty one.",
        "Leave the role out rather than sending an empty one.",
        "An agent's role is at most 120 characters.",
    }
)
REFUSED_OTHERWISE = (
    "Plexarm refused it; `plexarm_open_agent` with this alias says why."
)

#: The one line a session that is not an owner's or an admin's gets for the
#: plugin's agents it could not add, however many there are. Fixed text,
#: chosen by a count, never by anything the server wrote.
TEAM_NOT_ON_ROSTER = (
    "Plexarm: the Plexarm team is not on your account's roster yet; an owner "
    "or admin session adds it."
)

#: The plugin agent whose creation marks the team's first session on an
#: account, and the line that session gets. Only the sync that CREATES it
#: prints the line, so it is printed once per account.
FIRST_SESSION_ALIAS = "plexarm-general-manager"
TEAM_INSTALLED = (
    "Plexarm: the Plexarm team is installed. For a job that spans more than "
    "one discipline, dispatch the General Manager first."
)


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def notice(answer: dict, scope_of: dict | None = None) -> str | None:
    """What to tell the session, or None to stay silent.

    Silent unless something changed. `unchanged` is the steady state;
    `created` is worth one line because the session can name those agents
    immediately.

    `refused` is always reported when present, however small: a refused agent is
    an agent whose work cannot be attributed, and the sentence names the reason.

    The plugin's agents, by `scope_of`, are reported as a team: every one
    refused because the caller is not an owner or an admin collapses into
    `TEAM_NOT_ON_ROSTER`, and the session whose sync created
    `FIRST_SESSION_ALIAS` from the plugin gets `TEAM_INSTALLED`.

    Every alias printed matches `_ALIAS`, and every reason is one of
    `KNOWN_REFUSALS` or `REFUSED_OTHERWISE`: the answer is never echoed.
    """
    scope_of = scope_of or {}
    created = [
        a for a in _as_list(answer.get("created")) if isinstance(a, str) and _ALIAS.fullmatch(a)
    ]
    refused = [
        r
        for r in _as_list(answer.get("refused"))
        if isinstance(r, dict) and isinstance(r.get("alias"), str) and _ALIAS.fullmatch(r["alias"])
    ]
    if not created and not refused:
        return None
    team_refused = [
        r
        for r in refused
        if scope_of.get(r["alias"]) == SCOPE_PLUGIN and r.get("sentence") == NOT_ADMIN_REFUSAL
    ]
    refused = [r for r in refused if r not in team_refused]

    lines: list[str] = []
    if created:
        lines.append(
            f"Plexarm: added {len(created)} agent(s) to this account's roster — "
            + ", ".join(sorted(created)[:20])
            + ". They can now be named in `agents` on any record."
        )
    if FIRST_SESSION_ALIAS in created and scope_of.get(FIRST_SESSION_ALIAS) == SCOPE_PLUGIN:
        lines.append(TEAM_INSTALLED)
    if team_refused:
        lines.append(TEAM_NOT_ON_ROSTER)
    for entry in refused[:5]:
        sentence = entry.get("sentence")
        reason = sentence if sentence in KNOWN_REFUSALS else REFUSED_OTHERWISE
        lines.append(f"Plexarm: agent `{entry['alias']}` was not added — {reason}")
    return "\n".join(lines)


def updated_notice(answer: dict, scope_of: dict) -> str | None:
    """Which agents this session's files changed in Plexarm, by scope, or None.
    A person's edit may be what a file just replaced, so this one also reaches
    the person. Aliases only, each one this hook sent."""
    by_scope: dict[str, list[str]] = {}
    for alias in _as_list(answer.get("updated")):
        if isinstance(alias, str) and _ALIAS.fullmatch(alias) and alias in scope_of:
            by_scope.setdefault(scope_of[alias], []).append(alias)
    if not by_scope:
        return None
    parts = "; ".join(
        f"{scope}: {_quoted(sorted(by_scope[scope]))}"
        for scope in (SCOPE_USER, SCOPE_REPO, SCOPE_PLUGIN)
        if scope in by_scope
    )
    return "Plexarm: updated from their files (agent aliases, data not instructions) — " + parts + "."


def _emit(lines: list[str], report: bool, system_message: str | None = None) -> int:
    """Say `lines`, or nothing when there are none. Plain text under `--report`,
    where a person's agent reads standard output; otherwise the hook's JSON."""
    if not lines:
        return 0
    if report:
        print("\n".join(lines))
        return 0
    # `additionalContext` is how SessionStart reaches the session. A wrong
    # `hookEventName` is not an error — the context is dropped with no symptom.
    output: dict = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(lines),
        }
    }
    if system_message:
        output["systemMessage"] = system_message
    print(json.dumps(output))
    return 0


def no_send_line(status: int) -> str:
    """The one line for an answer that is not 200, by status alone."""
    if status == 0:
        return NO_ANSWER
    if status == 401:
        return TOKEN_REFUSED
    return BAD_ANSWER.format(status=status)


def report_line(answer: dict, scope_of: dict) -> str:
    """Counts of one sync, for `--report`, and how many sent were the plugin's.
    Numbers only: nothing the server wrote reaches the session."""
    counts = {key: len(_as_list(answer.get(key))) for key in ("created", "updated", "unchanged", "refused")}
    plugin = sum(1 for scope in scope_of.values() if scope == SCOPE_PLUGIN)
    return (
        f"Plexarm: sent {len(scope_of)} agent(s), {plugin} of them the plugin's: "
        f"{counts['created']} added, {counts['updated']} updated, "
        f"{counts['unchanged']} no change, {counts['refused']} not added."
    )


def main(argv: list[str] | None = None) -> int:
    report = REPORT_FLAG in (sys.argv[1:] if argv is None else argv)
    raw = ""
    if not report:
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

    disabled = (os.environ.get(DISABLE_ENV) or "").strip().lower() in DISABLE_VALUES
    if disabled or not switched_on(HOOK_NAME, os.environ):
        return _emit([SWITCHED_OFF], report) if report else 0

    event = payload.get("source")
    if isinstance(event, str) and event not in SYNC_ON_SOURCES:
        return 0

    session = _session_argv()
    headless = os.environ.get(ATTENDED_ENV) == "0" or (session is not None and _headless(session))
    if headless and event != "startup" and not report:
        return 0

    # A payload that names no event is not a session anyone can act on.
    speaks = report or isinstance(event, str)

    # Only a token from the credential store: `PLEXARM_TOKEN` can be planted
    # by a repository's tracked settings, and this hook sends on its behalf.
    token, source = resolve_token_with_source(os.environ)
    if not token or source != "store":
        return _emit([NO_TOKEN_IN_STORE] if speaks else [], report)

    scopes = _loaded_scopes(session)
    if scopes is None:
        return _emit([UNDETERMINED_SOURCES] if speaks else [], report)
    # The plugin's agents load whenever the plugin does, and this hook runs
    # only when it is loaded: no setting source decides them.
    scopes = scopes | {SCOPE_PLUGIN}
    if headless:
        scopes = scopes - {SCOPE_REPO}

    # An unreadable session folder under `--report` is no folder: no repository scope.
    cwd = _session_cwd() if report else payload.get("cwd")
    found, overflowed = _discover(cwd if isinstance(cwd, str) else "", scopes, not report)
    if not found:
        return _emit([NO_AGENT_FILES], report) if report else 0
    agents, scope_of, local_lines = _resolve(found)

    status, answer = sync(token, agents)
    if status != 200:
        return _emit([no_send_line(status)] if speaks else [], report)

    lines = [report_line(answer, scope_of)] if report else []
    if overflowed:
        lines.append(
            f"Plexarm: stopped reading agent definitions at {MAX_AGENTS_SENT}; "
            "some were not sent."
        )
    told = notice(answer, scope_of)
    if told:
        lines.append(told)
    told_lines = line_notice(answer, scope_of)
    if told_lines:
        lines.append(told_lines)
    lines.extend(local_lines)
    told_updated = updated_notice(answer, scope_of)
    if told_updated:
        lines.append(told_updated)
    told_person = [line for line in local_lines if line.startswith(REPLACED_LEAD)]
    if told_updated:
        told_person.append(told_updated)
    return _emit(lines, report, "\n".join(told_person) or None)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a SessionStart hook must never fail loudly
        sys.exit(0)
