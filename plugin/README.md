# Plexarm for Claude Code

Plexarm is the operating system for self-organised AI agent teams: a hosted MCP server that holds
the goals, initiatives and action items your agents and people work toward, with priorities, owners,
dates and dependencies. It does not run agents. It is what they run on: every agent starts oriented,
knows what is next and why, and closes with what was done — so progress toward the goal is tracked
as it happens, not reconstructed later. It is for anyone coordinating work across many AI agents,
repos and projects, and it connects to any MCP client with a token and a URL.

This plugin is a convenience wrapper. **The product is the Plexarm MCP server**, which works in
Claude Code, the desktop app, claude.ai on web and mobile, and any other client that speaks MCP.
What this plugin adds is a one-step install, a proper place to put your credential, a skill that
explains how the method works, and an agent that keeps the record itself in order.

---

## Install

```
/plugin marketplace add natalabs/plexarm-plugin
/plugin install plexarm@plexarm
```


### Prerequisites

The MCP tools need nothing beyond Claude Code and a token. **The four hooks are scripts**, and they
need two things Claude Code itself does not: **Python 3.10 or newer, reachable on your PATH as
`python3`**, and a **POSIX shell** at `/bin/sh`. macOS and Linux normally have both. On Windows,
install Python 3 and [Git for Windows](https://gitforwindows.org/) — Git Bash provides the shell.
Without them the MCP tools still work and only the four hooks are inert: Claude Code reports a hook
error at the moments they would have run, and that error is the hooks failing to start, not the
product failing.

The plugin is platform-agnostic by design and verified on macOS. Linux and Windows are unverified as
of 2026-09-07. If you run it there, tell us what happened — <https://plexarm.com>.

### Your token

You need a **Plexarm API token**. Create one at <https://plexarm.com/me> — it is shown once.

**Export it as `PLEXARM_TOKEN`, from wherever you keep secrets** — your own keychain item, a secrets
manager, your shell profile. That is the path the MCP server and every hook read **first** as of
1.4.1, and no step here asks you to edit JSON by hand. How you set it depends on where you launch
Claude Code from:

| | |
|---|---|
| **macOS or Linux, from a terminal** | `export PLEXARM_TOKEN=<your token>` in the profile that terminal reads — `~/.zshrc` or `~/.bashrc` — then open a new terminal |
| **Windows, PowerShell** | `$env:PLEXARM_TOKEN = "<your token>"` in your profile (`notepad $PROFILE`), then open a new window — or `setx PLEXARM_TOKEN <your token>` once, which stores it for every future process of your user account, then restart the terminal |
| **Windows, CMD** | `setx PLEXARM_TOKEN <your token>`, then open a new window |
| **A client launched from the Dock or the Start menu** | Apps launched from the Dock or Start menu do not see shell exports. Launch from a terminal, or use the client's secret prompt. On macOS, `launchctl setenv PLEXARM_TOKEN <your token>` reaches Dock-launched apps until you next log out; on Linux, a line `PLEXARM_TOKEN=<your token>` in `~/.config/environment.d/plexarm.conf` reaches the desktop session after you sign in again; on Windows, `setx` already covers it |

Whichever row you use, the token is stored in plain text somewhere only your user account can read —
a shell profile, the PowerShell profile, the Windows registry. That is the same posture as any
other API key on your machine. If you would rather not, use the client's own prompt, described next.

> ⚠️ **The token prompt at enable time still works and is now the FALLBACK, not the recommendation.**
> Values given there land in the client's shared `Claude Code-credentials` keychain item, which is
> **rebuilt on the client's own ~8-hourly OAuth refresh and drops entries it does not recognise** —
> so the credential silently reads as empty and the tools vanish. The *why* is under
> [What it sends, and when](#what-it-sends-and-when--changed-in-120-and-again-in-140). This README
> said the token "goes into your OS keychain, not into a settings file" until 2026-08-17, which
> described that shared item as though it were safe storage.

**Then turn on auto-update**, because it is off by default and it is not our choice:
`/plugin` → **Marketplaces** → **plexarm** → **Enable auto-update**. Anthropic's own marketplaces
default to on; every third-party one defaults to off, however you installed from. Without it you
stay on the version you installed until you update by hand.

---

## What you get

| | |
|---|---|
| **The MCP tools** | start automatically when the plugin is enabled — no `claude mcp add`, no config editing, no restart |
| **The `plexarm` skill** | how the method works: picking work up, recording what you did, filing what you noticed |
| **The `plexarm-chief-of-staff` agent** | **Vera** — sweeps the record for delays, gaps and unowned work, reports the numbers, fixes what is clerical and routes what is not. Added in 1.5.0; see below |
| **Four hooks** | one names the record obligation to each subagent at spawn; one checks, at the end, whether you left work open; one tells you when that check is not running; one sends the agent roster this session can see so work can be attributed — see all four below |
| **Nothing else** | see below |

### The agent, added in 1.5.0

`@plexarm:plexarm-chief-of-staff` — or just ask "where are we", "what is late", "what is
unassigned", and it routes there on its own.

It is a **reader and a triager**, not a doer. It sweeps goals, initiatives, items, findings and
incidents; reports counts with the scope it swept named; assigns owners, fixes missing links and
promotes work that is really planned out of the backlog; routes what needs domain judgment to
whoever owns it; and puts the decisions that are genuinely a human's — moving a deadline, accepting
a gap, calling a goal achieved — in front of you with the arithmetic attached.

Two things it will not do, deliberately: **it never closes a parent because its children are done**
(every item done is not an achieved initiative, and every initiative done is not an achieved goal),
and it never re-dates an overdue record to make a report look clean.

It makes no network calls of its own beyond the MCP tools you are already using. Like every plugin
agent, its `hooks`, `mcpServers` and `permissionMode` frontmatter would be ignored by Claude Code —
so it declares none.

### What it sends, and when — changed in 1.2.0, and again in 1.4.0

**Versions 1.0.0 and 1.1.0 sent nothing anywhere, and said so here. That is no longer true, and this
section names the version it changed in because the previous one promised it would.**

**Version 1.4.1 changed where the credential COMES FROM. It did not change what is sent, to whom, or
when.** The MCP server and the three hooks that hold a credential now read the environment variable **`PLEXARM_TOKEN`
first**, falling back to the plugin's own `api_token` setting only if that is unset. The setting is
no longer required at install.

*Why:* values stored in a plugin's `sensitive: true` setting live in the client's shared
`Claude Code-credentials` keychain item, alongside its own OAuth login and every MCP OAuth grant.
That item is rebuilt when the client refreshes its own access token — roughly every eight hours —
and the rebuild drops entries it does not know about. The credential then reads as empty, the record
guard silently stops checking, and the MCP tools disappear. This is
[anthropics/claude-code#62442](https://github.com/anthropics/claude-code/issues/62442), which is
**closed as not planned**, so it will not be fixed upstream. Sourcing `PLEXARM_TOKEN` from somewhere
the client does not own — a keychain item of your own, a secrets manager, your shell profile — takes
the plugin out of that failure entirely.



**As of version 1.4.0 there are TWO kinds of call, not one.** The paragraphs below described a single
call sending a single field; that was true for 1.2.0 and 1.3.x and is no longer true. The second one
is described after them, under *"And as of 1.4.0, a second call"*.

**As of version 1.2.0 the plugin makes one kind of network call of its own**: the record guard added
in 1.2.0 asks the Plexarm API, at the end of a session and at the end of each subagent, whether you
left work `in_progress` in this project. It goes to `https://api.plexarm.com/records/loose-ends`,
carries the token you configured, and sends **exactly one field — the project alias this repository
declares.** Nothing else: not your prompt, not your transcript, not the files you touched, not the
work you did.

Be aware of what that call still discloses even though its body is one word: **your API server
learns when your sessions end.** That is session cadence — when you work, how often, in which
projects. It is your own credential talking to your own account, and it is the same class of
information every hosted tool acquires; it is written here rather than left to be discovered.

**In a repository that has not opted in** (no marker, no `.plexarm`), subagents make **no call at
all**. The main session still makes one at the end, deliberately, carrying **no project name** — the
server refuses it and the refusal is what tells us the hook is installed and running rather than
silently absent. If you would rather it did not, turn the hook off; that is one line, below.

There is still no telemetry beyond that call, no analytics, no usage reporting, and no background
process.

**Version 1.3.0 added a third hook and did not change any of the above.** It runs at the *start* of
a session, makes no network call, holds nothing, and prints one line — only when the plugin has no
credential stored. See *Hook 3 of 3* below. This paragraph is here because the version before 1.2.0
promised that a change in what this plugin sends would name its version, and the honest way to keep
that promise is to also say when a version changed nothing.

### And as of 1.4.0, a second call — the agent roster, at the *start* of a session

**Version 1.4.0 added a fourth hook that sends something new, and it is the first call this plugin
makes that reads files off your disk.** The script is `hooks/session_start_agent_sync.py` — it runs
on `SessionStart`, goes to `https://api.plexarm.com/records/sync-agents`, and carries the token you
configured.

**What it reads.** The two directories Claude Code itself loads agent definitions from:
`~/.claude/agents/` and `<this project>/.claude/agents/`. For each `.md` file there it parses the
YAML frontmatter only.

**What it sends — three fields per agent, and nothing else:**

| Field | Where it comes from |
|---|---|
| `alias` | the frontmatter `name:` — the agent's identifier |
| `display_name` | a namespaced frontmatter key, if the file sets one |
| `role` | a namespaced frontmatter key, if the file sets one |

**It does not send the agent's prompt, its description, its tools, or any other part of the file**,
and it does not read any file that is not an agent definition. It never sends your transcript, your
code, or the work you did — that has not changed.

**It creates and never removes.** The server adds any alias it has not seen and leaves every existing
row exactly as it was: it cannot retire an agent, rename one, or overwrite a role. That is a property
of the request shape — there is no verb in the body to express anything else — and it is deliberate,
because what a session can see is session-dependent and an absent alias is evidence of nothing.

**Be aware of what this discloses**, on the same terms as the paragraph above about session cadence:
**your API server learns the names and roles of the agents on your machine**, including ones
belonging to projects other than this one, because `~/.claude/agents/` is not project-scoped. If you
maintain agents whose names are themselves sensitive — a client's name, an unannounced project — that
name reaches your account. It is your own credential talking to your own account, and it is written
here rather than left to be discovered.

**If you would rather it did not run, turn that hook off**; it is one line, the same as the others,
below. Everything else in the plugin works without it.

### Hook 1 of 4 — the spawn notice, in full

`hooks/subagent_start_context.sh`, fired on **`SubagentStart`** — when Claude Code spawns a
subagent. It prints two sentences reminding that subagent to record what it did, and exits.

| | |
|---|---|
| **What it sends** | nothing. No network call, no credential, no file written unless you ask for one |
| **What it reads** | nothing. It discards the payload on stdin |
| **Can it block you?** | **No.** `SubagentStart` is context-only; the client ignores a failure and the subagent runs regardless |
| **When it runs** | at spawn, before the subagent's work — never at the end, never in your own session |
| **Worst case** | the two sentences do not arrive, which is the same as not installing it |

**Why a hook at all**, since the same words could go in a file: because the words are not the point.
A brief that says *"report back"* gives the agent a channel that already works, and there are far
more dispatching prompts in the world than there are `CLAUDE.md` files. This is the only place the
reminder can sit that whoever wrote the dispatch cannot omit.

**To see it fire**, set `PLEXARM_HOOK_LOG` to a path and it appends one timestamped line per spawn.
Unset by default — it writes nothing to your disk unless you ask.

**To turn it off** without uninstalling: `/hooks` in Claude Code, or delete `hooks/hooks.json`.

### Hook 2 of 4 — the record guard, in full

`hooks/stop_record_guard.py`, fired on **`Stop`** (your session is finishing) and **`SubagentStop`**
(a subagent is finishing). It asks the Plexarm API one question — *did you take work in this project
and not put it down?* — and, if the answer is yes, gives that agent one more turn with the list and
the exact call to close it.

**⚠️ It asks about the USER — your Plexarm login — not about that agent.** Not about the account
either: the query filters on the person, so a colleague on the same account never appears, while
every session on *your* machine does. Plexarm attributes work to the person, never to the session,
so if you run several agents at once, one agent's unfinished work can appear in another's block.
*(1.3.1 made the message stop claiming authorship it could not verify. The word "account" was the
overshoot in that fix and was corrected to "user" on 2026-08-31, after the scope was verified
against the query rather than assumed — `gate_56` is the assertion that holds it.)* The message now asks *is this yours?*
first, shows the moment each item was picked up as the evidence, and tells the agent plainly not
to close anything it did not do. **An invented close note is worse than an item left open**, and a
message that asserted authorship it could not verify was an invitation to write one.

| | |
|---|---|
| **What it sends** | one HTTPS request to `api.plexarm.com`, with your token and one field: the project alias this repository declares. Nothing else leaves your machine |
| **What it reads** | `CLAUDE.md` and `.plexarm` **in the session's own directory only** — never recursively, never following a symlink, never a large file. It does not read your transcript |
| **Can it block you?** | **Yes — once per agent, and only when the API says you have an open item here.** It names the items, gives the call, and says what to do if the project is wrong. It never blocks the same agent twice |
| **When it runs** | at the end of a session and at the end of each subagent — never during the work |
| **Worst case** | it asks about the wrong project. See *"the one way it can be wrong"* below |
| **Files written** | scratch state in your OS temp directory, keyed on the session: which agents were already blocked, the parsed project name, the cached answer, and a one-line-per-firing log. Nothing under your home directory, nothing in your repository, nothing that outlives the temp directory |

**It only works in a repository that says which Plexarm project it is.** Either an HTML comment in
`CLAUDE.md`:

```
<!-- plexarm:project=your-project-alias -->
```

or a file called `.plexarm` containing that alias and nothing else. If you have both they must
agree; if they disagree, or if neither is there, **the hook does not block.** One key is allowed in
that comment, `project`, and that is deliberate and permanent — a repository must never be able to
tell this hook where to send your token.

**Everything fails open except one thing, and it is written here rather than buried.** No token, no
network, a slow server, a non-200, an unreadable file, a bug in the hook — every one of those lets
you finish. **The one way it can be wrong is a repository that declares the wrong project alias**: if
it names another project *you personally work in*, the answer comes back about that project's work
and the block is real but irrelevant. That is why the block message always names the alias and the
file it came from — so you can see it is wrong, say so, and finish. It will not stop that agent
again.

**Timeouts are in the hook**, not left to the client's default: 2 seconds to connect, 3 to read, no
retry. A server we cannot reach costs you about two seconds at the end of a session, once.

**To see what it did**, `PLEXARM_HOOK_LOG` works for this hook too — one line per firing, saying
whether the check ran and, if not, why. The same lines are in your temp directory without setting
anything.

**To turn it off** and keep the rest: `/hooks` in Claude Code, or remove the `Stop` and
`SubagentStop` entries from `hooks/hooks.json`.


### Hook 3 of 4 — the credential check, added in 1.3.0

`hooks/session_start_credential_check.py`, fired on **`SessionStart`**. It looks at one thing —
whether the plugin has a credential stored — and if it does, it does nothing at all.

| | |
|---|---|
| **What it sends** | nothing. No network call, no credential leaves the machine, nothing written to disk |
| **What it reads** | one environment variable, the one the client fills in from your configured token |
| **Can it block you?** | **No.** `SessionStart` is context-only |
| **When it runs** | at the start of a session, before your first message |
| **Worst case** | it prints a line about a credential you have already fixed |

**Why it exists, in one paragraph, because it is not a feature — it is scar tissue.** On our own
machine the record guard ran **407 times in a single day without checking anything**, because the
stored credential had vanished. The plugin was installed, enabled and valid the whole time. Nothing
was wrong that anyone could see: the guard fails open on purpose, so a dead guard and a clean session
look identical from where you sit. **The credential is kept by Claude Code, not by us** — resetting
or re-authenticating the client can empty it, and we get no say and no signal. So the guard cannot
promise to always work. This hook is the smaller promise it can keep: **if it stops working, you find
out in your next session.**

It checks that a credential is *present*, not that it still *works*. A token that exists but has been
revoked fails differently and shows up in the guard's own log instead.

### Hook 4 of 4 — the agent roster sync, added in 1.4.0

`hooks/session_start_agent_sync.py`, fired on **`SessionStart`**. It is documented in full above,
under *"And as of 1.4.0, a second call"*, rather than here — because it is the hook that reads files
off your disk, and what it sends belongs with the other disclosure and not at the end of a list.

---

## Using it

Once installed, ask Claude anything about your work record and it will reach for the tools. If you
want the method rather than the tools, load the skill:

```
/plexarm:plexarm
```

For a repository whose work lives in a specific Plexarm project, add a short block to that repo's
`CLAUDE.md` or `AGENTS.md` naming the project. The template is at
<https://plexarm.com> — it is about fifteen lines and it is the only per-repository step.

That block ends with a line the record guard reads:

```
<!-- plexarm:project=your-project-alias -->
```

It is invisible when the file is rendered, it is the same alias the prose above it names, and it is
what binds the hook to a project. Without it the guard never blocks in that repository.

---

## Before you install it

A plugin runs with your privileges, and Anthropic does not verify what is in a third-party one. That
cuts both ways, so:

- **Every file in here is meant to be read.** There are twelve counting this one: a manifest, an MCP
  config, a licence, this README, a skill, an agent, a hook registration, the **four** hook
  scripts it points at, and a `.gitattributes` that pins every file here to LF line endings so a
  Git-for-Windows checkout (`core.autocrlf=true` by default) does not rewrite the hooks' first line
  into a shebang no shell can find. That is the whole plugin. *(This bullet said seven and named two
  hook scripts until 1.5.0; 1.3.0 and 1.4.0 each added one and the count was not corrected with
  them. It said eleven until the `.gitattributes` was added on 2026-09-09.)*
- **The hooks are the part to read first**, because they are the only things here that execute.
  The agent and the skill are prose — they instruct Claude and run nothing.
  `subagent_start_context.sh` is mostly comments — **ten** lines actually execute, and what they do
  is print a fixed string. `session_start_credential_check.py` reaches nothing; it looks at **two**
  environment variables — `PLEXARM_TOKEN`, then the plugin option — and prints. *(This said "one
  environment variable" until 2026-08-17; the second arrived in 1.4.1.)*
  `stop_record_guard.py` and `session_start_agent_sync.py` are the
  two that make a network call and hold your token; their headers are written for a reader deciding
  whether to trust them, and the rules they follow — one key in the marker, the alias never reaching
  a shell, and a failure always meaning *do not block* — are stated at the top before any code.
- **There is no secret in this repository**, and there never will be. Every file that needs your
  token names an **unexpanded reference** to it — `${PLEXARM_TOKEN}` in `.mcp.json`, a plain
  environment read in the hooks — never a value. You supply it: exported as `PLEXARM_TOKEN`, or
  collected at enable time into the client's own store. It is in no file here and it is never passed
  on a command line, where every process on the machine could read it.
  `gate_53_no_credential_in_the_tree.py` is the check on that claim rather than the claim itself, and
  it reds on any credential-shaped literal in **any** git-tracked file. *(This bullet said the token
  is "collected at enable time and stored by your operating system", which described only the
  fallback path after 1.4.1 made the environment variable primary. Corrected 2026-08-17.)*
- Check the **Will install** inventory and the **Context cost** in the Discover tab before accepting.

---

## Licence

Restricted use — see [`LICENSE`](LICENSE). You may use it for your own and your organisation's work,
including commercially; you may not redistribute it.

Worth being straight about: Claude Code has no licence enforcement, so that file is a statement of
terms rather than a control. The real control is the API token, validated server-side on every call.

---

## Support

<https://plexarm.com>
