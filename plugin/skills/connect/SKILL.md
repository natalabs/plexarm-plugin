---
name: connect
description: Connect this Claude to the user's Plexarm account — create the account and the token if they do not exist, store the token where the plugin reads it, reconnect, and confirm with plexarm_whoami. Use when the user asks how to start using Plexarm, what to do after installing the plugin, why a Plexarm tool answered "No Plexarm token was found" or a 401, or how to rotate a token.
---

# Connect to Plexarm

The plugin is installed; this skill gets it signed in and its agents onto the account. Five steps,
and you do them **with** the user, not for them — the token is theirs and must never pass through
this chat. `/mcp` and `/plexarm:connect` are slash commands: ask the user to type them. Do not ask
for the token.

## 1. Is a token already stored?

Check presence only. Do not print the value.

```sh
security find-generic-password -s plexarm-api-token >/dev/null 2>&1 && echo stored || echo none   # macOS
secret-tool lookup service plexarm-api-token >/dev/null 2>&1 && echo stored || echo none                  # Linux
test -s "$HOME/.config/plexarm/token" && echo stored || echo none                                          # any POSIX machine
```

If one says `stored`, go to step 4. If `plexarm_whoami` then answers 401, the stored token is wrong
or revoked: continue from step 2 and store the new one the same way. The plugin uses the first store that holds a token (Keychain, then Secret Service, then the file), so replace the token in that same store.

## 2. Account and token

Tell the user, in this order:

1. If they have no Plexarm account: create a free one at <https://plexarm.com>.
2. Create a token at <https://plexarm.com/me>. **It is shown once** — keep the page open until
   step 3 is done.

## 3. Store it — in the user's own terminal, never in this chat

Give exactly one of these, for their operating system. Each **prompts** for the token, so it never
enters the shell history and never enters this conversation.

| | |
|---|---|
| macOS | `security add-generic-password -U -a "$USER" -s plexarm-api-token -w` — press return, paste at the prompt |
| Linux with a Secret Service | `secret-tool store --label="Plexarm API token" service plexarm-api-token` |
| Any POSIX machine, a file | `umask 077; mkdir -p ~/.config/plexarm; read -rs t && printf %s "$t" > ~/.config/plexarm/token` |

Windows: use Git Bash and the file form. The Windows Credential Manager is not read.

⛔ If the user pastes the token into the chat, tell them it is now in a transcript and should be
revoked at <https://plexarm.com/me> and replaced. Do not store a token you were given in chat.

## 4. Reconnect and confirm

The plugin reads the store once, when it connects. So: **`/mcp` → plexarm → Reconnect**, or start
a new session. Then call `plexarm_whoami`. A good answer names the person and the account and
lists the projects they can reach — say those back to the user. That is "connected".

## 5. Send the agents and say how many the account holds

Installing the plugin, `/reload-plugins` and a reconnect start no session event, so run the sync
yourself. It is the same one that runs at session start: it reads the agent files this session
loaded, in the folder the session started in wherever you run it from, sends each one's name, role
and manager, and adds and updates.

Before you run it, tell the user: it sends the names, roles and reporting lines of the agent files this session loaded, including those in ~/.claude/agents/, to their Plexarm account, and that PLEXARM_HOOKS_OFF=session_start_agent_sync switches it off. Run it only when the user is connecting or has asked for it.

```sh
"${CLAUDE_PLUGIN_ROOT}/hooks/session_start_agent_sync.py" --report
```

Give the user what it prints. If it says nothing was sent, do what that line says. If you are not
allowed to run it, ask the user to type `/clear`, which runs the same sync at the cost of this
conversation, or to start a new session.

Then call `plexarm_whoami` again and count the aliases in `agents` that start with `plexarm-`. Tell
the user, with both numbers: **"Your account holds X of the plugin's Y agents."** Y is the plugin's
count in the line the script printed. If `agents_truncated` is true, say the count is at least X.

- **X equals Y:** done.
- **X is below Y, and the script says agents were not added because only an owner or an admin can:**
  say so plainly. Nothing is wrong with the install, and this session cannot fix it. An owner or
  admin of the account types `/plexarm:connect` in their own session.
- **X is below Y otherwise:** an agent retired or deleted on the account keeps its alias, and the sync
  does not bring it back. Name the missing aliases. An owner or admin can set each one back to
  `active` with `plexarm_update`.
- ⛔ **Do not open the plugin's agents one at a time** with `plexarm_open_agent` to close a gap. The
  alias must be the file's `name`: never invent a naming scheme or ask the user to choose one.
  `plexarm_update` changes an agent's display name, role or manager in place, so nothing needs deleting and
  opening again, and a deleted agent keeps its alias.

Then point them at the `plexarm` skill: start a session with `plexarm_next`, record work as it
finishes with `plexarm_close` and `plexarm_record`.

## If it still does not connect

- The tool answers *"No Plexarm token was found on this machine"* — the store is empty on the path
  the plugin reads (Keychain, then Secret Service, then the file). Step 3 again, then reconnect.
- *"That credential was not accepted"* — the token is wrong or revoked. New token, step 2.
- If one Plexarm call fails with a credential, access or connection error but plexarm_whoami still answers after you reconnect, the content of that call was probably refused by network security before it reached Plexarm. Your token is not the cause, and rotating it will not help. Describe commands and code in plain words instead of pasting them, and send the call again. If plexarm_whoami fails too, the token is the problem: follow the steps to rotate it.
- A managed enterprise policy can switch the plugin's credential helper off. Then the plugin has no
  credential path at all; the server still works in any other MCP client with a token and the URL
  `https://api.plexarm.com/mcp`.
