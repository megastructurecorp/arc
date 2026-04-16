# Shared protocol — how jam agents coordinate on Arc

This document is for every agent in the jam, regardless of role.
Paste it into every session along with `docs/AGENTS.md`, the
harness-specific file, and `game-brief.md`. Your role-specific
prompt goes on top of these.

## 1. Channels, threads, and what goes where

There is exactly one channel for the jam: **`#jam`**. You create
it once at the start of phase 0 (see §3). It must exist before
anyone posts to it — the hub rejects posts to nonexistent
channels.

Inside `#jam`, there are six named threads that every agent
watches:

| Thread ID | Purpose | Who posts |
|---|---|---|
| `jam-interface` | The interface contract. Also phase-0 acks. | All agents (ack); whoever proposes a contract change. |
| `jam-reports` | Playtest reports, each one an `artifact`. | Playtest agent only. All other agents read. |
| `jam-engine-work` | Engine agent's in-progress notes, questions, file-lock announcements. | Engine agent primarily; others reply with questions. |
| `jam-content-work` | Same, for content agent. | Content agent primarily. |
| `jam-pilots-work` | Same, for pilots agent. | Pilots agent primarily. |
| `jam-playtest-work` | Same, for playtest agent. | Playtest agent primarily. |

Top-level `#jam` posts (no `thread_id`) are reserved for
**milestones** only — phase transitions, report-triggered
re-balance calls, and "signing off" notices. Do not clutter the
top level with implementation chatter. That belongs in a work
thread.

Direct messages (`to_agent=<other>`) are for **private
clarifications** only — e.g. "are you blocking on me for X?"
Use sparingly. Anything a third agent might care about belongs
on a thread, not in DMs.

## 2. Your `agent_id` for a jam

Shape: `<harness>-jam-<role>-<short-tag>`. The `jam-` infix makes
it obvious on the dashboard that this session is a jam
participant:

- `cc-jam-engine-rod-mac`
- `cursor-jam-content-rod-win`
- `gemini-jam-pilots-rod-mac`
- `codex-jam-playtest-rod-mbp`

One role per agent. If your operator has not told you which role
you are, stop and ask them — do not guess.

## 3. Phase 0 — interface lock

You do this exactly once, at the very start of the jam, before
any code is written.

1. The first agent to join creates the channel (idempotent —
   safe if another agent beat you to it):
   ```python
   client.create_channel("jam")
   ```
2. Every agent posts a hello on `#jam` top-level:
   ```python
   client.post("jam", f"{client.agent_id} online, role=<role>", kind="notice")
   ```
3. Every agent then polls briefly to confirm the round-trip. See
   `AGENTS.md` §4 step 3.
4. Whichever agent is first to be ready — traditionally the
   engine agent, but any of the four can do it — posts the full
   contents of `game-brief.md` §3 (the interface contract) to
   `jam-interface` as a single `artifact`:
   ```python
   with open("prompts/shared/game-brief.md") as f:
       brief = f.read()
   client.post(
       "jam",
       brief,
       kind="artifact",
       thread_id="jam-interface",
       metadata={"slot": "contract", "version": 1},
   )
   ```
5. Every other agent reads the contract, verifies their role's
   section matches what they understood, and posts an `ack`
   notice on the same thread:
   ```python
   client.post(
       "jam",
       f"<role> agent ack: contract v1 accepted",
       kind="notice",
       thread_id="jam-interface",
       metadata={"phase": "ack", "version": 1},
   )
   ```
6. The interface is "locked" once all four acks are visible on
   `jam-interface`. Any agent can tally the acks by polling the
   thread.
7. **No code may be written before all four acks are in.** If
   you finish your section and not everyone has acked yet, wait.
   The patience rules in `AGENTS.md` §9 apply here exactly.

If an agent sees a problem with the contract, they post a `chat`
message on `jam-interface` with the concern instead of acking.
The proposing agent (whoever posted the contract) can then post
a v2 and the ack cycle repeats. Do not start coding until the
contract stabilizes.

## 4. Phase 1 — parallel build

Once the interface is locked, every agent builds their own module
in parallel.

### 4.1 File locks

Before you touch any file, lock it:

```python
client.lock("engine.py", ttl_sec=900)
# ... edit engine.py ...
client.unlock("engine.py")
```

Your agent owns exactly the files listed in your role prompt —
no others. If you need to touch something outside your ownership
(e.g. the engine agent needs to add a helper to `content.py`),
**do not** just lock and edit. Instead:

1. Post a `chat` message on the other agent's work thread
   explaining the change you want and why.
2. Wait for them to accept or propose a different design.
3. Let them make the edit on their own schedule.

You lock. You own. You do not cross borders.

### 4.2 Work threads

Post to your own work thread (`jam-<role>-work`) whenever:

- You start a new module or file (post a `notice` naming the
  file and your planned duration)
