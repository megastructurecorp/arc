# Example 01 — Game Jam

**Pattern:** four heterogeneous agents — possibly from four
different harnesses — collaborate through an Arc hub to build,
playtest, and balance a small game in one session. No human is
the bottleneck; agents critique each other's work by *running
it*, not by arguing about it.

This example is a **template** you can adapt to any jam, plus a
**worked example** (`Arc Jam Starlane` — a text-based Elite-alike
exploration and trading game) that is battle-tested and runnable
as-is.

## What this example teaches

Running a multi-agent game jam sounds glamorous and fails for
boring reasons. The four rules below are the distilled output of
actual jams that worked and actual jams that didn't. If you
remember nothing else, remember these:

### 1. Every agent makes something, designs something, and tests something

No pure-critic roles. No "judge" agent. The moment one agent is
purely listening and opining while others write code, **that
agent becomes a context sink** — it has to hold the whole game
in its head to say anything useful, which overloads its context
(especially for smaller models) and slows everyone else down
waiting for feedback.

Give every agent a concrete module they own. That module has:
- **code** they write
- **design decisions** that only they make (which rules, which
  data tables, which AI pilots, which metrics)
- **tests** that live alongside their module and that only they
  own

If an agent's output is "my feedback on your code," they will
drown. If an agent's output is "my module, which exercises your
code," they produce something and review something in the same
act.

### 2. Divide the workload by files, not by feature

Two strategies that both work:

- **Multiple files, one per agent** (this example). Each agent
  owns one `*.py` file and its corresponding test file.
  File-level ownership means `client.lock(path)` has clean
  semantics — no two agents ever touch the same file. This is
  the easier mode and the default.
- **Single file, ownership by region or by content** (for very
  small jams). One agent owns "the engine loop," another owns
  "the commodity tables inside the same file," etc. You need
  tighter coordination on the lock — typically the file is
  locked by one agent at a time and the others wait — but it
  can work for a 200-line game.

Do **not** split work by "one agent codes, others give
feedback." That is the pattern that blows up small-model
context.

### 3. Feedback flows through the playtest report, not through chat

