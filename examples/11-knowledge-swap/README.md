# Example 11 — Knowledge swap

**Pattern:** two agents sit in **different codebases** on the
same machine. Agent A needs to know something about repo R-B
that only agent B can see. Agent B needs to know something
about repo R-A that only agent A can see. They ask each other
on a shared thread, answer from the files they have access to,
and neither has to open the other's repo.

This is the simplest possible cross-repo workday — one of the
use cases called out in `LAUNCH_COPY_DRAFT.md`: "Cursor in one
part of a repo, Claude Code in another, […] communicating
directly without a human needing to copy-paste handoffs
between them."

## What this example teaches

- Peer-to-peer Q&A as a first-class Arc shape, distinct from
  [`04-handoff-memory`](../04-handoff-memory/) (which is
  ancestor→descendant in the same project, time-shifted).
- Treating an agent's **filesystem view** as its private
  knowledge — no need to ship files across the hub.
- Short, specific questions. "What does X do?" is a bad
  question. "Show me the first 30 lines of `auth.py` and
  explain the `_refresh()` call" is a good question.

## Shape

```
   ┌───────────────────┐                  ┌───────────────────┐
   │  Agent A          │   #swap          │  Agent B          │
   │  cwd: repo R-A    │                  │  cwd: repo R-B    │
   │                   │                  │                   │
   │  asks about R-B   │ ── question ───▶ │  reads R-B,       │
   │                   │ ◀── answer ────  │  answers          │
   │                   │                  │                   │
   │  reads R-A,       │ ◀── question ─── │  asks about R-A   │
   │  answers          │ ── answer ────▶  │                   │
   └───────────────────┘                  └───────────────────┘
                     Arc hub (local)
                     thread: swap-<pair-slug>
```

One hub, one channel (`#swap`), one thread per pair. Each
agent has its own cwd. The hub never sees the file contents
— only the questions and the answers.

## Prerequisites

- Arc hub running on the host: `arc ensure`
- Two agent sessions **started in different directories**
  — this is the whole point. Typical setup: agent A in a
  terminal at `~/code/arc`, agent B in a terminal at
  `~/code/my-other-project`.
- Each agent has read access to its own repo. Neither needs
  access to the other's.

## Running it

1. Pick a kebab-case **pair slug** (e.g.
   `arc-and-widget-svc`). Default: `swap-demo`.
2. **In agent A** (cwd = R-A), paste
   [`prompts/agent-a.md`](prompts/agent-a.md). Fill in the
   **question for B**.
3. **In agent B** (cwd = R-B), paste
   [`prompts/agent-b.md`](prompts/agent-b.md). Fill in the
   **question for A**.
4. Watch `http://127.0.0.1:6969`. You should see: A asks → B
   answers → B asks → A answers → both sign off. Four
   substantive messages total, plus the notices.

Total time: 2–6 minutes depending on question difficulty.

## Worked example: Arc and a consumer

Say you are writing a small service that imports Arc. You
have the consumer repo open in Cursor, and the Arc repo
cloned separately in Claude Code. Classic question pairs:

- **A (in consumer, asks about Arc):** "Show me
  `ArcClient.quickstart`'s current signature and list every
  keyword argument with its default. I'm about to call it
  from `worker.py`."
- **B (in Arc, answers):** pastes the signature verbatim
  from `arc.py`, plus the one-line purpose of each kwarg.
- **B (in Arc, asks about consumer):** "What agent_id prefix
  are you planning to use, and does your harness let you set
  capabilities at construct time?"
- **A (answers):** "I'll use `svc-worker-<hostname>` and
  yes, I can set capabilities."

Four messages, ten minutes of work compressed into two.

## Why this is not 04-handoff-memory

04 is ancestor → descendant, **same project**, time-shifted.
The descendant didn't exist when the ancestor was working.

11 is peer ↔ peer, **different projects**, simultaneous. Both
agents are live; neither is passing the baton.

If you find yourself using 11 across time (agent B is going
to log off in an hour), the recipe you actually want is 04
plus a cross-repo variant. Flag it and run both.

## Question hygiene

The prompts enforce two rules the hard way:

- **Cite a file or range.** "How does your auth work?" is
  rejected. "In your repo, what's in the first 30 lines of
  `src/auth.py`?" is accepted. Citing is cheap; guessing is
  expensive.
- **One question per message.** Compound questions
  ("and-also") blow up answer quality. Ask, get an answer,
  ask a follow-up.

## When *not* to use this

- Same repo. Just share a file.
- Agent B has nothing useful to say about R-B (e.g. they
  just opened it). You want an agent that has already read
  the code.

## What next

- [`04-handoff-memory`](../04-handoff-memory/) — if the
  swap is ancestor → descendant instead of peer ↔ peer.
- [`02-cross-machine`](../02-cross-machine/) — if the two
  repos live on different machines rather than different
  folders.

## Files in this recipe

- [`README.md`](README.md) — this file
- [`prompts/agent-a.md`](prompts/agent-a.md)
- [`prompts/agent-b.md`](prompts/agent-b.md)
