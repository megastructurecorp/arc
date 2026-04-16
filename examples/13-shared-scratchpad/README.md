# Example 13 — Shared scratchpad

**Pattern:** two agents co-edit a single plain-text file on disk
by passing the file lock back and forth through Arc. One drafts
a first version; the other reads the file, edits, passes the
lock back; they converge in three to five short turns. The
deliverable is a real file in your working tree, not a message
in a channel.

This is the simplest useful demo of `client.lock()` — the one
primitive that makes "two agents, one file" safe — in a shape
you can drop into any real project by changing one placeholder.

## When to use this recipe

- Two agents need to produce a **single piece of text** together
  where neither alone would land it. One paragraph of README,
  one design memo, one commit message, one slide's worth of
  copy.
- You want the history of edits to be a visible channel trail,
  not a silent diff that only `git log` remembers.
- You want both agents to feel the lock discipline before you
  graduate to `examples/03-parallel-coding/` (different files)
  or the four-agent `examples/01-game-jam/` template.

## When *not* to use this

- The deliverable is structured code, not prose-or-light-markup.
  Two agents holding the same Python file under a lock for five
  turns is a recipe for merge regret — use `03-parallel-coding`
  instead, where each agent owns a disjoint file.
- Pure opinion exchange with no file. Use
  `examples/09-draft-and-critique/` (writer posts an artifact
  message, critic replies, writer revises) — no file lock, no
  disk write, lighter ceremony.
- More than two agents. The lock passes cleanly between two;
  three or more agents on one file is a queue and needs either
  a coordinator or a switch to `01-game-jam`'s scheme.

## Topology

The recipe ships with a runnable default topic —
*"draft one sentence describing Arc for an engineer who has
never heard of it, ≤30 words"* — so you can paste the two
prompts unchanged for a smoke run. Swap `{{TOPIC}}` when you
use this for real work.

```
        ┌──────────────┐                   ┌──────────────┐
        │ Agent A      │                   │ Agent B      │
        │ (drafter)    │                   │ (editor)     │
        │              │                   │              │
        │ lock → edit  │   passes lock on  │ lock → edit  │
        │ scratchpad.md│ ◀─────#scratch────▶│ scratchpad.md│
        │ unlock       │                   │ unlock       │
        └──────┬───────┘                   └──────┬───────┘
               │                                  │
               │            posts & polls          │
               ▼                                  ▼
        ┌───────────────────────────────────────────────┐
        │                Arc hub                        │
        │                                               │
        │  channel: #scratch                            │
        │  lock:    {{SCRATCHPAD_PATH}}                 │
        │                                               │
        │  messages on #scratch:                        │
        │    A → notice "v0 drafted, lock released"     │
        │    B → notice "v1 edited, lock released"      │
        │    A → notice "v2 edited, lock released"      │
        │    B → notice "v3 accepted, done"             │
        └───────────────────────────────────────────────┘
```

One channel. One file. One lock that the two agents pass back
and forth like a token. Every round ends with the file on disk
having one unambiguous current version.

## Prerequisites

- Arc hub running: `arc ensure`
- Two agent sessions, both able to read and write files in the
  same working directory
- Both agents have already done `examples/07-install-and-join/`
  once on this machine (or know the equivalent)
- `docs/AGENTS.md` in both agents' context — §7 (claims and
  locks) and §10 (clean shutdown) matter most
- Paste `examples/08-hello-two-agents/` context if your agents
  have never handshaked before; 13 assumes the handshake is
  already muscle memory

## Running the recipe

1. **Start the hub**: `arc ensure`
2. **Pick a scratchpad path.** Anywhere inside your working
   tree. Default: `scratchpad.md`. The prompts both take a
   `{{SCRATCHPAD_PATH}}` placeholder — set it to the same
   value for both agents.
3. **Pick a topic.** What are the two agents writing? Default
   baked into the prompt is:
   *"draft one sentence describing Arc for an engineer who has
   never heard of it, ≤30 words."* Swap `{{TOPIC}}` for your
   own when you use this for real work.
4. **Label the agents.** One is the **drafter** (role=`A`), one
   is the **editor** (role=`B`). The drafter owns the first
   lock; the editor owns the second. The two roles alternate
   thereafter.
5. **Paste the prompts:**
   - Drafter: [`prompts/agent-a.md`](prompts/agent-a.md)
   - Editor: [`prompts/agent-b.md`](prompts/agent-b.md)
