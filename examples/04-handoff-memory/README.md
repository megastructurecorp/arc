# Example 04 — Handoff Memory

**Pattern:** an ancestor chat session, running low on context, hands off live
to a fresh descendant chat session through an Arc hub. The descendant can ask
the ancestor clarifying questions *before* the ancestor's context is lost.

**When to use this instead of a static handoff document:**

- Your ancestor session has accumulated tacit knowledge (decisions made,
  dead-ends ruled out, half-formed hypotheses) that a written handoff doc
  would flatten or lose.
- The descendant needs to be able to *interrogate* the ancestor, not just
  read a summary. A handoff doc is a monologue; this recipe is a dialogue.
- You want an audit trail of the handoff — the thread is persistent on the
  hub and can be re-read by a future third agent.
- You want the ancestor to remain reachable as a reviewer while the
  descendant ramps up, without running two full agents in parallel for the
  whole session.

**When *not* to use this:**

- Trivial, well-scoped handoffs. If the whole handoff fits in 200 words of a
  static doc, just write the doc.
- One-shot sessions that will never have a successor. Handoff memory is for
  ongoing work.

## Topology

```
┌──────────────────────────┐                 ┌──────────────────────────┐
│ Ancestor session         │                 │ Descendant session       │
│ agent_id:                │                 │ agent_id:                │
│   cc-ancestor-<project>  │                 │   cc-descendant-<project>│
│ (context at ~90% full,   │                 │ (fresh context)          │
│  knows the full history) │                 │                          │
└────────────┬─────────────┘                 └────────────┬─────────────┘
             │                                            │
             │         posts & polls over HTTP            │
             ▼                                            ▼
         ┌───────────────────────────────────────────────────┐
         │              Arc hub (single hub mode)            │
         │                                                   │
         │  channel: #handoff                                │
         │  thread_id: handoff-<project>                     │
         │                                                   │
         │  artifacts:                                       │
         │    - decisions-so-far                             │
         │    - dead-ends-ruled-out                          │
         │    - open-questions                               │
         │    - current-plan                                 │
         │                                                   │
         │  messages:                                        │
         │    ancestor → notice "handoff ready"              │
         │    descendant → question "what about X?"          │
         │    ancestor  → answer "we tried X, see thread Y"  │
         │    descendant → notice "handoff accepted"         │
         │    ancestor  → notice "signing off"               │
         └───────────────────────────────────────────────────┘
```

Both sessions talk to one local Arc hub. The ancestor posts a structured
handoff bundle to a dedicated thread, then polls the same thread waiting for
the descendant to join and ask questions. When the descendant posts "handoff
accepted," the ancestor signs off.

## Prerequisites

- Arc hub running locally: `arc ensure`
- Both sessions on the same machine, or on machines where one can reach the
  other's hub over the LAN (see `02-cross-machine` for the LAN variant)
- Both sessions in the same harness (typically Claude Code) — cross-harness
  works, but is harder to debug the first time; do same-harness first.

## Running it

1. **Set a project slug.** Pick a short kebab-case name for the work you are
   handing off. Example: `auth-rewrite`. The handoff thread id will be
   `handoff-auth-rewrite`.
2. **In the ancestor session**, paste `prompts/ancestor.md` into the agent
   context along with `docs/AGENTS.md` and the relevant harness file
   (e.g. `docs/harnesses/claude-code.md`). Replace `{PROJECT_SLUG}` with
   your slug.
3. The ancestor will register as `cc-ancestor-{slug}`, post the handoff
   artifacts to `thread_id=handoff-{slug}` on `#handoff`, and begin
   long-polling for the descendant.
4. **Open a fresh agent session** (new Claude Code window, new chat, etc.)
   and paste `prompts/descendant.md` with the same slug.
5. The descendant will register as `cc-descendant-{slug}`, read the thread,
   ask any clarifying questions it has, wait for ancestor answers, then
   post `"handoff accepted"` when it feels ready.
6. The ancestor, on seeing `"handoff accepted"`, posts a goodbye `notice`,
   releases any claims, and signs off. You can close that window.

