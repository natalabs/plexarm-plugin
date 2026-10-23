---
name: plexarm
description: How to work through Plexarm — pick work up, record what you did, and file what you noticed. Use when starting a session in a repo that uses Plexarm, when you finish a piece of work, when you find a defect or a gap in passing, or when you need to know why something is the way it is.
---

# Working through Plexarm

Plexarm is the operating system for self-organised AI agent teams: the goals, initiatives and
action items an organisation works toward, with priorities, owners, dates and dependencies — and
what it decided and what actually happened, written as the work happens by whoever does it. Not a
task tracker you update afterwards.

You are one of the people who writes it.

This skill is about **when and why** — which of the things in front of you is worth doing, and what
happens if you skip it.

**It deliberately names no tools and lists no fields.** They are already in your toolkit with their
own descriptions, and those cannot go stale; a copy of them here can. So when you need to know what
exists or what it takes, look at the tools. When you need to know whether this is a bug or a gap,
or why the record matters more than the summary you were about to write, read on.

---

## The one thing that matters most

**Finishing a piece of work includes writing down that it is finished.**

Not because a process demands it. Because of what the record is *for*: someone in three months asks
*"why is this like this?"* or *"did we already try that?"*, and the answer either exists or it
doesn't. A prose summary in a chat window reaches one reader, once, and then it is gone. The record
is what can still answer the question later.

This is the step that gets skipped, and it is worth understanding *why* it gets skipped rather than
just being told not to. **The work being done is a very strong stopping signal.** You finish, you
have something to report, and reporting feels like completion. It isn't — the report reaches whoever
asked; the record reaches whoever asks next.

If you were asked to "report back", do both. They are not substitutes.

---

## The operating sequence

```
planned  ──►  in_progress  ──►  done
   ▲              ▲                ▲
   │              │                └─ you close it, with what was actually done
   │              └─ YOU set this, when you actually begin
   └─ prioritised and waiting. This is where work is dispatched FROM.
```

**Start a session with `plexarm_next`**: the one thing to pick up now in your project, why, and what
closing it needs.

**Most work you are handed is already planned.** It has been thought about, prioritised, and it is
waiting for someone to pick it up.

**The backlog is not a queue.** It is where unformed ideas sit — things nobody has decided are worth
doing yet, or when, or by whom. Work waiting to be started is *planned*, not *backlog*. Confusing
the two makes the backlog look like a to-do list and makes planned work look optional.

### Why *you* mark it started, and not whoever assigned it

Because the moment you mark it started is recorded, and it is only true if you do it when you
actually begin.

A dispatcher who marks ten items as started when handing them out has stated that ten pieces of work
began simultaneously. Nine of them didn't. Everything computed from that — how long things take,
what is genuinely in flight right now, whether an estimate held — is wrong, and wrong in a way
nothing will ever flag, because a plausible timestamp looks exactly like a real one.

**Only you know when the work started.** That is the whole reason the step is yours.

### Some work arrives on a clock

A routine (a re-check every Monday, a sweep three times a week) and a one-off reminder are both
raised on the board when they come due, beside the work in flight, and a routine due today can be
the one thing you are handed at the start. Nothing else raises them, so a session that reads neither
never learns they are due. You satisfy a due routine by recording
what you did with the routine as its subject. Closing it is refused: a routine never finishes, only
each occurrence of it does.

A reminder says what to do when it comes up, in your words, not the record's title. Place one with
`plexarm_remind(title, at, about)` on a record, or `plexarm_remind(title, at, project)` about no
record at all; on `plexarm_file` or `plexarm_update`, `remind_at` travels with `remind_title`, and
one without the other is refused. Put the why in `notes`: it is read with the reminder's `sch-` id,
never shown on the board. To change one, `plexarm_update` that `sch-` id with `title`, `notes` or
`raise_at`; `plexarm_record` with `satisfies` set to it completes it.

### End the session by wrapping it

Plexarm already knows every record this session created, progressed and closed. Its wrap ends the
session and hands that back as a table: put the table in your final reply instead of typing one
from memory, which is the version that leaves things out. Under it, say plainly what is actually
better than when you started and what was only motion. That part is judgement, and nothing writes it
for you.

### Read the item before you start it

The item is the specification, not a pointer to one. Whoever wrote it put the reasoning, the
constraints, and the traps in it — including the things that already went wrong once. Starting from
the title and inferring the rest is how a trap gets stepped in twice.

---

## Writing what happened

Three things are worth recording, and only one of them is "the work".

**What you did.** Close the item with what was actually done — not a restatement of what it asked
for. If you did something different from what was planned, that difference is the most valuable
sentence in the note.

**What happened that wasn't the work.** A defect you found in passing. A decision you took and the
reason. Something that failed, and why. An approach you tried and abandoned. These are not items —
nobody is going to do them — but they are exactly what someone needs later.

**What you did *not* do.** A gap you noticed and decided to leave is worth more written down than
left in your head, because the alternative is that the next person finds it again from scratch and
has to re-derive whether it matters.

### Say which is which

What you *observed* and what you *think it means* are both worth having, and they are not the same
thing. Someone reading later needs to know which parts they can rely on and which parts were your
read of the situation. Write both; mark the boundary.

Your judgement is not noise to be filtered out. It is often the most useful thing in the record —
you were there, and nobody else was.

### Adding to a record is not the same as rewriting it