The playtest agent runs N seeded sessions for each of the pilot
agent's AI pilots using the engine's code and the content
agent's galaxy data. It posts a report with per-pilot wealth,
stranding rate, systems visited, and any crashes — and any
"broken" observations (e.g. "GreedyTrader accumulates 10× the
next pilot's credits — economy mods too wild").

**Every agent then reads the report and responds to data.**

The content agent sees "GreedyTrader dominates" and flattens the
economy modifier curve. The pilots agent sees "ScoutExplorer
strands 30% of the time" and tightens its fuel reserve logic.
The engine agent sees "5% of pilots crashed on refuel" and
finds the ValueError case. The playtest agent sees its own
report is noisy and increases n.

This loop is why everyone needs to make things: if the content
agent were a pure critic, they'd be saying "I don't like the
economy" with no authority. Because they own `content.py`, the
`data/*` tables, and the balance tests, they say "my data file
change brings the richest:poorest ratio from 8× to 3×." That
proposal is grounded in running code.

### 4. Scope tight. Ship a playable game, not a designed game.

The jams that work ship something playable end-to-end in the
first third of the session, then iterate on balance and polish.
The jams that fail spend the whole session designing and never
ship.

**Pick a game small enough that "v1 runs a full session without
crashing" happens within the first 90 minutes.** A text-based
trading game with no combat, a fixed action vocabulary, and a
purely seed-deterministic universe is safely under that budget,
which is why the worked example below is scoped that way — it's
the same shape as Ian Bell's original text Elite, which fit in a
few hundred lines of BASIC.

## Topology

```
            ┌─────────────┐         ┌─────────────┐
            │ Engine agent│         │Content agent│
            │  owns       │         │ owns        │
            │  engine.py  │         │ content.py  │
            │             │         │ data/*      │
            └──────┬──────┘         └──────┬──────┘
                   │                       │
                   │   #jam  +  threads    │
                   │  jam-engine-work      │
                   │  jam-content-work     │
                   ▼                       ▼
            ┌──────────────────────────────────────┐
            │            Arc hub (single)          │
            │                                      │
            │ channel: #jam   (milestones, reports)│
            │ thread: jam-interface  (contract)    │
            │ thread: jam-reports    (playtest)    │
            └──────────────────────────────────────┘
                   ▲                       ▲
                   │                       │
            ┌──────┴──────┐         ┌──────┴──────┐
            │Pilots agent │         │Playtest     │
            │ owns        │         │ agent       │
            │ pilots.py   │         │ owns        │
            │             │         │ pilot_run.  │
            │             │         │ py, cli.py  │
            └─────────────┘         └─────────────┘
```

Four sessions, four role prompts, one hub, one `#jam` channel,
one `jam-interface` thread for the shared interface contract,
one `jam-reports` thread for iterated playtest reports, and
per-role work threads for detailed chatter.

## Prerequisites

- Arc hub running: `arc ensure`
- 4 agent sessions ready (can be any mix of harnesses — see
  `docs/harnesses/` for onboarding)
- A shared project directory that all 4 agents can write to

## Running the jam

1. **Pick a game idea** — or use the worked example below.
2. **Kick off each of the four agent sessions.** You have two
   equivalent ways to do this:
   - **Single template, filled in per agent.** Open
     [`OPERATOR_KICKOFF.md`](OPERATOR_KICKOFF.md) and paste the
     template block as the first user message in each agent,
     substituting `{{ROLE}}` (`engine` / `content` / `pilots`
     / `playtest`) and `{{HARNESS}}` (`claude-code` / `cursor`
     / `gemini-cli` / etc.). This is the right choice if you
     want full autonomy — the template includes explicit
     "don't stop until the jam is complete" instructions.
   - **Pre-filled per-role start prompts.** Alternatively,
     copy the matching file from [`prompts/start/`](prompts/start/):
     [`engine.md`](prompts/start/engine.md),
     [`content.md`](prompts/start/content.md),
     [`pilots.md`](prompts/start/pilots.md),
     [`playtest.md`](prompts/start/playtest.md). These are
     shorter and assume the operator stays in the loop.

   Both flows tell the agent to read, in order:
   `docs/AGENTS.md` → its matching file in `docs/harnesses/`
   → `prompts/shared/jam-protocol.md` →
   `prompts/shared/game-brief.md` → its role prompt in
   `prompts/roles/`. There is no extra context to assemble.
3. **Phase 0 (interface-lock).** All four agents meet on
   `#jam`. The interface contract in `game-brief.md` is posted
   to thread `jam-interface` as an `artifact`. Each agent
   acknowledges by posting a `notice` saying
   "engine/content/pilots/playtest locked on interface v1." No
   code until all four acks are in.
4. **Phase 1 (parallel build).** Each agent implements its
   module against the contract. File-locks via
   `client.lock(path)`. Progress updates on `jam-<role>-work`
   threads.
5. **Phase 2 (first playtest report).** Playtest agent declares
   it can run the benchmark; posts first report to
   `jam-reports`. All four agents read and respond with
   changes.
6. **Phase 3 (iterate).** Loop: patch → new benchmark run →
   report → patch. Typically 3–5 iterations before the
   economy feels reasonable.
7. **Phase 4 (wrap).** All four agents post a goodbye `notice`
   to `#jam`, release locks, and sign off.

## Worked example: Arc Jam Starlane

A text-based single-player space exploration and trading game,
directly inspired by Ian Bell's text port of Elite. Playable by
a human via `cli.py` or by an AI pilot via `pilot_run.py`,
~1000 lines of Python across four modules plus half a dozen
small data files. **Two viable progression paths:** classic
commodity trading (buy low at one system, sell high at
another) and exploration data trading (scan unique systems to
produce scan records, carry them to HIGH_TECH buyers who pay
premium). Full spec in
[`prompts/shared/game-brief.md`](prompts/shared/game-brief.md).
It was chosen because:

- **Single-player and seed-deterministic.** One ship flies
  through the galaxy, every system's properties come from
  `(seed, x, y)` with no hidden state. Two players playing
  seed=42 see the same systems with the same names and the
  same prices, forever. This makes tests and benchmarks
  reproducible and means the playtest agent can compare pilots
  fairly by running them over identical seed ranges.
- **Infinite galaxy via procedural generation.** There is no
  "map". Every integer coordinate is either empty space or a
  star system, decided by a deterministic hash. The content
  agent's density tuning is what makes the galaxy feel
  populated.
- **All the fun is in content.** Engine code is modest (a few
  hundred lines — jump math, market transactions, stranded
  detection). The real design work is the content agent's
  system name generator, economy modifier tables, commodity
  catalog, and description templates. This is a good fit for
  a jam because one agent has a fat juicy design surface
  instead of everyone fighting for scraps.
- **No combat.** v1 has five actions (jump, refuel, buy, sell,
  end_session) and no NPC encounters. This keeps engine code
  small and content code interesting.
- **Plays both ways.** The same binary runs as a human CLI and
  as a headless AI-pilot benchmark. The four required pilots
  (GreedyTrader, ScoutExplorer, SafeHauler, BargainHunter)
  exercise different parts of the economy, so the playtest
  report genuinely tells you whether the content agent's
  tuning is balanced.

Run the jam end-to-end with the four start prompts in
`prompts/start/`. At the end you should have a project tree
something like:

```
games/starlane/
    engine.py
    content.py
    pilots.py
    pilot_run.py
    cli.py
    data/
        syllables.txt
        adjectives.txt
        fauna.txt
        remarkable.txt
        commodities.toml
        economy_mods.toml
    tests/
        test_engine.py
        test_content.py
        test_pilots.py
        test_pilot_run.py
```

## Adapting for your own jam

See [`TEMPLATE.md`](TEMPLATE.md) — it walks through which parts
of the prompts to edit when you're swapping Arc Jam Starlane
out for something else (a roguelike, a card game, a dice game,
a text adventure). The four-role split holds for most small
turn-based or single-player games; what changes is the
interface contract and the balance metrics.

## Files in this recipe

- [`README.md`](README.md) — this file
- [`OPERATOR_KICKOFF.md`](OPERATOR_KICKOFF.md) — **the single
  fill-in-the-blanks operator prompt**. Paste it into each
  agent session with `{{ROLE}}` and `{{HARNESS}}` swapped in.
  Use this for end-to-end autonomous jams.
- [`TEMPLATE.md`](TEMPLATE.md) — how to adapt for your own game
- [`prompts/start/`](prompts/start/) — the four **pre-filled
  per-role start prompts**, one per role. An alternative to
  `OPERATOR_KICKOFF.md` for operators who want to stay in the
  loop.
- [`prompts/shared/game-brief.md`](prompts/shared/game-brief.md)
  — full Arc Jam Starlane spec + interface contract (the
  artifact that gets posted to `jam-interface`)
- [`prompts/shared/jam-protocol.md`](prompts/shared/jam-protocol.md)
  — how jam agents coordinate on Arc (channels, threads, locks,
  milestones)
- [`prompts/roles/engine.md`](prompts/roles/engine.md) — engine
  agent (detailed role doc, read by the engine agent during
  bootstrap)
- [`prompts/roles/content.md`](prompts/roles/content.md) —
  content agent
- [`prompts/roles/pilots.md`](prompts/roles/pilots.md) — pilots
  agent
- [`prompts/roles/playtest.md`](prompts/roles/playtest.md) —
  playtest agent
