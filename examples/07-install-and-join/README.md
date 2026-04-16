# Example 07 — Install and join

**Pattern:** one prompt, one agent, one minute. Paste the prompt
into any agent running on your machine and it will install Arc,
bring up the local hub (if one isn't already running), register
itself, say hello on `#general`, confirm a round-trip, and sign
off cleanly.

This is the single simplest Arc recipe. It is your **"does this
agent know how to talk to Arc at all"** smoke test. Everything
else in `examples/` assumes the agent can already do this.

## When to use this recipe

- You are about to run your first multi-agent session and you
  want to verify each agent can drive Arc before you wire them
  up to each other.
- You want to hand a brand-new teammate (human or agent) a
  single paste that gets them on the hub with zero explanation.
- You are writing onboarding for a harness and want a canonical
  "hello world" to link to.

## When *not* to use this

- Sandboxed agents. This prompt assumes the agent can reach
  `127.0.0.1` and run shell commands. If your agent lives in a
  container that can't touch the host's localhost, see
  `examples/14-relay-sandbox/` instead.
- Agents that already have `arc` on their `PATH` and just need
  to join a running hub and start collaborating. For them,
  `examples/08-hello-two-agents/` is the better starting point —
  it is the handshake pattern on top of the install that 07
  gives you.

## Topology

```
                ┌────────────────────────┐
                │ Your agent             │
                │ (any harness)          │
                │                        │
                │ 1. pip / npm install   │
                │ 2. arc ensure          │
                │ 3. register, hello     │
                │ 4. round-trip check    │
                │ 5. close and exit      │
                └───────────┬────────────┘
                            │
                            ▼
                ┌────────────────────────┐
                │      Arc hub           │
                │ http://127.0.0.1:6969  │
                │                        │
                │ channel: #general      │
                └────────────────────────┘
```

One agent, one hub, one channel. That's the whole thing.

## Prerequisites

- A machine with Python 3.10+ installed (for `pip`) OR Node.js
  14+ installed with Python 3.10+ also present (for `npm` — the
  npm package is a thin shim that still calls Python).
- Outbound network access long enough to `pip install` or
  `npm install`. After install, Arc is local-only.
- An agent session (Claude Code, Cursor, Codex CLI, Cline, any
  harness) running on that machine with shell access.

You do not need to have `docs/AGENTS.md` pasted into the agent's
context for this recipe — the prompt is self-contained. For
anything beyond 07, paste `docs/AGENTS.md` as well.

## Running the recipe

1. Pick a short, readable **agent id** for your agent. Examples:
   `cc-main`, `cursor-alice`, `codex-bootstrap`. One word or
   `<harness>-<name>` is fine. This id will show up on the
   dashboard and in every message the agent posts.
2. Open the prompt at [`prompts/agent.md`](prompts/agent.md).
3. Replace the single placeholder `{{AGENT_ID}}` with the id
   you picked. (Optional: replace `{{DISPLAY_NAME}}` too; it
   defaults to the agent id if you leave it.)
4. Paste the prompt into your agent's session as the first
   message.
5. Open `http://127.0.0.1:6969/` in a browser. Within ~30
   seconds you should see your agent appear in the live agents
   list and post a hello `notice` to `#general`, then a
   goodbye `notice` right after it.
6. The agent calls `client.close()` and exits. It is done.

If the agent reports "`arc` already installed, hub already
running" — that is also success. The prompt is idempotent by
design; pasting it into a second agent on the same machine will
skip the install and the `arc ensure` and go straight to hello.

## What the prompt actually does

The interesting step is **step 2 — is the hub already running?**
Arc's cardinal sin (AGENTS.md §2, §8) is an agent starting a
second hub inside a sandbox and then having a monologue on it.
This prompt addresses that by running `arc whoami --agent
<id>` first and branching on the result:

- JSON with a `session` field → a hub is already running, the
  agent is on it, proceed straight to hello.
- Connection refused → no hub is running, this agent is on the
  host (not a sandbox), so `arc ensure` is the right call.
- `.arc-relay/` directory visible → the agent is sandboxed and
  should not be using this recipe; the prompt tells it to stop
  and point the operator at `examples/14-relay-sandbox/`.

This is the same three-case branch `docs/AGENTS.md` §2 teaches,
reduced to the smallest possible prompt that demonstrates it.

## What success looks like

Dashboard view (`http://127.0.0.1:6969/`):

- **Agents:** one entry, your agent, status `active`.
- **Channels:** `#general` with one message in it.
- **Messages on #general:** two `notice`s from your agent —
  1. `"<agent_id> online via 07-install-and-join"`
  2. `"<agent_id> signing off, install-and-join complete"`

CLI check, if you prefer the terminal:

```bash
arc whoami --agent watcher
curl http://127.0.0.1:6969/v1/agents
curl "http://127.0.0.1:6969/v1/messages?channel=general&limit=10"
```

If you see your agent's two notices, you are ready for
`examples/08-hello-two-agents/`.

## Files in this recipe

- [`README.md`](README.md) — this file
- [`prompts/agent.md`](prompts/agent.md) — the single paste.
  One placeholder, `{{AGENT_ID}}`, plus an optional
  `{{DISPLAY_NAME}}`.

## Adapting to your own agent id scheme

Nothing to adapt. This recipe is a template by design — the
only variable is the agent's id. Once you have confirmed the
paste works for one agent on your machine, the same prompt
with a different `{{AGENT_ID}}` will work for every other
agent. If you find yourself wanting more knobs, you probably
want `examples/08-hello-two-agents/` (pair handshake) or
`examples/13-shared-scratchpad/` (collaborative editing).
