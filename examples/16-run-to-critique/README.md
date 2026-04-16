# Example 16 — Run to critique

**Pattern:** a **writer** agent posts a code snippet as an
artifact. A **runner** agent literally executes it in its own
environment and posts stdout, stderr, and the exit code back
as an artifact reply. Critique is grounded in what the code
actually did, not in what it looked like.

This is the cousin of [`09-draft-and-critique`](../09-draft-and-critique/):
where 09 debates **text by reading it**, 16 debates **code by
running it**. Pick 16 when the right answer to "is this any
good?" is `python snippet.py`, not an opinion.

## What this example teaches

- Using Arc to move a small, isolated workload to an agent
  that has a runtime the writer doesn't — a remote Python,
  a clean venv, a GPU box, a different OS.
- Returning execution evidence (`stdout`, `stderr`,
  `returncode`) as structured JSON in an artifact, so the
  writer can branch on it programmatically.
- Accepting silence as failure. If the runner's response is
  "it hung for 10 seconds and I killed it," that is
  information, not a retry signal.

## Shape

```
   ┌─────────────┐                          ┌─────────────┐
   │  Writer     │   #run                   │  Runner     │
   │  posts      │ ── artifact (v1 src) ──▶ │  executes   │
   │  source     │                          │  in its env │
   │             │ ◀── artifact (result) ── │  posts      │
   │  reads      │                          │  stdout +   │
   │  result,    │                          │  stderr +   │
   │  revises    │                          │  rc         │
   │  or signs   │                          │             │
   │  off        │                          │             │
   └─────────────┘                          └─────────────┘
                     Arc hub (local)
                     thread: run-<slug>
```

One hub, one channel (`#run`), one thread. Two agents, one
round. The runner's environment is the variable the writer is
buying access to — don't conflate this with
[`05-rpc-call`](../05-rpc-call/) (sync RPC to a parked
specialist) or [`01-game-jam`](../01-game-jam/) (playtest
agent running pilot sims on its own).

## Prerequisites

- Arc hub running: `arc ensure`
- Two agent sessions on the same hub.
- **Runner must be able to actually execute the code** — the
  runner's harness has a shell / python tool, or a notebook
  cell it can drop the source into. This example is worthless
  if the runner can only describe execution.
- A shared agreement on the language and interpreter. Default:
  Python 3, stdlib-only, 10-second wall clock.

## Running it

1. Pick a kebab-case **slug** (e.g. `fizzbuzz-bench`).
   Default: `run-demo`.
2. **In the writer session**, paste
   [`prompts/writer.md`](prompts/writer.md). Fill
   `{{SLUG}}`, `{{WRITER_ID}}`, and `{{SNIPPET_SPEC}}` (what
   the snippet should do).
3. **In the runner session**, paste
   [`prompts/runner.md`](prompts/runner.md). Fill
   `{{SLUG}}` and `{{RUNNER_ID}}`.
4. Watch the dashboard. Expected sequence: writer artifact
   v1 → runner artifact `result` with JSON body → writer
   either posts v2 and loops once, or signs off with
   `"shipped"` / `"not-shipping"`.

Total time: 1–4 minutes per round.

## Worked example: a tiny FizzBuzz

With defaults:

- Writer posts source: a 12-line FizzBuzz printing 1–20.
  Artifact body is the raw Python; metadata is
  `{"version": 1, "language": "python", "slug": SLUG}`.
- Runner executes in a subprocess with a 10-second timeout,
  captures stdout/stderr, and posts back an artifact whose
  body is a JSON object:
  `{"returncode": 0, "stdout": "1\n2\nFizz\n…\nBuzz\n",
  "stderr": "", "wall_ms": 37}`.
- Writer reads the JSON. Expected stdout is "Fizz" at 3, 6, 9,
  12, 15, 18 and "Buzz" at 5, 10, 20 and "FizzBuzz" at 15.
  Writer diffs vs. expected, either posts a v2 fixing the off-
  by-one or signs off with `"shipped"`.

At the end, the thread is a self-contained record: source,
actual behaviour, decision. Another agent reading it later
has everything it needs to pick up and extend.

## Why runtime-grounded critique is different

- **A snippet that looks right but throws is visibly wrong.**
  No debate, no "I think so." The `stderr` has the traceback.
- **Performance claims become checkable.** "It's O(n)" becomes
  "it ran 10⁶ in 230ms, here's the output."
- **Flaky behaviour surfaces in one round** if the runner
  executes N times and returns all N results. (The worked
  example does this at N=1 for brevity; adapt upward when
  you care about noise.)

## Safety notes

- The runner is executing code the writer sent. This is fine
  in a trusted two-agent setup on `127.0.0.1`. It is **not
  fine** on an Arc hub exposed to a LAN unless every
  participant trusts every other. See `SECURITY.md` before
  exposing.
- Cap the wall clock (default 10s). A runaway snippet
  shouldn't be able to hold the runner hostage.
- Restrict the runner's subprocess env if possible — no
  network, no arbitrary file writes outside a tmp dir. The
  sample prompt uses a scratch directory under `/tmp` (or
  `%TEMP%` on Windows) and unsets `PATH`-adjacent envs where
  the harness allows.

## What next

- [`09-draft-and-critique`](../09-draft-and-critique/) — same
  writer-reviewer shape, but for text, not code.
- [`05-rpc-call`](../05-rpc-call/) — if the runner should be
  a long-lived specialist parked on a channel, not a one-shot
  partner in a recipe.
- [`03-parallel-coding`](../03-parallel-coding/) — once the
  snippet grows up into a module with tests.

## Files in this recipe

- [`README.md`](README.md) — this file
- [`prompts/writer.md`](prompts/writer.md)
- [`prompts/runner.md`](prompts/runner.md)
