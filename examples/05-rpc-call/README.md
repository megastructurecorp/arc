# Example 05 — RPC call

**Pattern:** one agent sits idle on a channel waiting for
`task_request` messages. Another agent calls `client.call(...)`,
which posts the request and blocks until the matching
`task_result` comes back. The caller gets a synchronous sub-task
delegation; the specialist gets a clean request/response
protocol with no hand-rolled polling logic.

This is Arc's sub-task primitive. Think of it as "call a
specialist function that happens to live in another agent's
head."

## When to use this recipe

- You want **sync** sub-task delegation — i.e. "do this and
  give me the answer, I'll wait." Async fire-and-forget is
  what plain `post` + `poll` already gives you; `client.call`
  is for when the caller has nothing useful to do until the
  answer arrives.
- You have a **specialist skill** that is costly to put into
  every agent's context — a linter, a type-checker, a
  graph-querier, a shell runner, a vector-search — and you
  want one agent parked on that skill to serve the rest.
- You want a clean RPC boundary so a future change to the
  specialist (model, prompt, tools) does not ripple through
  every caller.

## When *not* to use this

- The work is fan-out-then-join. Use plain `post` +
  `task_request` and gather the replies yourself, because
  `client.call` is sequential by design — it blocks on one
  request at a time.
- The specialist work takes longer than a few minutes. The
  default RPC timeout is 30s; you can raise it, but a genuinely
  long job wants a `task_request` + explicit `task_result`
  protocol so the caller can do other work in the meantime.
- You want the caller and specialist to feel like peers
  collaborating. RPC has a clear caller/callee asymmetry; if
  the work is symmetrical, use a shared channel instead.

## Topology

```
     ┌─────────────────┐                  ┌─────────────────┐
     │ Caller agent    │                  │ Specialist      │
     │ (any role)      │                  │ agent           │
     │                 │                  │ lint-spec       │
     │  result =       │  task_request →  │                 │
     │  client.call(   │  ───────────────▶│  (long-polling  │
     │    "lint-spec", │                  │   #rpc)         │
     │    "check this" │                  │                 │
     │  )              │  ← task_result   │  lint(body)     │
     │                 │  ───────────────▶│  return         │
     │  # blocks until │                  │                 │
     │  # result lands │                  │                 │
     └─────────────────┘                  └─────────────────┘
                 ▲                                ▲
                 │                                │
                 └──────── Arc hub ───────────────┘
                      channel: #rpc
                      task_request kind,
                      task_result kind keyed to
                      reply_to = request.id
```

One hub, one channel (`#rpc` by default), one specialist
parked on it, one or more callers. The wire protocol is:

1. Caller posts `task_request` with `to_agent=<spec>,
   channel="rpc", body=<payload>`.
2. Specialist sees the `task_request` in its poll loop,
   computes the answer, posts a `task_result` with
   `reply_to=<task_request.id>` and `body=<answer>`.
3. `client.call` on the caller side sees the matching
   `task_result` and returns the full message object.

You do not construct these kinds by hand. `client.call` and the
specialist's poll loop handle the framing.

## Prerequisites

- Arc hub running: `arc ensure`
- Two agent sessions ready (caller + specialist)
- The specialist's "skill" is something the agent can actually
  do — for the worked example, that is "parse a chunk of Python
  source with `ast.parse` and return a summary." If you swap in
  your own skill, make sure the specialist's harness has the
  tools to do it.

## Worked example: `py-lint-spec`

The specialist in this example is **`py-lint-spec`**, an agent
whose single job is:

> Accept a chunk of Python source as the `task_request` body.
> Run `ast.parse` and `compile(..., "<rpc>", "exec")` on it.
> Return a short structured report:
>
> - whether it parses (`ok: bool`)
> - syntax error message if any
> - list of top-level function names defined
> - list of top-level class names defined
> - longest line length
>
> Stdlib only. No subprocesses. No tools beyond Python's own
> `ast` and `compile`. Deterministic.

This is small enough to read in one glance and realistic
enough that "caller agent asks specialist to lint its new
function before committing" is plausible usage.

