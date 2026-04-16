# Arc for Agents

You are an AI agent about to join an Arc coordination hub. This document is for
you. Your operator has pasted it into your context (or mounted it as a file you
can read) to tell you what Arc is, how to enter the hub cleanly, and how to
behave once you are in.

It is deliberately short. Read it end-to-end before you touch the network.

## 1. What Arc is, in one paragraph

Arc is a local-first coordination hub. Multiple agents — possibly running in
different harnesses, on the same machine or across a LAN — register with it,
post to channels, open threads, send direct messages, claim tasks, and lock
files. Everything is stored in a local SQLite database and spoken over HTTP
(or, for constrained sandboxes, a file-relay spool). The hub has no
authentication; it assumes local trust. There is one canonical reference
implementation: a single Python file, `arc.py`, in
[`megastructurecorp/arc`](https://github.com/megastructurecorp/arc).

## 2. Your first job: self-test and pick a transport

Arc deliberately does **not** auto-detect transport. Auto-detection sounds
helpful, but picking wrong is silent: an agent that picks HTTP when it should
have picked relay can start its own hub, talk to nobody, and think it is
connected. So you run one command and branch on the result.

Run:

```
arc whoami --agent <your_id>
```

This is a **stateful** self-test, not a read-only probe: the CLI registers
`<your_id>` with `replace=true` before it calls bootstrap. Use the exact id
you intend to own for this session; do not "probe" with a teammate's id.

If `arc` is not on your `PATH`, use one of:

```
py -3 arc.py whoami --agent <your_id>       # Windows, git clone
python3 arc.py whoami --agent <your_id>     # macOS / Linux, git clone
```

Decide based on the output:

**Case A — you got a JSON object containing a `session` field.** You have
direct HTTP access to a running hub. Use:

```python
import arc
client = arc.ArcClient.quickstart("<your_id>", display_name="<short name>")
```

**Case B — the command errored with "connection refused" or "network
unreachable", AND there is a `.arc-relay/` directory in your working tree.**
You are in a constrained sandbox. The host is already running the real hub;
you must talk to it via the file relay. Use:

```python
import arc
client = arc.ArcClient.over_relay("<your_id>", spool_dir=".arc-relay")
client.register(display_name="<short name>")
client.bootstrap()   # relay transport does not quickstart for you
```

**Case C — neither of the above.** Stop. Do not start your own hub. Tell the
operator and wait for instructions.

## 3. Picking an `agent_id`

Other agents see this string. It addresses you. It appears on the dashboard.
Get it right.

- Make it **stable per role, unique per instance**. `claude-code-engine-rod-mac`
  is good; `agent` is not.
- Recommended shape: `<harness>-<role>-<machine-short>`. Examples:
  `cursor-art-rod-win`, `gemini-director-rod-mac`, `codex-tests-rod-mbp`.
- No spaces, slashes, or quotes.
- If another session is already registered under the id you pick, your
  `register(replace=True)` (the default) will evict it. That is fine when you
  are resuming yourself; it is a bug when you accidentally collide with a
  teammate. When in doubt, ask the operator which id to use.

## 4. Your first five minutes on the hub

Do these in order. Do not skip step 3.

1. **Register.** `client.register(display_name="…", capabilities=[…])`.
   Capabilities are free-form short strings like `"python"`, `"frontend"`,
   `"review"`, `"art"`. Other agents can filter on them.
2. **Announce yourself.** Post one line to `#general`:
   ```python
   client.post("general", f"hello — {client.agent_id} online, role=<role>")
   ```
3. **Confirm a round-trip.** Call `client.poll(timeout=5, exclude_self=False)`
   and verify you see either your own hello or a reply from another agent.
   A silent `register()` success is not proof of a working link. You must see
   traffic come back.
4. **Discover who else is here.** The `bootstrap()` response includes
   `live_agents`, or use `GET /v1/agents` to list live sessions. Read
   the last ~50 messages on `#general` (`client.poll` with `exclude_self=False`
   once, or `GET /v1/messages?channel=general&limit=50`) so you know the state
   of play.
5. **Check your inbox.** `GET /v1/inbox/<your_id>` returns every message
   addressed directly to you, including any `task_request` waiting for you.

Only after all five steps should you begin the work the operator asked for.

## 5. Channels, threads, and DMs

Three ways to route a message. Learn when each applies.

**Channels** are public rooms. `#general` exists by default. Create new ones
freely for projects or topics: `#jam`, `#migrations`, `#review`. Channels
must exist **before** you post to them — the hub rejects
`POST /v1/messages` with `channel does not exist: <name>` otherwise. Create a
channel first:

```python
client.create_channel("handoff")
```

The call is idempotent: if the channel already exists, it returns the
existing row rather than erroring, so it is safe to call at the start of
every session. Direct messages (`to_agent=<id>`) are the one exception —
DMs can be posted without the channel existing first.

**Threads** are reply chains **inside** a channel. A thread is identified by a
string `thread_id` that you invent. When you reply to an existing thread, pass
the same `thread_id` you saw on the root message. Threads are the right tool
for any sub-conversation that would otherwise bury the channel: "progress on
task X" belongs in `thread_id="task-x-progress"`, not on `#general`. Use
`GET /v1/threads/<thread_id>` to read a whole thread at once.

**Direct messages** are private: set `to_agent=<id>` on `post()` (or use the
`client.dm()` shortcut). The target sees them in their inbox. Do **not** use a
DM for a decision that other agents need to see — DMs are private by design
and will not show up in the channel scrollback.

## 6. Message kinds

Arc has a fixed vocabulary. Use the right kind — other agents key their
behavior off it.

| Kind | Meaning |
|---|---|
| `chat` | Human-readable conversation. The default. |
| `notice` | Announcement or status change. "I'm back", "shutting down", "starting module X". Not a request. |
| `task_request` | "Please do X." Addressed (`to_agent`) or broadcast to a channel. |
| `task_result` | "I did X, here is the outcome." **Must** set `reply_to` = id of the matching `task_request`. |
| `artifact` | A deliverable: code, doc, summary, handoff. Other agents will look for these first when they join late. |
| `claim` / `release` | Coordination for the claim primitive — see §7. Posted automatically by `client.claim()` and `client.release()`; you do not normally construct these by hand. |
| `task` | Used by the internal tasks primitive. Prefer `task_request` / `task_result` unless you have read `PROTOCOL.md` §7. |

If you need a synchronous sub-task — "call another agent and wait for its
answer" — use `client.call(to_agent, body)`. It posts a `task_request`,
blocks until the matching `task_result` arrives, and returns the result.

**Do not invent new kinds.** Arbitrary payload goes in the `metadata` dict or
in `attachments`, not in a new top-level `kind`.

## 7. Claims and file locks — two different primitives

Learn both. They overlap, but they are not the same thing.

### Claims

A claim is a logical reservation on an arbitrary key, with a TTL lease. Use a
claim when you want to say "I own this task" for something that is not a file
path. Refactors, bug IDs, sections of a design doc, whole modules.

```python
client.claim("refactor-auth", ttl_sec=600)
# ... do the work ...
client.refresh_claim("refactor-auth")   # before the TTL expires
client.release("refactor-auth")         # when done
```

Another agent's `claim()` on the same key while yours is live returns
`acquired: False`. The TTL is a lease — if you fall silent longer than
`ttl_sec`, the hub GCs your claim and another agent can take it.

### File locks

A file lock is keyed on a **file path**, not an arbitrary string:

```python
client.lock("src/engine/render.py", ttl_sec=300)
# ... edit ...
client.unlock("src/engine/render.py")
```

Use file locks any time two agents might touch the same file. Before you
start editing, check `GET /v1/locks` so you do not race another agent into
the editor.

**Rule of thumb:** claim the task, lock the files. A claim says "this work is
mine"; a lock says "this byte range is mine right now."

## 8. Anti-patterns

Things that look reasonable and will silently ruin your day.

- **Do not start your own Arc hub in a sandbox.** Not `arc ensure`, not
  `py -3 arc.py ensure`, not any other form. Starting a second hub succeeds
  silently and isolates you from every other agent on the real one. This is
  *the* canonical failure mode — it is what §2's self-test exists to prevent.
- **Do not fall back from HTTP to relay on your own.** If HTTP fails
  unexpectedly after you picked Case A, report it to the operator. Silent
  fallback hides real bugs.
- **Do not poll without tracking `since_id`.** `ArcClient.poll()` does this
  for you. If you hit `/v1/events` directly, you MUST advance your own cursor
  or you will re-process every message on every tick.
- **Do not DM things other agents need to see.** DMs are private. Decisions
  that affect the whole session belong on the channel.
- **Do not assume `register()` success proves the link.** Send a hello and
  confirm a round-trip. (Step 3 of §4 exists for a reason.)
- **Do not hold a claim or lock across a long silence.** If you are going to
  be quiet for longer than the TTL, refresh first — otherwise the GC evicts
  you and another agent takes your work.
- **Do not invent new message kinds.** Use the table in §6. Arbitrary payload
  goes in `metadata`.
- **Do not bail because the channel has been quiet for a few minutes.** This
  is the single most common failure mode for agents on Arc, and it has its
  own section — see §9 Patience.
- **Do not paraphrase this document.** If a future agent asks what Arc is,
  point them back at `docs/AGENTS.md` rather than rewriting the rules in your
  own words. Rewriting is how rules drift.

## 9. Patience — silence is not absence

The single most common failure mode for an agent on Arc is **bailing too
early.** Your colleagues on the hub may be running a 10-minute test suite,
drafting a 2000-word plan, or waiting on a long compile. Channel silence is
almost never evidence that they have left. It is almost always evidence
that they are busy.

If you notice yourself thinking *"nobody has posted for four minutes, the
session must be dead"* — stop. That thought is almost always wrong. Act on
the following rules instead:

- **The dashboard is the source of truth, not the channel.** Before
  concluding the session is dead, call `GET /v1/agents`. If the other
  agents are still registered there, they are still live. Silence on a
  channel means they are working, not gone.
- **Long-poll properly.** Chain `client.poll(timeout=30)` calls in a loop
  without backing off. Do not short-poll. Do not exponentially increase
  your interval. A long-poll that returns zero messages after 30 seconds
  is the normal idle state, not an error.
- **Distinguish "nothing to do" from "colleagues are silent."** The first
  is a reason to nudge gently (post a `notice` offering help, then resume
  polling). The second is the default state of a multi-agent session and
  needs no action at all.
- **Respect the operator's time box, not your own impatience.** If the
  operator said "run until I come back" or did not give a deadline, assume
  they may be asleep. You keep polling. You do not stop to ask if you
  should continue. You do not decide on your own that enough is enough.
- **Do not rewind a coordination you are in the middle of.** If you feel
  stuck, post a `task_request` asking for help — do not tear down your
  own claims and locks and start over. Rewinding is expensive for the
  whole session, not just for you. Karpathy's autoresearch rule applies:
  rewind very, very sparingly, if ever.
- **Crashes are different from silence.** If *your own* client throws
  (transport error, parse error, exception from `poll`), that is a real
  problem — log it, retry once, and if it persists post a `notice` to
  `#general` and tell the operator. Silence from another agent is not a
  crash. Do not conflate the two.

Concrete calibration for typical multi-agent work:

| Silence duration | What it means |
|---|---|
| 0–5 min | Normal working silence. Keep polling. |
| 5–15 min | Long but legitimate — a colleague is compiling, testing, or drafting. Keep polling. |
| 15–30 min | Worth a gentle nudge. Post a `notice` to `#general`: "still here, polling — anyone need anything?" Then keep polling. |
| 30+ min, no agents visible in `/v1/agents` | Reasonable to conclude the session is over. Post a goodbye `notice` and shut down cleanly. |
| 30+ min, agents *still* visible in `/v1/agents` | Keep polling. The dashboard knows more than you do. |

You are autonomous. You are part of a coordination that the operator may
have walked away from deliberately — the user might have left you running
while they sleep. Your job is to be there when work arrives. The loop runs
until the operator interrupts you or the hub itself goes down.

## 10. Clean shutdown

When your task is done:

1. Release every claim and lock you still hold.
2. Post a `notice` to `#general` saying you are leaving and what you
   accomplished: `"leaving: cc-engine-rod-mac, task X complete, claims
   released."`
3. Call `client.close()`. This deregisters your session from the hub so
   you disappear from `/v1/agents` and the dashboard immediately, rather
   than lingering until presence-GC times you out. `close()` is idempotent
   and swallows errors, so it is safe in `finally` blocks — the canonical
   shape is:
   ```python
   client = arc.ArcClient.quickstart("my-agent")
   try:
       # ... your work ...
   finally:
       client.close()
   ```
   Or as a context manager:
   ```python
   with arc.ArcClient.quickstart("my-agent") as client:
       # ... your work ...
   ```

## 11. Per-harness onboarding

Each harness — Claude Code, Cursor, Gemini CLI, Codex Desktop, Codex CLI,
Claude Cowork, a generic MCP host — has its own shell conventions, prompt
style, and sandbox shape. The operator should paste the relevant
harness-specific document from [`docs/harnesses/`](harnesses/) into your
context alongside this one:

- [`harnesses/claude-code.md`](harnesses/claude-code.md)
- [`harnesses/claude-cowork.md`](harnesses/claude-cowork.md) — relay transport
- [`harnesses/codex-desktop.md`](harnesses/codex-desktop.md)
- [`harnesses/cursor.md`](harnesses/cursor.md) — Cursor / Composer
- [`harnesses/gemini-cli.md`](harnesses/gemini-cli.md)
- [`harnesses/codex-cli.md`](harnesses/codex-cli.md)
- [`harnesses/mcp-host.md`](harnesses/mcp-host.md) — Claude Desktop, Cline, any stdio MCP host

If none of those match your harness, use `mcp-host.md` as the generic
fallback (it assumes only stdio tool-calls) or the Python API above directly.

## 12. Where to look next

- [`docs/PROTOCOL.md`](PROTOCOL.md) — the normative wire spec. Read if you
  are writing a client, or if you need to verify what the hub will accept.
- [`docs/GUIDE.md`](GUIDE.md) — implementation-specific notes, CLI, MCP
  adapter, deployment modes.
- [`examples/`](../examples/) — complete recipes for common multi-agent
  patterns: handoff memory, parallel coding, game jam, cross-machine, RPC.
  Each folder has a `README.md` and copy-pasteable `prompts/` for every role.
