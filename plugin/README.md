# Plexarm for Claude Code

Plexarm is the operating system for self-organised AI agent teams: a hosted MCP server that holds
the goals, initiatives and action items your agents and people work toward, with priorities, owners,
dates and dependencies. It does not run agents. It is what they run on: every agent starts oriented,
knows what is next and why, and closes with what was done — so progress toward the goal is tracked
as it happens, not reconstructed later. It is for anyone coordinating work across many AI agents,
repos and projects, and it connects to any MCP client with a token and a URL. You need a Plexarm account and a token: create a free account at plexarm.com, create a token at plexarm.com/me, and run /plexarm:connect to store it and check the connection. To install in Claude Code, type these at its prompt, one at a time: /plugin marketplace add natalabs/plexarm-plugin, then /plugin install plexarm@plexarm.

The product is the Plexarm MCP server, which works in MCP clients that can send a token with each request. This plugin is a convenience
for Claude Code: a two-command install, a helper that reads your token from your operating system's
credential store, two skills, a team of 26 agents, and five optional hooks. The token is read on your own machine, so Claude surfaces other than Claude Code may not be able to sign in.

---

## Install and connect

Type each line at the Claude Code prompt, one at a time:

```
/plugin marketplace add natalabs/plexarm-plugin
```

```
/plugin install plexarm@plexarm
```

If Claude Code will not run these for you, type the slash lines yourself at its prompt.


1. **Install** the plugin with the two commands above.
2. **Create a free account** at <https://plexarm.com>.
3. **Create a token** at <https://plexarm.com/me> (it is shown once) and store it as described
   below — or run **`/plexarm:connect`**, which walks you through it without asking you to paste the
   token into the chat.
4. **Reconnect** (`/mcp` → plexarm → Reconnect, or start a new session) and ask Claude to run
   `plexarm_whoami`. It answers with your name and account.

Until a token is stored, the tools answer every call with a sentence saying where to put one.

Installing and reconnecting do not start a session, so the plugin's agents are sent at the next session start, after /clear, or when /plexarm:connect runs.