The caller in this example is deliberately generic — it just
calls `client.call(...)` with a snippet, gets the report,
prints it, and decides what to do next.

## Running the recipe

1. **Start the hub**: `arc ensure`
2. **Open the specialist session first.** It needs to be
   parked on `#rpc` *before* the caller starts making
   requests. Paste
   [`prompts/specialist.md`](prompts/specialist.md) as the
   first message.
3. **Wait for the specialist to post a "ready" notice** on
   `#rpc`. This confirms it registered, created the channel
   (if needed), and entered its poll loop. You can also check
   `GET /v1/agents` to see it live.
4. **Open the caller session.** Paste
   [`prompts/caller.md`](prompts/caller.md) as the first
   message. It will make three example calls — a clean
   snippet, a syntactically broken snippet, and a snippet with
   multiple functions — and print each returned report.
5. **Watch the dashboard** (`http://127.0.0.1:6969`). On
   `#rpc` you will see alternating `task_request` and
   `task_result` messages pair up neatly via `reply_to`.

The whole flow usually finishes in under a minute once both
agents are up. The specialist keeps running until you stop it
(or until the operator posts a `"shutdown"` notice — see
`prompts/specialist.md` for the exact keyword).

## What `client.call` actually does

```python
result = client.call(to_agent, body, *,
                     channel="direct",
                     timeout=30.0,
                     poll_interval=1.0,
                     metadata=None)
```

Under the hood:

1. Posts a `task_request` message to `channel` with
   `to_agent=<to>` and the body.
2. Records the returned message id as `req_id`.
3. Polls both `/v1/messages?channel=<channel>&since_id=<req_id-1>`
   and `/v1/inbox/<agent_id>&since_id=<req_id-1>` on a short
   interval, scanning for any message with `kind="task_result"`
   and `reply_to == req_id`.
4. Returns the first match as a full message dict.
5. Raises `arc.ArcError` with HTTP 408 if the timeout passes
   without a match.

The polling here is **short-poll** on purpose — RPC is
interactive, and you do not want to sit on a 30-second long
poll when the specialist might answer in 100ms. Adjust
`poll_interval` if you care about the trade-off.

The default channel is `"direct"`. Override it to `"rpc"` if
you want RPC traffic visible on a shared dashboard tab (the
worked example does this for clarity).

**Important:** `client.call` does **not** require the
specialist to exist at call time. If the specialist is not
parked on the channel, the request will sit there and the
call will time out. You see the `task_request` on the
dashboard but no `task_result`. This is a useful failure mode
— "the specialist is down" is visible, not silent. For a
production setup you probably want the caller to set a short
timeout and fall back gracefully.

## Specialist patterns

The worked specialist is deliberately tiny. Real specialists
follow the same shape:

```python
def main():
    import arc
    client = arc.ArcClient.quickstart(
        "lint-spec",
        display_name="Python lint specialist",
        capabilities=["rpc", "specialist", "python", "lint"],
    )
    # quickstart() calls bootstrap() automatically, advancing the poll
    # cursor past any pre-existing history. See "Specialist gotchas"
    # below for why this matters.
    client.create_channel("rpc")
    client.post("rpc", "lint-spec ready", kind="notice")
    try:
        while True:
            for msg in client.poll(timeout=30, channel="rpc"):
                if msg.get("kind") != "task_request":
                    continue
                if msg.get("to_agent") != client.agent_id:
                    continue
                try:
                    result = handle(msg["body"])
                    client.post(
                        "rpc", result,
                        kind="task_result",
                        reply_to=msg["id"],
                    )
                except Exception as exc:
                    client.post(
                        "rpc", f"error: {exc}",
                        kind="task_result",
                        reply_to=msg["id"],
                        metadata={"error": True},
                    )
    finally:
        client.close()
```

Key rules:

- **Always answer every `task_request` you see directed at
  you**, even with an error. A caller blocked on
  `client.call` has no other way to know its request is
  actually being handled; silence is indistinguishable from
  "specialist crashed" until the timeout hits.
- **Set `reply_to=msg["id"]` exactly.** This is how
  `client.call` finds your response. Getting the id wrong
  means the caller times out even though you answered.
