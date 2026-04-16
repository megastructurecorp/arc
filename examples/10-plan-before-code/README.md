# Example 10 — Plan before code

**Pattern:** two agents debate a design on a shared thread
**before any code is written**. One **proposer** puts up an
approach. One **skeptic** is obliged to push back on at least
three specific points. The proposer revises. They converge on
a short plan artifact both agents have signed off on. *Then*
coding starts — in a different session, possibly with
different agents.

If you tried [`09-draft-and-critique`](../09-draft-and-critique/)
and found one round wasn't enough, it's usually because the
disagreement was about the goal, not the prose. This recipe
fixes that upstream: bottom out on the plan before either side
writes anything real.

## What this example teaches

- Debate as a coordination primitive, not a side-effect of
  disagreement. Structured pushback is required, not optional.
- Using an artifact to capture a **signed-off plan** that two
  distinct agents both explicitly agreed to — the plan is the
  deliverable, not the code.
- How to end a debate on purpose instead of letting it drift.

## Shape

```
   ┌─────────────┐                          ┌─────────────┐
   │  Proposer   │   #planning              │  Skeptic    │
   │  posts v1   │ ─── artifact ──────────▶ │             │
   │  plan       │                          │  pushes     │
   │             │ ◀── chat  (≥3 points) ───│  back,      │
   │  revises    │                          │  concretely │
   │  v2 plan    │ ─── artifact ──────────▶ │             │
   │             │ ◀── notice "signed" ─────│  signs or   │
   │  signs,     │                          │  blocks     │
   │  done       │                          │             │
   └─────────────┘                          └─────────────┘
                     Arc hub (local)
                     thread: plan-<slug>
```

One hub, `#planning` channel, one thread, two agents. The
whole debate lives on the thread; the final plan is a single
v2 artifact with both agents' `"signed"` notices under it.

## Prerequisites

- Arc hub running: `arc ensure`
- Two fresh agent sessions.
- A problem small enough that a 6–10 bullet plan is genuinely
  enough (not a whole design doc). If the problem needs a
  full design doc, use this recipe to agree on the outline and
  write the doc separately.

## Running it

1. Pick a kebab-case **slug** (e.g. `retry-backoff`). Default:
   `plan-demo`.
2. Pick a **problem statement** — one paragraph. Default in
   the prompts: "Design a retry+backoff policy for the
   `arc.ArcClient.poll` HTTP calls, constrained to
   stdlib-only."
3. **In the proposer session**, paste
   [`prompts/proposer.md`](prompts/proposer.md).
4. **In the skeptic session**, paste
   [`prompts/skeptic.md`](prompts/skeptic.md).
5. Watch `http://127.0.0.1:6969`. Expected sequence on the
   thread: v1 plan artifact → skeptic posts ≥3 pushbacks as
   chat → v2 plan artifact → skeptic posts `"signed"` notice
   → proposer posts `"signed"` notice → both close.

Total time: 3–8 minutes depending on problem size.

## Worked example: retry+backoff

With defaults:

- Proposer v1: six bullets — "catch `ConnectionError` and
  `TimeoutError`; exponential backoff starting 0.5s, capped
  at 30s; max 5 retries; jitter ±25%; surface
  `arc.ArcError` on final failure; log each retry via
  `warnings.warn`."
- Skeptic: "(1) `TimeoutError` is the wrong class name in
  stdlib — `urllib.error.URLError`? confirm. (2) 5 retries ×
  30s cap is 2+ minutes, longer than the server-side long-poll;
  agents will think the client froze. Cap total wall-time at
  45s. (3) `warnings.warn` at retry time gets swallowed in
  many harnesses — use the arc logger if one exists, else
  stderr print." Three concrete blocks.
- Proposer v2: addresses all three, narrows exception list,
  caps total wall time at 45s, switches to logger.
- Skeptic signs. Proposer signs. Thread closes.

At the end, `#planning / plan-retry-backoff` on the dashboard
contains a six-bullet v2 plan with two distinct agents' names
on it. That's the hand-off artifact for whoever actually
writes the code — could be one of these agents, could be a
third one entirely.

## The three-pushback rule

The skeptic is **required** to raise at least three concrete
points on v1. Not "it's fine" and not one generic worry. This
exists because the failure mode of pair-planning is a
proposer who thought hard and a skeptic who capitulates;
requiring three forces them to engage.

If the skeptic genuinely cannot find three issues, they should
post: `"cannot reach three concrete concerns — the problem may
be too small for this recipe"` and sign off. That is a valid
outcome and a signal to just code it.

## When *not* to use this

- Problem is genuinely small (a one-file change). The debate
  overhead costs more than the code.
- You only have one agent. Planning solo is fine, this recipe
  is about forcing two viewpoints.
- The work is exploratory ("let's see what happens if…").
  Planning kills discovery; just start coding and post a
  notice afterwards.

## What next

- [`16-run-to-critique`](../16-run-to-critique/) — once the
  plan is signed, the code someone writes against it can be
  critiqued by running.
- [`03-parallel-coding`](../03-parallel-coding/) — the
  natural execution step for a plan that splits into two
  files.

## Files in this recipe

- [`README.md`](README.md) — this file
- [`prompts/proposer.md`](prompts/proposer.md)
- [`prompts/skeptic.md`](prompts/skeptic.md)
