# Game Jam Template — adapting the recipe to your own game

The four-role split in this example is deliberately generic. It
works for most small turn-based or single-player games. The
parts that change when you swap out Arc Jam Starlane for your
own idea are concentrated in a small number of files;
everything else can stay.

## What stays the same

- The `jam-protocol.md` shared prompt — Arc coordination
  pattern, channel/thread names, lock discipline, milestones,
  patience rules. You may want to rename `#jam` to something
  project-specific (`#dungeon-jam`, `#tricktaker`, etc.) but
  the mechanics are the same.
- The `README.md` structural advice — make/design/test for
  every agent, feedback via playtest reports not chat, scope
  tight, file-level division.
- The role structure — engine / content / pilots / playtest is
  a surprisingly general split. More on that below.

## What you rewrite

### 1. `prompts/shared/game-brief.md`

This is the biggest single edit and the one you must not
shortcut. A vague brief is what stalls a real jam. The brief
must contain:

- **One-paragraph pitch** — what the game is, who plays it,
  why it's interesting
- **Player count, session length, win/end condition** — how
  many players (or one), how long a session lasts (turn count
  or wall-clock), and how it ends. Short is better; your
  first worked version should be finishable inside one jam
  session.
- **Complete rule set** — every rule, including the ones you
  think are obvious. Agents will argue over anything you leave
  implicit.
- **Every edge case answered up front** — "what if fuel runs
  out mid-jump," "can X stack with Y," "who wins a tie." List
  them even if the answers seem mechanical. Each unanswered
  question is five minutes of coordination waste.
- **Determinism requirements** — state explicitly what must be
  reproducible under a seed. For Arc Jam Starlane it's
  everything; for other genres it might be only the
  procedurally-generated content. Pin this in the brief.
- **The interface contract** — exact function and class
  signatures that every agent's module must satisfy, with type
  hints. This is the alignment document. Agents patch *their*
  side of the contract in parallel without breaking each other.

The Arc Jam Starlane version in `prompts/shared/game-brief.md`
is a template for shape and rigor. Copy it, replace the
content, keep the shape.

If your game has **data-driven content** (most do — name
tables, monster stats, room definitions, loot tables, economy
modifiers), make sure the brief documents:

- Where the data files live (e.g. `data/commodities.toml`,
  `monsters/bestiary.toml`, `rooms/*.md`)
- Which fields are required and which are optional
- How the content agent's generator transforms the data (if
  at all)
- Which agent owns the data files — by default this is the
  content agent, and other agents must not edit the files
  directly

### 2. `prompts/roles/*.md`

Each role prompt has three sections that change:

- **"What you own"** — the module path and its
  responsibilities in terms of *your* game
- **"What you test"** — the assertions that are meaningful for
  your game (rule invariants for the engine, determinism and
  distribution checks for content, behavior sanity for the AI
  players, balance metrics for playtest)
- **"Interface you implement"** — the function/class
  signatures you promised in `game-brief.md`

The "how you join the hub" section and the "Arc coordination"
section at the bottom stay identical across games. Don't
rewrite them unless you have a real reason.

### 3. `prompts/start/*.md`

The start prompts mention the worked example name in their
first paragraph. Update the game name and the one-line
pitch in each of the four start prompts to match your
game.

## What you might adjust

### The four roles — when to swap one out

The engine / content / pilots / playtest split is not sacred,
but it is a good default. Here are the cases where you'd
deviate:

- **The game has no AI players to write.** (Text adventures,
  worldbuilding games where playtest means a human actually
  plays.) Replace "pilots" with a **UX/parser agent** who owns
  the input handling and the player-visible surface. Replace
  "playtest" with a **scenario agent** who owns a scripted
  deterministic playthrough that walks the game from start to
  end.
- **The game has heavy content authoring.** (Roguelike with
  200 monsters, text adventure with 40 rooms.) Split "content"
  into **content-data** (pure data, no code) and
  **content-code** (item effects, room handlers). You then
  have five agents, not four, but each one still makes +
  designs + tests.
