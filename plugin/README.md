# Plexarm for Claude Code

Plexarm is the operating system for self-organised AI agent teams: a hosted MCP server that holds
the goals, initiatives and action items your agents and people work toward, with priorities, owners,
dates and dependencies. It does not run agents. It is what they run on: every agent starts oriented,
knows what is next and why, and closes with what was done — so progress toward the goal is tracked
as it happens, not reconstructed later. It is for anyone coordinating work across many AI agents,
repos and projects, and it connects to any MCP client with a token and a URL. You need a Plexarm account and a token: create a free account at plexarm.com, create a token at plexarm.com/me, and run /plexarm:connect to store it and check the connection. To install in Claude Code: /plugin marketplace add natalabs/plexarm-plugin, then /plugin install plexarm@plexarm.

The product is the Plexarm MCP server, which works in any MCP client. This plugin is a convenience
for Claude Code: a one-step install, a helper that reads your token from your operating system's
credential store, two skills, an agent, and six optional hooks.

---

## Install and connect

```
/plugin marketplace add natalabs/plexarm-plugin
/plugin install plexarm@plexarm
```


1. **Install** the plugin with the two commands above.
2. **Create a free account** at <https://plexarm.com>.
3. **Create a token** at <https://plexarm.com/me> (it is shown once) and store it as described
   below — or run **`/plexarm:connect`**, which walks you through it without asking you to paste the
   token into the chat.
4. **Reconnect** (`/mcp` → plexarm → Reconnect, or start a new session) and ask Claude to run
   `plexarm_whoami`. It answers with your name and account.

Until a token is stored, the tools answer every call with a sentence saying where to put one.

