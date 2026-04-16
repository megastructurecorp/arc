# Example 03 — Parallel coding

**Pattern:** two agents split a small codebase by file, lock
their files so nobody steps on them, and coordinate their work
through a single shared channel. No playtest agent, no judge,
no fancy role matrix — just two agents writing code at the same
time without tripping over each other.

This is the recipe we recommend for **your first multi-agent
Arc session**. It is deliberately the smallest thing that earns
its complexity: two agents, one channel, one claim, two file
locks, and a runnable result.

## When to use this recipe

- You have a small task that decomposes into **two files** (a
  library + its test suite, a parser + its runner, a frontend
  widget + its stylesheet, a migration + its rollback).
- You want both agents working in parallel instead of one
  coding while the other waits.
- You want to learn how `client.lock(...)` behaves before
  graduating to four-agent jams or cross-machine setups.

## When *not* to use this

- One-file changes. Two agents on one file through a lock is
  worse than one agent doing the whole thing — the lock just
  serializes them.
- Tasks where the two halves have a deep, evolving interface.
  If the contract between the two files will change several
  times, use the four-role jam pattern in `01-game-jam/` — it
  has explicit machinery for contract re-locks.

## Topology

```
            ┌──────────────┐            ┌──────────────┐
            │ Library      │            │ Tests        │
            │ agent        │            │ agent        │
            │ owns         │            │ owns         │
            │ litecsv.py   │            │ tests/       │
            └──────┬───────┘            └──────┬───────┘
                   │                           │
                   │      posts & polls        │
                   ▼                           ▼
            ┌──────────────────────────────────────┐
            │          Arc hub (single)            │
            │                                      │
            │ channel: #build                      │
            │ locks:   litecsv.py                  │
            │          tests/test_litecsv.py       │
            │                                      │
            │ messages:                            │
            │   lib   → notice "starting X"        │
            │   tests → notice "pytest -k X fails" │
            │   lib   → notice "fixed, try again"  │
            │   tests → notice "all green"         │
            └──────────────────────────────────────┘
```

One hub, one channel (`#build`), two file locks, two agents.
That is the entire moving-parts list.

## Prerequisites

- Arc hub running: `arc ensure`
- Two agent sessions ready (same or different harnesses — see
  `docs/harnesses/` for onboarding)
- A shared project directory both agents can read and write
- Python 3.10+ in that directory (only because the worked
  example is a Python library)

## Worked example: `litecsv`

A tiny zero-dependency CSV parser. ~60 lines of library code +
a dozen tests. Small enough that both agents can finish in a
few minutes, big enough that the lock discipline actually
matters.

### Interface contract (one paragraph — no fancy `game-brief.md`)

```python
# litecsv.py — public API

def parse(text: str) -> list[dict[str, str]]:
    """Parse CSV text. First row is the header. Fields may be
    quoted with double quotes; escaped double quotes appear as
    `""` inside a quoted field. Empty cells are the empty
    string. Returns a list of dicts keyed by header name."""

def dumps(rows: list[dict[str, str]]) -> str:
    """Serialize a list of dicts to CSV text. Header order is
    taken from the keys of the first row. Fields containing a
    comma, double quote, or newline are quoted; inner double
    quotes are escaped by doubling."""
```

That is the whole contract. Both agents see it. The library
agent implements it in `litecsv.py`; the tests agent asserts it
in `tests/test_litecsv.py`. Neither can see the other's file
without reading it from disk — they communicate through
`#build`.

## Running the recipe

1. **Start the hub**: `arc ensure`
2. **Open two agent sessions.** Label them in your head as
   "library" and "tests". They can be two Claude Code windows,
   one Claude Code + one Cursor, two Gemini CLI sessions — any
   combination that all harnesses listed under
   `docs/harnesses/`.
3. **Paste the matching prompt file** as the first message in
   each session:
   - Library agent: [`prompts/library.md`](prompts/library.md)
   - Tests agent: [`prompts/tests.md`](prompts/tests.md)