**Prerequisites:** a POSIX shell at `/bin/sh` and Python 3.10 or newer on your `PATH` as `python3`.
The credential helper needs the shell; the hooks need Python. Tested on macOS. On Windows, use
[Git for Windows](https://gitforwindows.org/) and run Claude Code from Git Bash.

### Your token

The plugin reads your token from the first of these that holds one, at the moment Claude Code
connects. There is no config file to edit.

| | |
|---|---|
| **macOS Keychain** | `security add-generic-password -U -a "$USER" -s plexarm-api-token -w` (prompts for the token) |
| **Linux Secret Service** | `secret-tool store --label="Plexarm API token" service plexarm-api-token` (prompts for the token) |
| **A file** | `umask 077`, `mkdir -p ~/.config/plexarm`, then `read -rs t && printf %s "$t" > ~/.config/plexarm/token`. The file must be mode `600` or `400` and owned by you, in a folder other accounts cannot write to |

A credential store protects the token at rest and from other user accounts. Like any credential on
your machine, it does not protect it from programs already running as you.

**Rotating:** store the new token the same way, then reconnect. On macOS that is `security add-generic-password -U -a "$USER" -s plexarm-api-token -w`. The plugin uses the first store that holds a token (Keychain, then Secret Service, then the file), so replace the token in that same store.

If one Plexarm call fails with a credential, access or connection error but `plexarm_whoami` still answers after you reconnect, the content of that call was probably refused by network security before it reached Plexarm. Your token is not the cause, and rotating it will not help. Describe commands and code in plain words instead of pasting them, and send the call again. If `plexarm_whoami` fails too, the token is the problem: follow the steps to rotate it.

`PLEXARM_TOKEN` is not used to send anything. The plugin's connection reads the store only.

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
- *"Ask the General Manager to put a team on the new pricing page."* — names the roles the job
  needs, sets the order and dispatches them.
- *"Ask the Chief of Staff what is late or unowned."* — sweeps the record and reports what it found.

---

## What is included

| | |
|---|---|
| **MCP connection** | to `https://api.plexarm.com/mcp`, started when the plugin is enabled |
| **Credential helper** | `helpers/plexarm-headers` — reads your token from the stores above and prints the Authorization header for `https://api.plexarm.com/mcp` and no other destination. It makes no network call and writes nothing |
| **Skills** | `plexarm` (how to pick up, record and close work) and `connect` (setup) |
| **Agents** | a team of 26, listed below |
| **Five hooks** | listed under [What leaves your machine](#what-leaves-your-machine). Each can be switched off |

---

## Your agent team

The plugin adds 26 agents to Claude Code. Each has a name, one discipline and a manager, and each
is written to record its work in Plexarm. Installing or updating the plugin adds these agents. Plugin updates do
not change copies you keep in your own agents folders.

| Name | Role | Reports to | Agent |
|---|---|---|---|
| Rio | General Manager | you | `plexarm-general-manager` |
| Vera | Chief of Staff | General Manager | `plexarm-chief-of-staff` |
| Amara | Head of Product | General Manager | `plexarm-head-of-product` |
| Priya | Head of Engineering | General Manager | `plexarm-head-of-engineering` |
| Kwame | Backend Engineer | Head of Engineering | `plexarm-backend-engineer` |
| Sofia | Frontend Engineer | Head of Engineering | `plexarm-frontend-engineer` |
| Oskar | Database Engineer | Head of Engineering | `plexarm-database-engineer` |
| Sasha | Quality Engineer | Head of Engineering | `plexarm-quality-engineer` |
| Robin | Head of Security | General Manager | `plexarm-head-of-security` |
| Nadia | Compliance & Privacy Lead | Head of Legal | `plexarm-compliance-privacy-lead` |
| Tomas | Delivery Manager | Chief of Staff | `plexarm-delivery-manager` |
| Oliver | Customer Success Lead | Head of Product | `plexarm-customer-success-lead` |
| Arjun | AI Engineer | Head of Engineering | `plexarm-ai-engineer` |
| Astrid | Platform Engineer | Head of Engineering | `plexarm-platform-engineer` |
| Lucas | Head of Design | General Manager | `plexarm-head-of-design` |
| Elodie | Product Designer | Head of Design | `plexarm-product-designer` |
| Yuki | Head of Data | General Manager | `plexarm-head-of-data` |
| Felix | Data Analyst | Head of Data | `plexarm-data-analyst` |
| Jin | Head of Marketing | General Manager | `plexarm-head-of-marketing` |
| Mei | Content & Communications Lead | Head of Marketing | `plexarm-content-communications-lead` |
| Noa | Conversion Specialist | Head of Marketing | `plexarm-conversion-specialist` |
| Emil | Head of Legal | General Manager | `plexarm-head-of-legal` |
| Lin | Release Engineer | Head of Engineering | `plexarm-release-engineer` |
| Leila | Mobile Engineer | Head of Engineering | `plexarm-mobile-engineer` |
| Tao | Head of Research | General Manager | `plexarm-head-of-research` |
| Hana | UX Researcher | Head of Research | `plexarm-ux-researcher` |

The agent team is a starting point: it is meant to help you get started in your Plexarm account
quickly, to show your agents how to work with Plexarm, and to be changed to fit how you work (see
[Customise an agent](#customise-an-agent)). It is not meant to replace the people in your
organisation or any of their functions, and the decisions taken with its help, for your business or
in any function, stay with you and your people. Use it with a person who reviews its work and
decides what happens next. What the Head of Security, Head of Legal and Compliance & Privacy Lead
produce is review and recommendation, not legal advice, a security certification or a compliance
determination.

Claude picks an agent from its description, so you can ask for one by role (*"ask the Head of
Security to review this change"*) or let Claude choose. For a job that spans more than one
discipline, start with the General Manager.

### Run the General Manager as your main session

Optional. To start a session led by the General Manager:

```
claude --agent plexarm:plexarm-general-manager
```

To make it the default for one project, add `"agent": "plexarm:plexarm-general-manager"` to that
project's `.claude/settings.json`; every session started there then begins as the General Manager.
Without either, your main session works as before and can dispatch any agent.

### Customise an agent

Copy an agent's file from the plugin's `agents/` folder into `~/.claude/agents/` (every project on
your machine) or into a project's `.claude/agents/` on your machine, keep its `name`, and edit the copy. Plugin
updates do not change it. Your copy and the original both stay available: your copy runs when it is
called by its plain name (`plexarm-head-of-product`, or `claude --agent plexarm-head-of-product`),
the original by its full name (`plexarm:plexarm-head-of-product`). Your copy is covered by the
plugin licence, like the original: you may change it on your machine for your own and your
organisation's work. You may not publish, share or redistribute it, so keep it out of any repository
other people can read, your team's included.

### Switch the team off

To stop Claude dispatching one agent, add its full name to `permissions.deny` in a Claude Code
settings file:

```json
{ "permissions": { "deny": ["Agent(plexarm:plexarm-head-of-marketing)"] } }
```

To switch the whole team off, disable the plugin: `/plugin` → **Installed** → **plexarm** →
**Disable**, or `claude plugin disable plexarm@plexarm`. That also switches off the Plexarm tools,
skills and hooks. `claude plugin enable plexarm@plexarm` turns them back on.

---

## What leaves your machine

What you and your agents write into Plexarm through the MCP tools is sent to the Plexarm service;
that is what the tools are for. Beyond that, the hooks send only what is listed below, over HTTPS,
to `api.plexarm.com`, with your token.

| Hook | Runs on | Sends | Default |
|---|---|---|---|
| `subagent_start_context.sh` | `SubagentStart` | nothing | on |
| `session_start_credential_check.py` | `SessionStart` | usually nothing; see below | on |
| `session_start_agent_sync.py` | `SessionStart`, on a new session, after `/clear` and on a resume; also when `/plexarm:connect` runs | your agents' and the plugin's agents' names, roles and managers, to `/records/sync-agents` | on |
| `session_start_due.py` | `SessionStart`, new sessions only | one request to `/records/board?limit=1` | on |
| `post_tool_use_dispatch.py` | `PostToolUse` on the `Agent` tool, in the background | asks `/records/dispatch/consent` each time an agent starts another; sends three fields to `/records/dispatch` only if you switched capture on | capture **off** until you switch it on in your Plexarm profile |

**Recording reminder.** When any subagent starts, adds two sentences to its context asking it to record its work in Plexarm if the work belongs to a project tracked there. It sends nothing. Switch off: `PLEXARM_HOOKS_OFF=subagent_start_context`.

**Credential check.** At session start, says so if no token is stored. If the Plexarm entries in
`~/.claude.json` (or the one in `CLAUDE_CONFIG_DIR`), the project's `.mcp.json`, `.claude/settings.json` or `.claude/settings.local.json` hold a literal token different
from the hooks' token, it sends `GET /records/identity` once per distinct token, with no body, to
learn which account each belongs to. If they belong to different accounts, it names both and stops the session where Claude Code honours that, rather than record anything. Otherwise it is silent and sends nothing. Switch off:
`PLEXARM_HOOKS_OFF=session_start_credential_check`.

**Agent roster sync.** When a session starts, after `/clear` and on a resume, and when `/plexarm:connect` runs, reads the frontmatter of the plugin's own agent
files, of the agent files in `~/.claude/agents/` and of this project's `.claude/agents/`, and sends
`POST /records/sync-agents` with each agent's `alias` (its `name`) and, if the file sets them, its
`display_name` (`plexarm_display_name:`), `role` (`plexarm_role:`) and reporting line
(`plexarm_manager:`). Where two files have the same `name`, the one in `~/.claude/agents/` is sent, then the one in this project's `.claude/agents/`, then the plugin's. A project file that replaces a plugin agent is named in the session, and it can set that agent's name, role and manager on your account, so open only repositories you trust. It does not send an agent's prompt, description or tools. It uses only the token in your
credential store. Only an owner or admin of your Plexarm account can change its roster: in their
session, a new agent is added, and an existing agent whose file differs is updated to match the
file, including a change made in Plexarm. It says which agents it updated. In anyone else's session
nothing changes, it prints one line if the team is not on the roster, and the agents work as usual.
If no token is stored, or Plexarm cannot be reached or does not answer normally, it may add one line to the session saying so and what to do.
The session that first adds the team to the roster adds one line to the session saying the team is installed and to dispatch the General Manager first for a job that spans more than one discipline. It reads only the
folders the session loads: a session started with `--setting-sources` reads `~/.claude/agents/`
only when the list includes `user`, and `.claude/agents/` only when it includes `project`. Where it
cannot tell which folders the session loaded, it sends none. Because `~/.claude/agents/` is not tied
to one project, the names of agents you keep there for other projects reach your Plexarm account
too. Switch off: `PLEXARM_HOOKS_OFF=session_start_agent_sync`; the agents stay available.

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

---

## Switching hooks

`hooks/switches.json` says which hooks are on. To override it on one machine, set either variable
to a comma-separated list of hook names, in your shell profile or in the `env` block of your Claude
Code `settings.json`:

```
PLEXARM_HOOKS_OFF=session_start_agent_sync
PLEXARM_HOOKS_ON=session_start_due
```

`OFF` wins over `ON`, and either wins over the file. A hook that is off exits without sending
anything.

## Files on your disk

The hooks keep small per-session state files in a temporary directory. If you set `PLEXARM_HOOK_LOG`
to a `.log` file in `~/.config/plexarm/` or in that temporary directory, they append one line per
run to it; any other path is ignored. Nothing else is written.

---

## Licence

The Plexarm Plugin Licence 1.0 — see [`LICENSE`](LICENSE), which governs; this is a summary. You may
use the plugin for your own work and your organisation's, including commercially, and change your
own copy on your machine, its agents included. You may not redistribute it, modified or not, sell,
rent or sublicense it, or use it to build a competing product.

## Privacy and support

What Plexarm does with the data it receives: <https://plexarm.com/privacy>. Terms:
<https://plexarm.com/terms>. Support: [help@plexarm.com](mailto:help@plexarm.com).

Security reports: help@plexarm.com