6. **Watch `#scratch`** on the dashboard
   (`http://127.0.0.1:6969`). You will see alternating
   `notice` messages announcing each version. The file on
   disk at `{{SCRATCHPAD_PATH}}` advances in lock-step.
7. **You're done when** agent B posts a `notice` containing
   the word `accepted` and both agents have released their
   locks and signed off.

First full pass usually takes 2–4 minutes end to end. If one
agent holds a lock for more than 10 minutes without posting,
something has gone wrong — check the dashboard's locks panel.

## The lock handoff protocol, in three rules

The whole recipe is this protocol. Internalize it.

1. **Before you touch the file, you hold the lock.** No
   exceptions. `client.lock("{{SCRATCHPAD_PATH}}", ttl_sec=600)`
   must return `{"acquired": true, ...}`. If it returns
   `{"acquired": false}`, wait — your counterpart is mid-edit.
2. **After you edit, release.** `client.unlock(...)` and post
   a one-line `notice` on `#scratch` announcing your version
   and that the lock is free. "v2 edited: sharpened the verb,
   lock released."
3. **The other agent then `lock`s, edits, `unlock`s.** The
   exchange continues until one side posts a `notice`
   containing `"accepted"`, at which point the current file on
   disk is the final version. That agent also releases the
   lock (or never acquired it this round — the handoff just
   resolves).

The TTL on the lock is a lease. If you hold the lock and fall
silent for longer than `ttl_sec`, the hub GCs your lock and
the other agent can take it. We use `ttl_sec=600` (10 minutes)
to give agents room to think but not so much that a crashed
agent deadlocks the file.

## Convergence discipline

Two agents can loop on "one more revision" forever if the
prompts don't tell them when to stop. The prompts enforce a
hard ceiling:

- **Cap at 5 rounds.** If the file hasn't been accepted by
  round 5, agent B posts `"no convergence, freezing at v5"`
  and both sign off. This is not failure — it is a signal
  that the topic was underspecified and the operator should
  look at it.
- **Accept early.** If agent B reads the drafter's version
  and agrees with no substantive change, it posts
  `"v<N> accepted, done"` without further edits. Shipping a
  v1 is a better outcome than shipping a v4.
- **No style churn.** Neither agent should rewrite without a
  concrete reason they can state in one sentence. If agent B
  cannot name what it changed and why, it shouldn't have
  changed it.

## What happens if the other agent stalls

The lock's TTL handles a crashed agent automatically — after
10 minutes the lock is released and you can take it. But a
*slow* agent (thinking, running tests) is different from a
*dead* one. The prompts tell both agents to:

- **Refresh the lock** if you are still working past 5
  minutes. `client.lock(path, ttl_sec=600)` called by the
  current holder refreshes the lease (the Arc lock primitive
  treats a re-lock by the current owner as a refresh).
- **Post a heartbeat notice** if you hold the lock for more
  than 3 minutes of wall-clock. "still editing v2, brb."
- **Yield gracefully** if you realize you have nothing
  useful to add. Release the lock and post
  `"v<N> unchanged, passing to <other agent>"`.

## Files in this recipe

- [`README.md`](README.md) — this file
- [`prompts/agent-a.md`](prompts/agent-a.md) — the drafter.
  Owns the first lock, writes v0 into the file.
- [`prompts/agent-b.md`](prompts/agent-b.md) — the editor.
  Takes the second lock, reads the file, edits or accepts.

Both prompts take the same two placeholders:
- `{{SCRATCHPAD_PATH}}` — the path to the shared file.
  Default: `scratchpad.md`.
- `{{TOPIC}}` — what the two agents are writing. Default
  runs without modification as a smoke test.

## Adapting to your own work

Swap the topic and the file path. The shape is stable across
use cases:

- Two writers co-drafting one paragraph of marketing copy.
  `{{SCRATCHPAD_PATH}}=marketing.md`, `{{TOPIC}}="one-line
  hero tagline for product X"`.
- A senior and junior engineer co-drafting a design memo.
  `{{SCRATCHPAD_PATH}}=docs/design/auth.md`, `{{TOPIC}}=
  "outline the three decision points in the auth rewrite"`.
- Two reviewers jointly writing a code-review summary.
  `{{SCRATCHPAD_PATH}}=.github/pull_request_template.md`,
  `{{TOPIC}}="PR #NNN summary — ship/hold/followup bullets"`.

If you need more than two writers, or the file has meaningful
structured sections each owner should hold independently,
move up to `examples/01-game-jam/` — it has a four-role
template with explicit section ownership. This recipe
deliberately stops at two.
