# Start prompts — copy-paste bootstraps for jam agents

The four files in this folder are the **first message** you paste
into a fresh agent session at the start of a jam. One per role:

- [`engine.md`](engine.md) — for the agent that will own
  `engine.py`
- [`content.md`](content.md) — for the agent that will own
  `content.py` and the `data/*` files
- [`pilots.md`](pilots.md) — for the agent that will own
  `pilots.py` (the AI ship drivers)
- [`playtest.md`](playtest.md) — for the agent that will own
  `pilot_run.py` and `cli.py`

Each prompt is self-contained: it tells the agent its role,
lists every file it must read (including the harness-specific
file, picked from `docs/harnesses/`), and tells it what to do
once it has read everything.

## How to use them

1. Decide which four agents are in the jam and which harness
   each one is running. Four heterogeneous harnesses is fine —
   one agent on Claude Code, one on Cursor, one on Gemini CLI,
   one on Codex Desktop or Codex CLI works.
2. Start the Arc hub: `arc ensure`.
3. Open four agent sessions, one per role.
4. **For each session:** copy the matching `start/<role>.md`
   file verbatim as the first user message. The agent will read
   its role docs, join the hub, and wait for the interface
   lock.
5. Watch the `#jam` channel's `jam-interface` thread. Once all
   four agents have posted an `ack` notice, the jam is in phase
   1 and coding begins.

## Why separate start prompts at all?

The role prompts in `../roles/*.md` are the *detailed*
instructions an agent reads during onboarding — what it owns,
what it designs, what it tests, how it finishes. They assume
the agent has already read `AGENTS.md`, a harness file,
`jam-protocol.md`, and `game-brief.md`.

The start prompts in this folder are the glue that gets the
agent from zero context to "I have read all those files and I
know who I am." They are shorter, deliberately redundant about
paths, and safe to paste into any harness without editing.

If you prefer, you can inline all five files into one giant
paste yourself — but the start prompts are usually enough,
because every modern agent runtime can follow "read these files
in order" without help.
