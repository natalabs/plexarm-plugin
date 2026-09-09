#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Plexarm — the SubagentStart context hook.
#
# Fires when a subagent is spawned. Prints one JSON object on stdout, exits 0.
#
# IT MAKES NO NETWORK CALL, HOLDS NO CREDENTIAL, AND CANNOT BLOCK.
# `SubagentStart` is context-only: exit 2 shows stderr to the user and the
# subagent proceeds regardless. So the worst failure available to this file is
# that the two sentences below do not arrive — which is the state you are in
# without it. It cannot make anything worse.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE ARGUMENT IS PLACEMENT, NOT VOLUME
# ─────────────────────────────────────────────────────────────────────────────
# Measured 2026-08-07: four agents did substantial work and none recorded
# anything until asked. The cause was not forgetfulness — every brief ended
# "report back", which established a channel Plexarm then had to BEAT rather
# than merely be available to. And there are far more dispatching prompts in
# the world than there are CLAUDE.md files. We control neither.
#
# Every previous copy of this obligation sat in a brief, a CLAUDE.md or the MCP
# server's `instructions` — all three of which a dispatcher can omit, override
# or never write. This one is appended by the client at spawn, inside the
# subagent's own transcript, before the work. No prompt author can prevent it.
#
# That is the entire claim. If it fails it will fail for a different reason
# than the other three did, and that is what makes it worth running.
#
# ─────────────────────────────────────────────────────────────────────────────
# ⚠️ DO NOT LET THIS GROW
# ─────────────────────────────────────────────────────────────────────────────
# Two sentences. The temptation is to append the operating philosophy — that is
# the `plexarm` skill, which loads on demand and has no length limit. This
# string is paid on EVERY subagent of EVERY session, which is the single most
# expensive place in the whole design to add a word.
#
# `gate_68_the_injected_context_stays_two_sentences.py` runs this script and
# parses what it prints, so the cap is on what ARRIVES rather than on what the
# source says.
#
# ─────────────────────────────────────────────────────────────────────────────
# NO AGENT-TYPE FILTER, DELIBERATELY, AND IT IS NOT AN OVERSIGHT
# ─────────────────────────────────────────────────────────────────────────────
# `SubagentStart` takes a `matcher` on agent type, so this could stay silent
# for agent types that never own work (`Explore`, `Plan`). It does not, and the
# reason is that the measurement has not been run yet: this hook's whole
# quality gate is "dispatch a batch with it and a batch without, and score at
# funnel stage S5". A filter chosen before that runs can put a briefed agent
# type on the silent list, and the null result then reads as "more text in a
# new place buys nothing" when the real cause was the filter.
#
# The place to add one is the `matcher` in `hooks.json`, after the measurement.
# ─────────────────────────────────────────────────────────────────────────────

set -u

# Drain stdin. The hook payload is not read — nothing here depends on it — but
# leaving it unread means the client writes into a pipe with no reader.
cat >/dev/null 2>&1 || true

# THE INJECTED CONTEXT. Two sentences. The second one is the one doing the
# work: the failure being addressed is a channel that already exists and wins,
# not an agent that has never heard of us.
CONTEXT='When you finish this work, record it in Plexarm — `plexarm_close` the item you were given, or `plexarm_record` what you did if there was no item. Reporting back to whoever dispatched you does not do this, and nothing else will do it for you.'

# Optional local log, OFF by default and off unless a path is exported.
#
# Exit 2 on this event renders in the SUBAGENT's transcript, not the parent's.
# That is right — the person watching is not spammed — but it also means a
# misfiring hook is invisible to them. This is how you make it visible, and it
# is opt-in because nothing durable belongs on a user's disk by default.
#
#     export PLEXARM_HOOK_LOG=~/plexarm-hook.log
if [ -n "${PLEXARM_HOOK_LOG:-}" ]; then
    printf '%s SubagentStart fired\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$PLEXARM_HOOK_LOG" 2>/dev/null || true
fi

# Escape for JSON before interpolating. The string above contains neither a
# double quote nor a backslash today, so this changes nothing today — it is
# here so that editing the string cannot silently emit invalid JSON, which
# Claude Code would surface as plain stdout to the user rather than as context.
ESCAPED=$(printf '%s' "$CONTEXT" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')

# `hookEventName` must be exactly `SubagentStart`. A wrong value is not an
# error — the context is dropped with no symptom at all.
printf '{"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":"%s"}}\n' "$ESCAPED"

exit 0