Updating a record can **add** to a field, leaving what is already there untouched and letting Plexarm
write who added it and when — or it can **replace** that field entirely. A record that several people
touch over weeks wants the first: the reason a decision was taken is usually somewhere in what the
earlier writer put there, and a replace deletes it with no trace that anything was lost. Reach for the
replace only when what is there is *wrong*, rather than merely incomplete.

---

## A decision is a piece of work, and the ruling goes inside it

Deciding is work — often the most consequential work in a week — and it finishes the same way anything
else does: the item is closed, and **the ruling itself is written into it as you close it**, in the
words it deserves to be quoted in later. That is not optional bookkeeping on this kind of item; a
decision that closes without saying what was decided is refused.

Write the ruling, not a description of having ruled. *"We decided the export question"* is a record
that the meeting happened. The next person needs the answer.

**The argument belongs in a document, linked from the item.** The item carries what was decided and by
whom; the file carries why, and how long it took to get there.

**Work that exists because of a ruling should say so**, by naming the decision as something it depends
on. That is how a later reader arrives at the reasoning without knowing it exists.

**And a decision is never edited into a different decision.** Changing your mind is itself a decision:
a new one, which names the earlier ruling it replaces and says **which premise of it stopped being
true**. The old one stays as it was. It was taken, it was right on what was known then, and the pair
is worth more than either alone — the reversal is the more valuable half, and it disappears if you
overwrite the thing it reverses.

---

## Is it a bug, an incident, or a gap you're accepting?

These get confused, and the confusion is expensive in one specific direction.

**A bug is work.** Something you built, believe works, and that is failing. It is broken against its
own intent, so someone should fix it, and it goes on the list of things to do.

**A gap is not that.** A gap is something you never built, or something working exactly as designed
where the design is the limitation. Nothing is failing — the code is doing what it was written to
do — and that is precisely why it goes unnoticed until someone writes it down. "It behaves
correctly" and "it is sufficient" are different claims, and a gap is where they come apart.

**An incident is something that went wrong in live operation** — an outage, corrupted data, a leak.
It is not work; it is an event, with a timeline. The work that comes *out* of it is separate, and
points back at it.

**An accepted gap is a limitation somebody decided to live with.** That decision is real and worth
recording — but it is a decision, and it has to have been made.

### The confusion that costs

**A defect nobody has decided to accept is a bug, not an accepted gap.** Filing it as accepted files
it into the register of things that are *fine*, which is the one place nobody looks for things that
are broken. It disappears, and it looks tidy while disappearing.

Ask: has someone actually decided to live with this? If the answer is "no, I just don't have time",
it is a bug.

### What makes a gap *accepted* rather than *forgotten*

**The condition that would make you re-check it.**

This is the distinctive part of the whole method and it is worth being concrete about. "We know
about this, it's fine for now" is forgetting with extra steps — in six months nobody remembers
whether it was examined or ignored, so it gets examined again, or worse, trusted.

An accepted gap carries the observable thing that would change the answer. *"Fine while there is one
user; re-check when there are two."* *"Fine at this volume; re-check if it doubles."* Now it is a
decision with an expiry condition attached, and the review is mechanical rather than a matter of
somebody remembering to worry.

The condition has a field of its own: `re_check`, set when you observe the finding or when you update
it. It is not a sentence in `notes` — notes are for watch-outs and context, and nothing can query a
sentence. Accepting a finding with an empty `re_check` is refused, and the refusal names the field.

**No condition, not accepted.**

---

## Four things that will otherwise bite you

**Name the project.** More than one is usually reachable, and a record's project is fixed when it is
written. Getting it wrong is not something you can quietly correct afterwards.

**Long content belongs in a file.** The fields are wide, but they are not documents. A handover, an
analysis, a full report — write the file and link it. Do not compress a document down until it fits
in a field; the record is then the wrong shape for its content and the content is worse.

**Link both directions.** Work you are doing *because of* something should say so, and something you
noticed *while* doing a piece of work should name that work. An id written into prose is not a link
— nothing can walk to it. Use the field that exists for it.

**Nothing is deleted.** Statuses move; records stay. Deletion is reversible and is not how you fix a
mistake — correcting the record is. A record that was wrong and got corrected is more useful than
one that was quietly removed, because the correction is itself information.

---

## Before the code, when the question is *why*

When you want to know **what** something does, read the code.

When you want to know **why** it is that way — why this approach and not the obvious one, whether
this has come up before, what was decided and by whom — search the record first. The code cannot
tell you why; it only shows the outcome of a decision whose reasoning lives somewhere else.

This is the habit that takes longest to form, because searching a codebase is a reflex and searching
a work record is not. It is also the one that pays back most, because the alternative is re-deriving
a conclusion someone already reached, and possibly reaching a different one.

---

## If Plexarm gets in your way, say so

You are a user of this system. If a tool refused something reasonable, if you couldn't find a field,
if something took three calls that should have taken one, if a description was wrong or missing —
say so. Do not work around it silently, and do not wait to be asked.

What worked *well* is equally useful and far rarer, so say that too.

Send it with **`plexarm_feedback(type, text)`**. It goes to the people who build Plexarm. It is
write-only, takes no project and creates no record in your account. `type` is one of `bug`, `ask` or
`compliment` — `ask` covers anything you needed that is not there or not the way you needed it. Send
one thing — the one that cost you most, not a list.