- **The game is competitive multiplayer.** Rename "pilots" to
  "strategies" and have the playtest agent run a round-robin
  tournament instead of a seeded benchmark. This is the shape
  the earlier deckbuilder/card-game versions of this example
  used — it works fine for anything with pairings.
- **The game has a distinct AI opponent layer.** (Chess-like,
  hand management with memory.) Keep the four roles but make
  the pilots/strategies agent's surface larger — they own
  both the dumb baseline and the real AI.

Do not split one role into two unless the resulting modules
each have real work for a whole agent. Two agents with too
little to do is worse than one agent with too much.

### Module file layout

The worked example uses four single-file modules plus a small
data directory:

```
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

For a larger game, each agent may own a small package instead:

```
engine/
    __init__.py
    state.py
    rules.py
    actions.py
    tests/
content/
    __init__.py
    generator.py
    market.py
    tables/
    tests/
...
```

Either works. What matters is that **the top-level directory
names match the role names**, so `client.lock(path)` can use
directory prefixes as the natural lock boundary. If the engine
agent wants to edit anywhere under `engine/`, they lock
`engine/` once and release when done.

### The interface contract location

In the worked example, the interface contract lives inside
`prompts/shared/game-brief.md` because it's a template. In a
real jam, it should move to a shared file in the project repo
— typically `CONTRACT.md` at the project root — as soon as
the agents have locked on it. That way future edits to the
contract are tracked in git, not in chat scrollback.

Post the contract file to `jam-interface` as a `kind=artifact`
message any time it changes, so agents that were AFK see the
new version.

### Playtest metrics

The Arc Jam Starlane playtest report tracks per-pilot average
credits, systems visited, turns played, stranding rate, and
crash rate. These are the minimum for a single-player economy
game. For other genres:

- **Competitive game (deckbuilder, card game, board game):**
  win matrix per strategy pairing, average game length,
  resource-acquisition curves, "dominant opening" detection
- **Combat game:** win rate per strategy pairing, average round
  length, damage distribution
- **Roguelike:** runs-to-win, average depth reached,
  death-cause histogram
- **Exploration game:** map coverage, time-to-completion,
  dead-end frequency
- **Puzzle game:** solve rate per seed, move count vs optimal,
  unsolvable-seed detection

The playtest agent owns the metrics list. They should post a
draft of what they plan to measure to `jam-interface` during
phase 0 — it is part of the contract. If everyone agrees
"this is what balance means," the balance conversation later
is short and data-driven. If everyone discovers the metrics
during phase 3, the balance conversation is ten rounds of "I
don't like this" and nothing ships.

## Minimal quick-start checklist

1. [ ] Copy `examples/01-game-jam/` to your project as
       `jam-<your-game>/`.
2. [ ] Rewrite `prompts/shared/game-brief.md` — pitch, rules,
       edge cases, determinism requirements, interface
       contract.
3. [ ] In each `prompts/roles/*.md`, edit the "What you own /
       design / test" sections to match your game. Rename the
       pilots role if your game is competitive (use
       "strategies") or driven by a human (use "ux" +
       "scenario").
4. [ ] Decide your playtest metrics and add them to
       `prompts/roles/playtest.md`.
5. [ ] If your game has data-driven content, edit
       `prompts/roles/content.md` to list the data files the
       content agent owns and their format.
6. [ ] Update the one-paragraph game pitch in each of the four
       `prompts/start/*.md` start prompts.
7. [ ] Start the hub (`arc ensure`).
8. [ ] Open four agent sessions. Paste the matching start
       prompt from `prompts/start/` into each one — these
       bootstrap the agent through `docs/AGENTS.md`, the
       harness file, `jam-protocol.md`, `game-brief.md`, and
       the role prompt in one go.
9. [ ] Watch `jam-interface` for the interface-lock acks. Do
       not start coding until all four are in.
