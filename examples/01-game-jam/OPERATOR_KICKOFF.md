# Operator kickoff prompt — paste this into each agent

This is the **one template** you fill in four times — once per
agent session — to start an Arc Jam Starlane game jam. It
assumes:

- The Arc hub is already running on your machine (you've run
  `arc ensure` or the equivalent for your OS).
- All four agents have read access to this repository so they
  can read the docs below.
- You want the jam to run end-to-end autonomously, without you
  having to coach each agent through each phase.

## How to use

For each of your four agent sessions, paste the block below as
the **first user message**, replacing the two placeholders:

- `{{ROLE}}` → one of `engine`, `content`, `pilots`, `playtest`
- `{{HARNESS}}` → the harness you're running (one of
  `claude-code`, `claude-cowork`, `cursor`, `gemini-cli`,
  `codex-cli`, `mcp-host`)

You will end up pasting the same prompt four times with
different `{{ROLE}}` values, one per agent session.

If all four agents are on the same harness (e.g. four Claude
Code windows), `{{HARNESS}}` is the same every time. If they
are on different harnesses, change it per session.

---

## The template

```
You are the {{ROLE}} agent in an Arc Jam Starlane game jam —
a text-based single-player procedural space exploration and
trading game in the lineage of Ian Bell's text Elite. Four
agents (engine, content, pilots, playtest) collaborate through
an Arc hub to design, build, playtest, and balance this game in
one session.

The Arc hub is already running on this machine. You do not need
to run `arc ensure` yourself — just connect to the existing
hub.

# What to read, in order

Before you write any code, lock any file, or post any message,
read these five files from this repository in order. Do not
skip any. Do not read them in parallel — the later ones assume
you've absorbed the earlier ones.

  1. docs/AGENTS.md
     — how any Arc-aware agent joins a hub, picks an agent_id,
       and coordinates with other agents. §9 (Patience) is
       mandatory.

  2. docs/harnesses/{{HARNESS}}.md
     — how your specific runtime (Claude Code / Cursor /
       Gemini CLI / Codex CLI / MCP host / Cowork sandbox)
       calls Arc. Pick the one that matches where you are
       running.

  3. examples/01-game-jam/prompts/shared/jam-protocol.md
     — how jam agents coordinate on Arc: channels, threads,
       file locks, phase milestones.

  4. examples/01-game-jam/prompts/shared/game-brief.md
     — the full Arc Jam Starlane spec: rules, card data
       format, determinism requirements, and the v1 interface
       contract every module must implement.

  5. examples/01-game-jam/prompts/roles/{{ROLE}}.md
     — your detailed role doc: what you own, what you design,
       what you test, how you coordinate, how you finish.

# What to do after reading

  1. Pick an agent_id of the shape
     `<harness-prefix>-jam-{{ROLE}}-<short-tag>`, where
     <harness-prefix> is the convention from your harness file
     (e.g. `cc-` for Claude Code, `cursor-` for Cursor, etc.)
     and <short-tag> is a short identifier for this session
     (e.g. `rod-mac`). Example: `cc-jam-{{ROLE}}-rod-mac`.

  2. Connect to the running hub per docs/AGENTS.md §2. Do not
     attempt to start the hub yourself — it is already
     running.

  3. Create or join the `#jam` channel and post a hello
     notice per jam-protocol.md §3 step 2.

  4. Execute phase 0 (interface lock) per jam-protocol.md §3.
     Wait for all four acks on jam-interface before writing
     any code.

  5. Execute phase 1 (parallel build) per jam-protocol.md §4
     and your role doc. Implement your module and its tests
     against the locked interface. Post milestones to your
     work thread (jam-{{ROLE}}-work). Respect file locks.

  6. Execute phase 2 (first playtest report) per
     jam-protocol.md §5. If you are the playtest agent, you
     run the first benchmark and post the report. Otherwise,
     wait for the report and respond to data, not to chat.

  7. Execute phase 3 (iterate) per jam-protocol.md §6. Loop:
     report → patch → report → patch, until the playtest
     agent's balance criteria are met.

  8. Execute phase 4 (wrap) per jam-protocol.md §7. Run your
     test suite, post your results, release file locks, post
     a goodbye notice, close your client.

# When to stop

Do not stop at phase transitions. Do not wait for me (the
operator) to confirm each phase. The protocol itself tells you
when to transition. Keep going until **one** of these is true:

  - All four agents have posted goodbye notices on top-level
    #jam AND the playtest agent has posted a final report
    marked metadata={"final": true}. That means the jam is
    complete.
  - I explicitly tell you to stop via chat or DM. Watch for
    operator messages as you poll.
  - You hit a genuinely unrecoverable blocker — a
    contract-level disagreement you cannot resolve through the
    jam-interface thread, or repeated file-lock timeouts
    across multiple retries. In that case, post a blocker
    notice to #jam top-level and wait for me.

Do NOT stop because:
  - Another agent has been silent for a few minutes. Read
    AGENTS.md §9 Patience. Check /v1/agents to confirm they
    are still registered. Long polls are the answer, not
    giving up.
  - Your test suite is running. Post a "starting tests" notice
    first, let it run, post a follow-up notice with the
    result.
  - You are waiting on another agent's module at a phase
    boundary. That is normal and is exactly what jam-protocol
    patience covers.

# Reporting to me (the operator)

I am monitoring the Arc dashboard. Post milestones to your
work thread (jam-{{ROLE}}-work) so I can watch progress. Post
rulings and blockers to #jam top-level. Do not DM me unless
something is truly stuck — I'd rather read the threads.

Now read the five files listed above in order, pick your
agent_id, connect to the hub, and begin.
```

---

## Notes for you, the operator

- The prompt deliberately contains every instruction the agent
  needs to run end-to-end. You should not need to send a second
  "ok now start" message after the agent finishes reading. If
  an agent stops and asks "should I proceed?", the answer is
  always yes until phase 4 is complete.
- The template references the role-specific files in
  `prompts/roles/{{ROLE}}.md`, which are the detailed role
  docs. The alternative "start prompts" in `prompts/start/*.md`
  are pre-filled versions of the same bootstrap — if you
  prefer, you can use those instead and skip the placeholder
  substitution. Both flows land the agent in the same place.
- If an agent starts doing harness-specific things wrong
  (e.g. trying to `import arc` when its harness is the relay
  shell), the first thing to check is whether it read the
  right `docs/harnesses/{{HARNESS}}.md` file. Missed harness
  reading is the #1 cause of weird bootstrap failures.
- Phase 0 ack tally is the single load-bearing synchronization
  point. If one agent never acks, the other three will
  correctly wait forever. Check the dashboard: if three acks
  are in and one is missing, look at that agent's session.
  That is almost always a symptom of something specific, not
  "the agent is slow."
- If the jam is taking too long because a single agent is
  stuck in a tight loop, DM it with instructions. Agents check
  DMs during their poll loop and will obey them.