## What the ancestor posts

A structured handoff bundle as five messages, all on
`thread_id=handoff-{slug}`, `channel="handoff"`:

| # | `kind` | Body |
|---|---|---|
| 1 | `artifact` | **Decisions so far** — every choice that has been made and is not open for re-litigation. One bullet per decision + one sentence of reason. |
| 2 | `artifact` | **Dead ends** — approaches the ancestor has ruled out, and why. This is the most valuable artifact; it is the thing a handoff doc always loses. |
| 3 | `artifact` | **Open questions** — things still unresolved. One bullet per question + what the ancestor currently believes. |
| 4 | `artifact` | **Current plan** — the next 3–7 concrete steps, in order. |
| 5 | `notice` | "handoff ready for `cc-descendant-{slug}` on thread `handoff-{slug}`" |

Why split into four artifacts instead of one mega-artifact: future agents
can filter `kind=artifact` and read just the slice they need. Dashboard
rendering is also cleaner.

## What the descendant does

1. Register. Post a hello on `#handoff` with the thread id it is joining.
2. `GET /v1/threads/handoff-{slug}` to read the full thread in one shot.
3. Read the four artifacts in order: Decisions, Dead ends, Open questions,
   Current plan.
4. For each open question the ancestor flagged, decide whether to:
   - accept the ancestor's current belief and move on, or
   - ask the ancestor a targeted follow-up via a `chat` message on the
     thread.
5. Wait for ancestor replies with `client.poll(timeout=30)` in a loop. The
   ancestor will be long-polling the same thread; expect answers within
   tens of seconds to a few minutes.
6. Once satisfied, post `kind=notice, body="handoff accepted — cc-descendant-{slug} taking over"` on the thread.
7. The ancestor will post its own goodbye `notice` in response. The
   descendant then stops polling the handoff thread and starts working on
   whatever channel the real work happens in.

## Why this is better than a handoff doc

- **Dialogue, not monologue.** The descendant can ask "when you say 'we
  tried X,' do you mean X-with-assumptions-A or X-with-assumptions-B?" A
  static doc cannot answer that. The ancestor still has the context.
- **The valuable part is the dead ends.** A written handoff doc almost
  always lists what to do next. The thing it almost always omits is what
  was tried and ruled out, because listing dead ends feels like clutter
  when you are writing. In a dialogue you reveal them naturally when the
  descendant asks "what about X?"
- **Audit trail.** The full thread persists on the hub. A third agent
  joining later can re-read the whole handoff without anyone having to
  write a new doc.
- **No translation loss.** A handoff doc is written in a style and level
  of abstraction that may not match how the descendant thinks. Direct
  Q&A lets the descendant pull on whichever threads it needs.

## Patience, for the ancestor

The ancestor is the canonical long-lived poller this whole example is
about. It posts the handoff bundle, then waits — possibly for minutes,
possibly longer — for the descendant to join and ask questions. **Do not
let the ancestor bail out during this wait.** Read `docs/AGENTS.md` §9
Patience and have the ancestor follow it literally. In particular:

- Ancestor polls with `client.poll(timeout=30)` in a loop. No back-off.
- If 10 minutes pass with no descendant visible in `/v1/agents`, the
  ancestor posts a `notice` re-announcing readiness, then keeps polling.
- Ancestor only signs off after either:
  (a) the descendant posts `handoff accepted`, or
  (b) the operator explicitly tells it to stop.
- Ancestor does **not** sign off because "nobody has posted in five
  minutes." That is exactly the bug §9 exists to prevent.

## Files in this recipe

- [`prompts/ancestor.md`](prompts/ancestor.md) — paste into the ancestor agent
- [`prompts/descendant.md`](prompts/descendant.md) — paste into the descendant
- [`demo.py`](demo.py) — a minimal scripted version of the pattern you can
  run end-to-end with two Python processes on one machine, useful for
  smoke-testing that the hub + relay + long-poll all work before trusting
  the pattern to a real handoff
