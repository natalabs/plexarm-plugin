---
name: connect
description: Connect this Claude to the user's Plexarm account — create the account and the token if they do not exist, store the token where the plugin reads it, reconnect, and confirm with plexarm_whoami. Use when the user asks how to start using Plexarm, what to do after installing the plugin, why a Plexarm tool answered "No Plexarm token was found" or a 401, or how to rotate a token.
---

# Connect to Plexarm

The plugin is installed; this skill gets it signed in. Four steps, and you do them **with** the
user, not for them — the token is theirs and must never pass through this chat.

## 1. Is a token already stored?

Check presence only. Do not print the value.

```sh
security find-generic-password -a "$USER" -s plexarm-api-token >/dev/null 2>&1 && echo stored || echo none   # macOS
secret-tool lookup service plexarm-api-token >/dev/null 2>&1 && echo stored || echo none                  # Linux
test -s "$HOME/.config/plexarm/token" && echo stored || echo none                                          # any POSIX machine
```

If one says `stored`, go to step 4. If `plexarm_whoami` then answers 401, the stored token is wrong
or revoked: continue from step 2 and store the new one the same way.

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
| macOS | `security add-generic-password -a "$USER" -s plexarm-api-token -w` — press return, paste at the prompt |
| Linux with a Secret Service | `secret-tool store --label="Plexarm API token" service plexarm-api-token` |
| Any POSIX machine, a file | `umask 077; mkdir -p ~/.config/plexarm; read -rs t && printf %s "$t" > ~/.config/plexarm/token` |

Windows: use Git Bash and the file form. The Windows Credential Manager is not read.

⛔ If the user pastes the token into the chat, tell them it is now in a transcript and should be
revoked at <https://plexarm.com/me> and replaced. Do not store a token you were given in chat.

## 4. Reconnect and confirm

The plugin reads the store once, when it connects. So: **`/mcp` → plexarm → Reconnect**, or start
a new session. Then call `plexarm_whoami`. A good answer names the person and the account and
lists the projects they can reach — say those back to the user. That is "connected".

Then point them at the `plexarm` skill: start a session with `plexarm_board`, record work as it
finishes with `plexarm_close` and `plexarm_record`.

## If it still does not connect

- The tool answers *"No Plexarm token was found on this machine"* — the store is empty on the path
  the plugin reads (Keychain, then Secret Service, then the file). Step 3 again, then reconnect.
- *"That credential was not accepted"* — the token is wrong or revoked. New token, step 2.
- A managed enterprise policy can switch the plugin's credential helper off. Then the plugin has no
  credential path at all; the server still works in any other MCP client with a token and the URL
  `https://api.plexarm.com/mcp`.