**Prerequisites:** a POSIX shell at `/bin/sh` and Python 3.10 or newer on your `PATH` as `python3`.
The credential helper needs the shell; the hooks need Python. Tested on macOS. On Windows, use
[Git for Windows](https://gitforwindows.org/) and run Claude Code from Git Bash.

### Your token

The plugin reads your token from the first of these that holds one, at the moment Claude Code
connects. There is no config file to edit.

| | |
|---|---|
| **macOS Keychain** | `security add-generic-password -a "$USER" -s plexarm-api-token -w` (prompts for the token) |
| **Linux Secret Service** | `secret-tool store --label="Plexarm API token" service plexarm-api-token` (prompts for the token) |
| **A file** | `umask 077`, `mkdir -p ~/.config/plexarm`, then `read -rs t && printf %s "$t" > ~/.config/plexarm/token`. The file must be mode `600` or `400` and owned by you |

A credential store protects the token at rest and from other user accounts. Like any credential on
your machine, it does not protect it from programs already running as you.

**Rotating:** store the new token the same way, then reconnect.

**`PLEXARM_TOKEN`** is read by some hooks if the store is empty. The MCP tools read the store only.

**Managed Claude Code policies** can disable `headersHelper`. If yours does, the plugin cannot
connect; the server still works in any other MCP client with a token and the URL.

**Updates:** to receive new versions automatically, go to `/plugin` → **Marketplaces** →
**plexarm** → **Enable auto-update**.

---

## Examples

Once connected, ask Claude in plain words:

- *"What should I pick up next in my project?"* — reads the one item to work on now, and why.
- *"File a bug: the export button does nothing on the settings page."* — puts a new item on the record.
- *"Close the item I just finished and record what was done."* — closes it with a note of what changed.
- *"@plexarm-chief-of-staff, what is late or unowned?"* — sweeps the record and reports what it found.

---

## What is included

| | |
|---|---|
| **MCP connection** | to `https://api.plexarm.com/mcp`, started when the plugin is enabled |
| **Credential helper** | `helpers/plexarm-headers` — reads your token from the stores above and prints the Authorization header for `https://api.plexarm.com/mcp` and no other destination. It makes no network call and writes nothing |
| **Skills** | `plexarm` (how to pick up, record and close work) and `connect` (setup) |
| **Agent** | `plexarm-chief-of-staff` — sweeps the record for late, unowned or unlinked work and reports what it found. It uses only the MCP tools |
| **Six hooks** | listed below. Each can be switched off |

---

## What leaves your machine

What you and your agents write into Plexarm through the MCP tools is sent to the Plexarm service;
that is what the tools are for. Beyond that, the hooks send only what is listed below, over HTTPS,
to `api.plexarm.com`, with your token.

| Hook | Runs on | Sends | Default |
|---|---|---|---|
| `subagent_start_context.sh` | `SubagentStart` | nothing | on |
| `session_start_credential_check.py` | `SessionStart` | usually nothing; see below | on |
| `session_start_agent_sync.py` | `SessionStart` | your agents' names, to `/records/sync-agents` | on |
| `session_start_due.py` | `SessionStart`, new sessions only | one request to `/records/board?limit=1` | on |
| `post_tool_use_dispatch.py` | `PostToolUse` on the `Agent` tool, in the background | asks `/records/dispatch/consent` each time an agent starts another; sends three fields to `/records/dispatch` only if you switched capture on | capture **off** until you switch it on in your Plexarm profile |
| `stop_record_guard.py` | `Stop` and `SubagentStop` | the repository's project alias, to `/records/loose-ends` | **off** |

**Credential check.** At session start, says so if no token is stored. If the Plexarm entries in
`~/.claude.json`, the project's `.mcp.json` or `.claude/settings.json` hold a literal token different
from the hooks' token, it sends `GET /records/identity` once per distinct token, with no body, to
learn which account each belongs to. If they belong to different accounts, it names both and asks
Claude to stop rather than record anything. Otherwise it is silent and sends nothing. Switch off:
`PLEXARM_HOOKS_OFF=session_start_credential_check`.

**Agent roster sync.** At session start, reads the frontmatter of the agent files in
`~/.claude/agents/` and in this project's `.claude/agents/`, and sends `POST /records/sync-agents`
with each agent's `alias` (its `name`) and, if the file sets one, its reporting line
(`plexarm_manager:`). For agents in `~/.claude/agents/` it also sends `display_name` and `role` if
the file sets them. It does not send an agent's prompt, description or tools. Because
`~/.claude/agents/` is not tied to one project, the names of agents you keep there for other
projects reach your Plexarm account too. Switch off: `PLEXARM_HOOKS_OFF=session_start_agent_sync`.

**Due notice.** A new session asks `GET /records/board?limit=1`, with no body. If routines or
reminders are due for you, it lists them; otherwise it prints nothing. Switch off:
`PLEXARM_HOOKS_OFF=session_start_due`.

**Dispatch capture.** Each time Claude Code starts an agent, the hook asks Plexarm in the background
whether you have switched dispatch capture on in your Plexarm profile — `GET
/records/dispatch/consent`, with no body — and does nothing more unless the answer is yes. When it
is on, it sends `POST /records/dispatch` with three fields: the type of the agent that started it
(empty for the main session), the type of the agent started, and Claude Code's id for that tool
call. It does not send the prompt, the description, the agent's output, your session id or any path.
It uses only the token in your credential store. Switch it off in your Plexarm profile; to stop the
question too, `PLEXARM_HOOKS_OFF=post_tool_use_dispatch`.

**Record guard (off by default).** When switched on (`PLEXARM_HOOKS_ON=stop_record_guard`), at the
end of a session and of each subagent it sends `POST /records/loose-ends` with the project alias the
repository declares, and asks whether work was left in progress there. If it was, it gives that
agent one more turn with the list, once per agent. A repository names its project with a line in
`CLAUDE.md` — `<!-- plexarm:project=your-project-alias -->` — or a file named `.plexarm` holding the
alias.

---

## Switching hooks

`hooks/switches.json` says which hooks are on. To override it on one machine, set either variable
to a comma-separated list of hook names, in your shell profile or in the `env` block of your Claude
Code `settings.json`:

```
PLEXARM_HOOKS_OFF=session_start_agent_sync
PLEXARM_HOOKS_ON=stop_record_guard
```

`OFF` wins over `ON`, and either wins over the file. A hook that is off exits without sending
anything.

## Files on your disk

The hooks keep small per-session state files in a temporary directory. If you set `PLEXARM_HOOK_LOG`
to a `.log` file in `~/.config/plexarm/` or in that temporary directory, they append one line per
run to it; any other path is ignored. Nothing else is written.

---

## Licence

The Plexarm Plugin Licence 1.0 — see [`LICENSE`](LICENSE). You may use the plugin for your own work
and your organisation's, including commercially. You may not redistribute, sell or sublicense it, or
use it to build a competing product.

## Privacy and support

What Plexarm does with the data it receives: <https://plexarm.com/privacy>. Terms:
<https://plexarm.com/terms>. Support: [help@plexarm.com](mailto:help@plexarm.com).
