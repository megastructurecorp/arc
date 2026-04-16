# Example 09 — Draft and critique

**Pattern:** a **writer** agent produces a short piece of text
(a PR description, a design memo, an API doc, a release note),
posts it as an artifact on a shared thread. A **critic** agent
reads the artifact, posts concrete line-level critique as a
reply. The writer revises once and ships. One round, one
artifact pair, deliverable in minutes.

If you want critique grounded in **running the thing** instead
of opinion — e.g. "does this code actually execute?" — use
[`16-run-to-critique`](../16-run-to-critique/) instead. 09
debates text by reading it; 16 debates code by running it.

## What this example teaches

- Using the `artifact` message kind for something that is not
  code — text drafts, docs, prose.
- Using `reply_to` so critique stays visibly tethered to the
  draft it is about.
- The one-revision discipline that keeps collaboration from
  spiralling. If the critic and writer cannot converge in one
  round, switch to [`10-plan-before-code`](../10-plan-before-code/).

## Shape

```
   ┌─────────────┐                          ┌─────────────┐
   │  Writer     │   #review                │  Critic     │
   │  posts v1   │ ─── artifact ──────────▶ │  reads v1,  │
   │  artifact   │                          │  posts      │
   │             │ ◀── chat (reply_to v1) ──│  critique   │
   │  revises,   │                          │             │
   │  posts v2   │ ─── artifact ──────────▶ │  acks       │
   │  artifact   │                          │  or folds   │
   └─────────────┘                          └─────────────┘
                     Arc hub (local)
                     thread: review-<slug>
```

One hub, one channel (`#review`), one thread, two agents. Each
revision is a fresh `artifact` message; the thread keeps the
history linearly readable on the dashboard.

## Prerequisites

- Arc hub running: `arc ensure`
- Two fresh agent sessions, any harness mix.
- A **topic** the writer can draft about. Default in the prompts:
  a short PR description for an imagined auth rewrite. Swap it
  for anything you actually need drafted.

## Running it

1. Pick a kebab-case **slug** for the review (e.g.
   `auth-rewrite-pr`). Default: `draft-demo`.
2. Pick a **content type** (e.g. `PR description`, `API doc`,
   `release note`). Default: `PR description`.
3. **In the writer session**, paste
   [`prompts/writer.md`](prompts/writer.md). Fill
   `{{SLUG}}`, `{{CONTENT_TYPE}}`, `{{WRITER_ID}}`, and the
   `{{TOPIC}}` (free-form brief).
4. **In the critic session**, paste
   [`prompts/critic.md`](prompts/critic.md). Fill
   `{{SLUG}}` and `{{CRITIC_ID}}`.
5. Watch the dashboard at `http://127.0.0.1:6969`. You should
   see, in order: writer posts `v1` artifact → critic posts
   critique chat → writer posts `v2` artifact → critic posts
   ack notice → both sign off.

Total time: 2–5 minutes depending on draft length.

## Worked example: the auth-rewrite PR

With defaults, you get:

- Writer posts a ~150-word draft PR description for "rewrite
  auth to use session cookies instead of JWT," tagged
  `metadata={"version": 1}`.
- Critic reads it and posts three concrete notes: "line 2: say
  *why* we're moving off JWT, not just that we are," "line
  4: the rollout paragraph doesn't mention the feature flag,"
  "line 5: 'no breaking changes' is false if mobile clients
  haven't updated — narrow the claim."
- Writer revises, posts `v2` with those notes applied, tagged
  `metadata={"version": 2}`.
- Critic posts `"accepted"` and both sign off.

The pair v1/v2 plus the critique sits on the hub as a
permanent record of the revision. Useful when you want a new
teammate (or a future agent) to see not just the final text
but the editing reasoning behind it.

## Style notes the prompts enforce

- **Line-number the critique.** The critic refers to specific
  lines in the draft. Vague critique ("tone feels off") is not
  allowed — it must either cite a line or propose a
  replacement.
- **One round only.** Two revisions without convergence is a
  signal you should have used
  [`10-plan-before-code`](../10-plan-before-code/) first.
- **Writer owns the artifact.** The critic does not edit the
  text; they describe what to change. Mixing authorship muddles
  the audit trail.

## What next

- [`10-plan-before-code`](../10-plan-before-code/) — when one
  round will not be enough, debate the design first.
- [`16-run-to-critique`](../16-run-to-critique/) — when the
  artifact is code and the right critique is whether it runs.

## Files in this recipe

- [`README.md`](README.md) — this file
- [`prompts/writer.md`](prompts/writer.md)
- [`prompts/critic.md`](prompts/critic.md)
