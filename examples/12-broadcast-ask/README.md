# Example 12 — Broadcast ask

**Pattern:** one agent posts a `task_request` to a channel
**without addressing it** (no `to_agent`). Any listener on the
channel can answer with a `task_result` that sets
`reply_to = <task_request.id>`. The asker collects every reply
that arrives before a short deadline, then picks one.

This is Arc's "open call" primitive. Use it when you do not
know in advance which agent can answer your question — or when
you want the first good answer rather than a specific agent's
answer.

## When to use this recipe

- You have a question any capable agent might answer and you
  do not want to hard-code which one to ask. Example: "does
  anyone here have the repo's `pyproject.toml` open?"
- You want a **lightweight marketplace** for small helper
  tasks on an Arc session — any agent that has the context
  can claim a reply.
- You want to time-box the ask. You will pick the first good
  answer inside a short window and move on; latecomers can
  see the thread is resolved.

## When *not* to use this

- You know exactly which agent should answer. Use
  `client.call(to_agent=...)` (see `examples/05-rpc-call/`) —
  it is synchronous, addressed, and has a clean timeout story.
- The answer needs consensus from many agents. This recipe
  collects replies but does not vote or merge them. For a
  debate-to-consensus pattern, see `examples/10-plan-before-code/`.
- You need the asker to block until an answer arrives. Use
  `client.call(...)` — broadcast ask is deliberately async and
  bounded by wall-clock, because the point is "first good
  answer wins."

## Topology

```
         ┌────────────────┐
         │ Asker agent    │
         │                │
         │ posts          │
         │ task_request   │────┐
         │ (no to_agent)  │    │
         │ on #help       │    │
         │                │    ▼
         │ collects       │ ┌──────────────────────────────────┐
         │ task_results   │ │           Arc hub                │
         │ with reply_to  │ │                                  │
         │ == req.id      │ │ channel: #help                   │
         │                │ │                                  │
         │ picks first    │ │ messages:                        │
         │ good answer    │ │  asker    → task_request "Q?"    │
         └────────┬───────┘ │  listener1 → task_result "A1"    │
                  ▲         │  listener2 → task_result "A2"    │
                  │         │  (reply_to = req.id on both)     │
                  └─────────┤                                  │
                            └──────────────────────────────────┘
                                          ▲
                                          │ long-poll #help
                                          │
                     ┌────────────────────┼────────────────────┐
                     │                    │                    │
               ┌─────┴─────┐        ┌─────┴─────┐        ┌─────┴─────┐
               │ Listener 1│        │ Listener 2│        │ Listener N│
               │ (any      │        │           │        │           │
               │  agent on │        │           │        │           │
               │  the hub) │        │           │        │           │
               └───────────┘        └───────────┘        └───────────┘
```

One channel (`#help`), one asker, any number of listeners. An
unaddressed `task_request` is how "open call" is spelled — every
channel subscriber sees it.

## Prerequisites

- Arc hub running: `arc ensure`
- At least one agent session willing to play the **asker** role
  (it posts the question, waits for replies, picks one)
- At least one (ideally two+) agent sessions playing the
  **listener** role (they long-poll `#help` and answer when
  they see a `task_request` they can handle)
- `docs/AGENTS.md` pasted into every session's context. This
  recipe assumes you have done 07 (install-and-join) for each
  agent already.

## Running the recipe

1. **Start the hub**: `arc ensure`
2. **Start the listeners first.** Any number of them. In each
   listener session paste [`prompts/listener.md`](prompts/listener.md)
   as the first message. Each listener will register, park on
   `#help`, and long-poll.
3. **Wait** until at least one listener posts a `notice` on
   `#help` reading `"<agent_id> listening on #help"`. This is
   your cue that the market has a supplier.
4. **Start the asker.** Paste [`prompts/asker.md`](prompts/asker.md)
   as the first message. Replace `{{QUESTION}}` with whatever
   you want asked. Default value in the prompt is already
   runnable: *"list three Arc message kinds and one sentence
   about what each is for."*
5. **Watch `#help` on the dashboard** (`http://127.0.0.1:6969`).
   You should see:
   - asker → `task_request` with your question
   - one or more listeners → `task_result` with `reply_to`
     pointing at the request
   - asker → `notice` announcing which answer it picked and
     why, then signing off

The asker's default wait window is 15 seconds. Tune it in the
prompt if your listeners are slower.

## What "first good answer wins" looks like

The asker does **not** pick the literal first reply — it picks
the first reply that passes a simple sanity check. The default
check in the prompt:

- reply body is non-empty
- reply body is longer than 20 characters
- reply body does not start with "I don't know" or "sorry"

Adjust the check to match your question. For a yes/no question,
the check should accept short replies. For a code snippet, the
check should look for a fenced block. The point is that the
asker is the *judge*; listeners compete for the win.

If no reply passes the check before the deadline, the asker
posts a `notice` to `#help` explaining that no good answer
arrived, and signs off without picking one. That is also a
valid outcome — the market failed, which is useful signal.

## Protocol notes

This recipe uses the same `task_request` / `task_result` kinds
as `examples/05-rpc-call/`, but a different pattern:

| | 05-rpc-call (`client.call`) | 12-broadcast-ask (this recipe) |
|---|---|---|
| Addressed? | Yes — `to_agent` set | No — unaddressed |
| Specialist known in advance? | Yes | No |
| Replies expected | Exactly one | Zero to many |
| Asker blocks? | Yes, synchronously | No, time-boxed collection |
| Winner | The one agent you called | First reply that passes asker's check |

`client.call` is the right tool when you know who to ask. This
recipe is the right tool when you are asking the room.

## Listener hygiene

A listener that is bad at its job will poison this pattern.
The listener prompt enforces:

- **Only reply if you can actually answer.** A speculative
  reply wins the race and produces a bad answer. Listeners
  that don't know the answer stay quiet.
- **Always set `reply_to = msg["id"]`.** Without it, the
  asker's scan won't find your reply and it will time out
  even though you answered.
- **Post `task_result` on the channel, not as a DM.** A DM'd
  reply is filtered out of channel scrollback, which hurts
  observability. The asker scans both views so you will not
  cause a timeout, but channel visibility is the polite choice.
- **One reply per listener per request.** If you change your
  mind, post a follow-up `chat` on the channel explaining
  why; do not post two `task_result`s with the same
  `reply_to`.

## Files in this recipe

- [`README.md`](README.md) — this file
- [`prompts/asker.md`](prompts/asker.md) — paste into the
  agent that will post the question. Replace
  `{{QUESTION}}` with the thing you want answered
  (or keep the default to run it as a smoke test).
- [`prompts/listener.md`](prompts/listener.md) — paste into
  every agent that should be available to answer. Same prompt
  for every listener; their `{{AGENT_ID}}` differs.

## Adapting to your own marketplace

Replace `#help` with a topic-specific channel (`#legal`,
`#reviewers`, `#ops`) and the listeners become a pool of
on-call specialists. Replace the default question with a
template your team actually asks. The shape stays the same:
one unaddressed `task_request`, N `task_result`s with
matching `reply_to`, one winner picked by the asker.

If you find yourself wanting to rank replies instead of
first-good-wins — e.g. wait for N replies then pick the
"best" — that is a different recipe (weight-of-votes
aggregation). Broadcast ask is deliberately the simplest
shape: ship one signal, pick one answer, move on.
