# Operator cheatsheet — for the human

This file is not a prompt. There is nothing to paste into any
LLM session. It is the CLI-and-dashboard reference card you
keep open in a second terminal window while the worker agent
does its thing.

## Your identity

```bash
# Pick once, use consistently. Short, lowercase, no hyphens.
export ARC_AGENT=rod
```

Every command below assumes `$ARC_AGENT` is set. If you do
not want to set an env var, inline `--agent rod` on every
call — the CLI never remembers your id between invocations.

## Posting

```bash
# Public post to #general
arc post --agent $ARC_AGENT "back in 10 minutes"

# Public post to a specific channel
arc post --agent $ARC_AGENT --channel work "approve the migration"
arc post --agent $ARC_AGENT --channel review "use option B"

# DM to a specific agent
arc post --agent $ARC_AGENT --to cc-worker-rod-mac "please pause"

# DM in a specific thread
arc post --agent $ARC_AGENT --to cc-worker-rod-mac \
  --thread-id review-migration-042 "use option B"

# Post as a different `kind`
arc post --agent $ARC_AGENT --to cc-worker-rod-mac --kind task_request \
  "run the full test suite, not just the fast subset"
arc post --agent $ARC_AGENT --channel work --kind notice \
  "operator monitoring — you're doing great, keep going"
```

The first `arc post` with a new agent_id implicitly registers
you with `replace=true`. Subsequent posts reuse the same
session. Running `arc post` from two terminals simultaneously
as the same `$ARC_AGENT` is fine.

## Polling

```bash
# Long-poll everything new addressed to you or on channels you
# listen to, for up to 30 seconds.
arc poll --agent $ARC_AGENT --timeout 30

# Watch a specific channel
arc poll --agent $ARC_AGENT --channel review --timeout 30

# Watch a specific thread
arc poll --agent $ARC_AGENT --thread-id work-2026-04-15 --timeout 30

# Include your own messages in the stream (useful for debugging
# that a post actually landed)
arc poll --agent $ARC_AGENT --include-self --timeout 5
```

For a tail-like watcher, loop it:

```bash
while true; do arc poll --agent $ARC_AGENT --timeout 30; done
```

Each invocation prints new messages as JSON. The CLI is not
stateful between invocations, so a tight loop can re-show the
same message once across the boundary; filter client-side by
`id` if that bothers you, or switch to the Python API:

```python
import arc, json
c = arc.ArcClient.quickstart("rod", display_name="Rod (operator)")
while True:
    for msg in c.poll(timeout=30):
        print(json.dumps(msg, indent=2))
```

## Dashboard checks without the browser

Sometimes you just want a quick `jq`-able snapshot from the
terminal. Three useful calls:

```bash
# Who is currently registered on the hub?
curl -s http://127.0.0.1:6969/v1/agents | python -m json.tool

# Last 20 messages on a channel
curl -s "http://127.0.0.1:6969/v1/messages?channel=work&limit=20" \
  | python -m json.tool

# Full thread view in one shot
curl -s http://127.0.0.1:6969/v1/threads/work-2026-04-15 \
  | python -m json.tool

# Your inbox (messages addressed to you)
curl -s http://127.0.0.1:6969/v1/inbox/$ARC_AGENT \
  | python -m json.tool
```

## What to do when the worker asks a question on `#review`

The standard worker prompt (`worker.md`) has the agent post
questions on `#review` with a `thread_id` and
`metadata={"awaiting": "rod"}`. Your answer is one command:

```bash
# Reply in the same thread the worker opened
arc post --agent $ARC_AGENT --channel review \
  --thread-id review-migration-042 "use option B"
```

The worker sees it within its poll interval (typically 30s)
and acts on it.

## When you want to stop the worker cleanly

DM the agent the standard wrap-up keyword:

```bash
arc post --agent $ARC_AGENT --to cc-worker-rod-mac \
  "please wrap up: finish the current step, post a final notice, close your client"
```

The worker's main loop watches for the phrase `please wrap
up` (or `wrap up` / `shutdown`) in operator DMs and does a
clean shutdown when it sees one. It will:

1. Finish the in-flight step.
2. Release any claims/locks.
3. Post a final `notice` on `#work`.
4. Call `client.close()` to deregister.

You will see the worker disappear from `/v1/agents` within a
second of its `close()`.

## When you want to stop the whole hub

```bash
arc stop                                  # graceful
arc reset                                 # graceful + delete database
```

**`reset` wipes all sessions, messages, claims, locks, and
tasks.** Only do this at the start of a new session or when
you are genuinely done with the work. You cannot undo it.

## Etiquette tips

- **Be sparing with DMs.** A DM interrupts the worker's
  poll loop immediately; chat on a channel is low-urgency.
  Save DMs for actual interventions.
- **Prefer `notice` over `chat`** for one-way
  announcements. `chat` implies an expected response;
  `notice` is "FYI."
- **Use threads.** A long `#review` conversation without
  `thread_id` is a mess on the dashboard. Every sub-topic
  gets its own thread id.
- **Do not over-post progress updates yourself.** The worker
  is the primary source of truth; you posting "looks good!"
  every 5 minutes adds noise without adding signal. Post
  when you actually have something to say.
- **Goodbye notices are useful.** When you are stepping
  away, a one-line `"calling it for the night"` on
  `#general` lets the worker know not to expect fast
  replies to `#review` questions. (The worker should not
  *wait* for you — the patience rules still apply — but it
  can choose to defer risky decisions to tomorrow instead.)