4. **Watch `#build` on the dashboard** (`http://127.0.0.1:6969`).
   You will see the two agents say hello, lock their files,
   post progress notices, and eventually converge on "all
   tests pass."
5. **You are done when** the tests agent posts a final notice
   `"all tests green"` and both agents have released their
   locks and signed off.

The first full pass usually takes 5–10 minutes of wall-clock
depending on harness speed. If it takes 20 minutes and nothing
has happened, one agent probably did not read its file —
check the dashboard for who posted what.

## What the locks actually protect you from

In this recipe, the two agents own disjoint files. `litecsv.py`
vs `tests/test_litecsv.py` — no overlap. So why bother with
locks at all?

- **Discipline.** The two agents learn the habit of
  `client.lock(path)` → edit → `client.unlock(path)` **before**
  they graduate to a recipe where files are actually contested.
  Muscle memory is cheap now and expensive later.
- **Retries.** If a test fails and the library agent wants to
  edit `litecsv.py` again, the lock prevents a race with a
  future version of the recipe where the tests agent patches
  the library to add a test helper import. Not contested today,
  probably contested tomorrow.
- **Dashboard signal.** An active lock appears on
  `GET /v1/locks` and on the dashboard. You (the operator) can
  see at a glance what each agent is in the middle of. A
  missing lock is a "this agent is idle or polling" signal.
- **Cross-file edits.** If an agent ever needs to touch the
  *other* agent's file — e.g. the library agent wants to add a
  docstring example that the tests agent also references —
  the operation is "ask on `#build`, wait for the other agent
  to `unlock` its file, lock it yourself, edit, unlock,
  release." The lock makes that protocol natural.

You are paying the complexity of file locks at the easiest
possible scale, so the habit is in place when you scale up.

## Feedback loop

Feedback flows through `#build`, not through direct inspection
of each other's files. The typical loop:

1. Library agent writes `litecsv.py` happy path, posts
   `"litecsv v0: parse + dumps happy path done"`.
2. Tests agent writes `tests/test_litecsv.py` against the
   interface contract, runs `pytest`, posts the result:
   `"pytest: 8 passed, 2 failed — empty-cell test fails,
   quoted-comma test fails"`.
3. Library agent reads the failures, fixes `parse()` to handle
   empty cells, re-posts `"v1 pushed, retry"`.
4. Tests agent re-runs `pytest`, posts
   `"pytest: 10 passed — all green"` and a goodbye `notice`.
5. Library agent posts its own goodbye `notice`, releases
   locks, signs off.

Neither agent reads the other's file directly. They could —
Arc does not stop them — but the discipline is that
**coordination messages beat file-peeking** because messages
leave a trail on the dashboard and file-peeking doesn't. If
the tests agent reads `litecsv.py` silently, only the tests
agent knows what it just saw; the operator loses context.

## Files in this recipe

- [`README.md`](README.md) — this file
- [`prompts/library.md`](prompts/library.md) — paste into the
  library agent's session
- [`prompts/tests.md`](prompts/tests.md) — paste into the tests
  agent's session
- [`demo.py`](demo.py) — a scripted two-thread version of the
  pattern you can run end-to-end with one Python process and
  one local hub. Useful for smoke-testing that file locks,
  channels, and long-poll all work before trusting the pattern
  to two real LLM sessions.

## Adapting to your own task

Replace `litecsv.py` / `tests/test_litecsv.py` with any pair
of files where:

- each agent can make meaningful progress on its file in
  parallel, and
- the interface between them can be stated in a paragraph (or
  less).

Good pairs:
- a library + its test suite (this recipe)
- a React component + its CSS/stylesheet
- a database migration + its rollback script
- a CLI parser + the handler functions it dispatches to
- a protobuf schema + the Python bindings that consume it

If you find yourself wanting three files per agent, you
probably want `01-game-jam/` instead — the four-role jam
template handles that cleanly with work threads and named
phases.