- **Prefer not setting `to_agent` on the response.** It is
  tempting to "address" the result back to the caller so the
  dashboard shows a clean DM, but DMs are filtered out of
  channel views — other observers on `#rpc` (including the
  dashboard operator) will not see the reply. `client.call`
  now scans both the channel and the caller's inbox, so a
  DM'd reply will not cause a timeout, but omitting `to_agent`
  keeps RPC traffic visible to everyone. Reply on the channel;
  `reply_to` is sufficient to thread the response to the
  request.
- **Don't hold claims between requests.** A specialist is
  pure: each call is independent of the last. If you find
  yourself wanting state between calls, that is a signal to
  use a plain long-lived agent with a regular chat protocol
  instead of RPC.

## Specialist gotchas

Two real bugs we have hit while writing this recipe; they are
worth internalising up front because neither is obvious from
the naive reading of the protocol.

### 1. The poll cursor must start at "now", not at zero

Before the current version, `ArcClient.quickstart(...)` left
the client's internal `_since_id` at 0. The first call to
`client.poll(...)` then returned **every** matching message in
the hub's history, including stale `task_request`s from a
previous demo run and any stale `shutdown` notices you use as
a kill-switch. A fresh specialist would eagerly "answer"
yesterday's requests (whose callers are long gone), then see
yesterday's shutdown keyword and exit before today's caller
even posted.

**This is now fixed:** `quickstart()` calls `bootstrap()`
automatically, which advances `_since_id` to the
highest-visible message id at the time of registration. The
first `poll()` only returns messages that arrived **after**
the specialist came online.

If you construct a client with `ArcClient(...)` and
`register()` manually (instead of using `quickstart`), you
must call `client.bootstrap()` yourself to get the same
behaviour — or accept that your first poll returns the full
backlog. The worked specialist prompt and `demo.py` both use
`quickstart`, which handles this for you.

### 2. `to_agent` on the response (now tolerated, but not recommended)

A `task_result` posted as a DM (with `to_agent` set) is
filtered out of `GET /v1/messages?channel=` results, which
is how `client.call` originally scanned for replies. In
earlier versions this was a silent timeout — the specialist
thinks it answered, the caller times out.

**This is now tolerated:** `client.call` scans both the
public channel view **and** the caller's inbox, so a DM'd
`task_result` will be found either way.

That said, posting `task_result` without `to_agent` is still
the recommended pattern: it keeps the RPC traffic visible
on the channel to everyone watching (including the dashboard
operator), and it avoids the asymmetry of a response that is
visible to the recipient but invisible to other observers on
the same channel. The specialist prompts in this example omit
`to_agent` on all responses for this reason.

## Files in this recipe

- [`README.md`](README.md) — this file
- [`prompts/specialist.md`](prompts/specialist.md) — paste
  into the specialist agent's session first
- [`prompts/caller.md`](prompts/caller.md) — paste into the
  caller agent's session second
- [`demo.py`](demo.py) — a scripted two-thread version of the
  pattern. One thread runs the `py-lint-spec` specialist; the
  other runs three calls against it and prints the results.
  Useful for smoke-testing that your hub, poll, and the
  `task_request`/`task_result` round-trip all behave before
  trusting the pattern to two real LLM sessions.

## Adapting to your own specialist

Replace `py-lint-spec`'s body handler with whatever skill you
want to make RPC-accessible. Good candidates:

- **`format-spec`** — run `black` (or `ruff format`) on a
  snippet and return the reformatted source. 10 lines of
  handler.
- **`grep-spec`** — scan a project directory for a pattern
  and return matching file:line:body triples. Specialist owns
  the project path; callers just send queries.
- **`embed-spec`** — compute an embedding for a chunk of
  text, return the vector. Nice if one agent in the fleet has
  the model loaded and others don't.
- **`shell-spec`** — run a whitelisted command in a specific
  cwd and return stdout/stderr. Dangerous if not locked down,
  but the pattern fits.

Keep the body format simple — a plain string or a small JSON
blob. If the payload is big enough to need a schema, you
probably want a real internal API, not an Arc RPC.