- You encounter an ambiguity in `game-brief.md` that needs a
  ruling (post a `chat` with the question — tag the relevant
  other agent via `to_agent` if the ambiguity is clearly their
  domain)
- You finish a phase of work (post a `notice` summarizing what
  is now testable)

Other agents poll your thread periodically. A thread you never
post to makes you look inactive; don't do that.

### 4.3 Patience during silent stretches

See `AGENTS.md` §9. The typical sin during a jam is bailing
after 4 minutes of silence while another agent is mid-compile or
mid-test suite. Don't. Chain `client.poll(timeout=30,
thread_id=...)` calls. Check `GET /v1/agents` if you are worried
someone has gone — if they are still in the agents list, they
are still alive.

## 5. Phase 2 — first playtest report

The **playtest agent** decides when phase 1 is done. The signal:

1. All four agents have posted at least one `notice` to their
   work thread saying "module ready for integration".
2. Playtest agent runs `run_benchmark(ALL_PILOTS, n=100)`
   against the current codebase. Each pilot plays 100
   independent seeded games.
3. Playtest agent posts the formatted report as an `artifact`
   to `jam-reports`:
   ```python
   client.post(
       "jam",
       report_text,
       kind="artifact",
       thread_id="jam-reports",
       metadata={"slot": "report", "version": 1, "n_games": 100},
   )
   ```
4. Playtest agent posts a top-level `notice` on `#jam`
   announcing that a new report exists — this is one of the few
   things that belongs on top-level `#jam`:
   ```python
   client.post(
       "jam",
       "playtest report v1 posted to jam-reports — please review",
       kind="notice",
   )
   ```

Every other agent then reads `jam-reports` and responds **to
the data**, not to each other. Patterns:

- Engine agent finds a rule that causes a pilot to crash or
  hang: post a fix proposal on `jam-engine-work`, lock the file,
  make the change, post a follow-up notice.
- Content agent sees one pilot accumulating absurd wealth (or
  going bankrupt every game): hypothesises which economy modifier
  or commodity base price is off, proposes a data-file change on
  `jam-content-work`, locks the file, changes, follows up.
- Pilots agent sees one of their pilots always strands or
  bankrupts: revises the heuristic on `jam-pilots-work`, or
  retires the pilot with a notice ("BargainHunter cut in v2").
- Playtest agent: waits for a quiet stretch (no `jam-*-work`
  posts in 5 minutes), then runs the next benchmark and posts
  report v2.

## 6. Phase 3 — iterate

Phase 2 repeats until one of:

- The playtest agent's declared balance criterion is met —
  typically "all pilots finish within a wealth band that matches
  their philosophy, no pilot strands more than 10% of the time,
  and at least three distinct economies appear in the visit
  histogram." The exact criterion is owned by the playtest
  agent and announced in phase 0.
- The jam has run N iterations with no improvement — declare
  current state the final and proceed to phase 4. The playtest
  agent makes this call.
- The operator explicitly stops the jam.

Reports are versioned (`version` metadata). Each iteration
increments the version. The `jam-reports` thread becomes an
audit trail of the balance conversation.

## 7. Phase 4 — wrap

When the jam is done:

1. Playtest agent posts a final report to `jam-reports` marked
   `metadata={"final": true}`.
2. Every agent runs their test suite one final time and posts
   the result as a `notice` on their own work thread.
3. Every agent releases every file lock they still hold.
4. Every agent posts a goodbye `notice` on top-level `#jam`:
   ```python
   client.post(
       "jam",
       f"{client.agent_id} signing off, module <file> complete, tests green",
       kind="notice",
   )
   ```
5. Call `client.close()` to deregister cleanly — this removes
   your session from `/v1/agents` and the dashboard immediately.

## 8. Things that break a jam

Short list of real failure modes, in decreasing order of
frequency:

- **One agent becomes the listener.** The moment an agent has no
  module they own, they are listening to everyone. This fills
  their context and makes them useless within an hour. Fix:
  every agent owns a module, always.
- **Interface-lock gets skipped.** Someone gets excited, starts
  coding before acks are in, and the other three find out later
  that their types disagree. Fix: phase 0 is not optional.
- **Cross-file edits without asking.** Agent A sneakily edits
  agent B's file to "fix" something. Agent B's next edit
  conflicts. Resentment follows. Fix: own your files, request
  changes on work threads.
- **Bailing on silence.** Agent C decides the jam is over
  because it has been quiet for 5 minutes while agent A's test
  suite runs. Fix: `AGENTS.md` §9. Check `/v1/agents`, not the
  channel.
- **Balance is argued instead of measured.** Agents post
  opinions about commodity prices or pilot thresholds without
  running the actual benchmark. Fix: balance claims without a
  report number are dismissed.
- **Scope creep.** "What if we added combat" in phase 3. Fix:
  phase 3 is iteration within the v1 contract, not new
  features. Ship first, extend later.
