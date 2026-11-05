#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Plexarm — the SubagentStart context hook.
#
# Fires when a subagent is spawned. Prints one JSON object on stdout, exits 0.
#
# IT MAKES NO NETWORK CALL, HOLDS NO CREDENTIAL, AND CANNOT BLOCK.
# `SubagentStart` is context-only, so the worst failure available to this file
# is that the two sentences below do not arrive.
#
# The client adds this text to the subagent's own context at spawn, before the
# work starts, so it reaches every subagent whatever its brief says. A brief
# that ends "report back" otherwise sets up a channel that competes with
# recording the work.
#
# Keep it to two sentences: it is paid on every subagent of every session. The
# full guidance lives in the `plexarm` skill, which loads on demand.
#
# There is no agent-type filter. If one is ever wanted, the place for it is the
# `matcher` in `hooks.json`.
# ─────────────────────────────────────────────────────────────────────────────

set -u

# THE SWITCHBOARD. `switches.json` beside this file says whether this hook is
# on in this version; `PLEXARM_HOOKS_OFF` / `PLEXARM_HOOKS_ON` (comma-separated
# hook names, from the environment) override it per machine.
SWITCHES="$(dirname "$0")/switches.json"
if command -v python3 >/dev/null 2>&1; then
    SWITCH=$(PLEXARM_SWITCHES_FILE="$SWITCHES" python3 - <<'PY' 2>/dev/null
import json, os
name = "subagent_start_context"
def names(var):
    return {p.strip() for p in (os.environ.get(var) or "").split(",") if p.strip()}
if name in names("PLEXARM_HOOKS_OFF"):
    print("off")
elif name in names("PLEXARM_HOOKS_ON"):
    print("on")
else:
    try:
        with open(os.environ["PLEXARM_SWITCHES_FILE"], encoding="utf-8") as fh:
            print("on" if json.load(fh)["hooks"][name]["on"] else "off")
    except Exception:
        print("on")
PY
    ) || SWITCH=on
    if [ "$SWITCH" = "off" ]; then
        cat >/dev/null 2>&1 || true
        exit 0
    fi
fi

# Drain stdin. The hook payload is not read — nothing here depends on it — but
# leaving it unread means the client writes into a pipe with no reader.
cat >/dev/null 2>&1 || true

# THE INJECTED CONTEXT. Two sentences; the second says why reporting back is
# not enough.
CONTEXT='When you finish this work, if it belongs to a project tracked in Plexarm, record it — `plexarm_close` the item you were given, or `plexarm_record` what you did if there was no item. Reporting back to whoever dispatched you does not do this.'

# Optional local log, off unless `PLEXARM_HOOK_LOG` names a `.log` file directly
# in `~/.config/plexarm/` or in `<tmp>/plexarm-hook-<uid>/`. Any other path is
# ignored, and so is a symlink.
#
# Errors on this event show in the subagent's transcript, not the parent's, so
# a misfiring hook is easy to miss; this log makes it visible.
#
#     export PLEXARM_HOOK_LOG=~/.config/plexarm/hook.log
LOG="${PLEXARM_HOOK_LOG:-}"
case "$LOG" in
    *.log)
        LOG_DIR=$(cd -P -- "$(dirname -- "$LOG")" 2>/dev/null && pwd -P) || LOG_DIR=""
        CONFIG_DIR=$(cd -P -- "${HOME:-/nonexistent}/.config/plexarm" 2>/dev/null && pwd -P) || CONFIG_DIR=""
        # The temp root only when it is this user's own: a real directory, not a
        # link, owned by this uid and writable by no one else.
        BASE="${TMPDIR:-/tmp}/plexarm-hook-$(id -u)"
        TEMP_DIR=""
        if [ -d "$BASE" ] && [ ! -L "$BASE" ] && [ -O "$BASE" ] &&
            [ -z "$(find "$BASE" -prune \( -perm -020 -o -perm -002 \) 2>/dev/null)" ]; then
            TEMP_DIR=$(cd -P -- "$BASE" 2>/dev/null && pwd -P) || TEMP_DIR=""
        fi
        LOG_FILE="$LOG_DIR/$(basename -- "$LOG")"
        if [ -n "$LOG_DIR" ] && [ ! -L "$LOG_FILE" ] &&
            { [ "$LOG_DIR" = "$CONFIG_DIR" ] || [ "$LOG_DIR" = "$TEMP_DIR" ]; }; then
            printf '%s SubagentStart fired\n' \
                "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$LOG_FILE" 2>/dev/null || true
        fi
        ;;
esac

# Escape for JSON before interpolating, so that editing the string above can
# never emit invalid JSON.
ESCAPED=$(printf '%s' "$CONTEXT" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')

# `hookEventName` must be exactly `SubagentStart`. A wrong value is not an
# error — the context is dropped with no symptom at all.
printf '{"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":"%s"}}\n' "$ESCAPED"

exit 0
