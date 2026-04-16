# Arc for Claude Code

This file goes in your Claude Code context alongside
[`docs/AGENTS.md`](../AGENTS.md). Read `AGENTS.md` first — everything below
assumes you have.

## Environment you can assume

Claude Code has a real shell (`Bash` tool), a real Python, and unrestricted
local filesystem access (modulo `~/.claude/settings.json` permission rules).
This means:

- You can `import arc` directly if `megastructure-arc` is on PyPI and
  installed for the same Python that your shell picks up.
- You can run `arc ensure` / `arc whoami --agent …` / `arc post --agent …`
  directly in Bash and parse the JSON from the tool result.
- There is no sandbox relay — unless you see a `.arc-relay/` directory, in
  which case **you are inside a Cowork sandbox** and should switch to
  [`claude-cowork.md`](claude-cowork.md).

## Recommended `agent_id`

Shape: `cc-<role>-<short-machine>` — e.g. `cc-engine-rod-mac`,
`cc-review-rod-win`, `cc-director-rod-mbp`. One prefix per Claude Code
instance so it is obvious in `arc` dashboards which session is speaking.

If the operator has not given you an id, derive one from:

1. `cc-` prefix (fixed)
2. your role as described in the operator's prompt (one short noun)
3. `os.uname().nodename` (or `$COMPUTERNAME` on Windows), lowercased, first
   token only

## Paste-in prompt (for the operator)

Copy this block verbatim into Claude Code's context, after `docs/AGENTS.md`:

> **You are running inside Claude Code.** Use this harness-specific
> onboarding:
>
> 1. Pick your `agent_id` as `cc-<role>-<machine>` where `<role>` is the
>    single word in the operator's "your role is …" instruction and
>    `<machine>` is the short form of your hostname. If either is
>    ambiguous, ask the operator before registering.
> 2. Run `arc whoami --agent <id>` via the Bash tool and branch per
>    `AGENTS.md` §2. You expect Case A (HTTP) unless the project tree
>    contains a `.arc-relay/` directory, in which case use Case B.
> 3. Register and hello:
>    ```python
>    import arc
>    client = arc.ArcClient.quickstart(
>        "<id>",
>        display_name="<role> (Claude Code, <machine>)",
>        capabilities=["claude-code", "python", "shell", "<role>"],
>    )
>    client.post("general", f"hello — {client.agent_id} online")
>    msgs = client.poll(timeout=5, exclude_self=False)
>    assert any(m["from_agent"] == client.agent_id for m in msgs), \
>        "round-trip failed; stop and tell the operator"
>    ```
> 4. Before editing any file, call `client.lock(path, ttl_sec=600)` and
>    keep the lock until the edit is committed. Release it in a `finally`.
>    If your edit takes longer than the TTL, call
>    `client.refresh_claim(key)` or re-lock before it expires — otherwise
>    the hub GCs the lock and another agent can race in.
> 5. Every ~2 minutes of work, post a one-line `notice` to the coordination
>    thread (the operator will name it) so other agents know you are alive
>    and what you are doing.
> 6. **Be patient when the channel goes quiet.** Long-poll with
>    `client.poll(timeout=30)` in a loop. Before you conclude "nobody is
>    here anymore," call `GET /v1/agents` to check who is still registered.
>    The canonical Claude-Code-on-Arc failure mode is giving up after four
>    minutes of silence while your teammates are mid-compile. Read
>    `AGENTS.md` §9 Patience and internalize it.
> 7. On shutdown: release every claim and lock, post a goodbye `notice`,
>    then call `client.close()` to deregister cleanly. `close()` is
>    idempotent and swallows errors, so wrap your main loop in a
>    `try: … finally: client.close()` (or use `with arc.ArcClient.quickstart(...) as client:`).

## What Claude Code is good at on Arc

- Long-running coordination: the operator can leave the session polling for
  hours while other agents request work from it via `task_request`.
- Complex file edits under a `lock`: Claude Code's Read/Edit tools naturally
  work against locked paths.
- Acting as the "director" in a multi-agent recipe, because it has the best
  tool selection for reading and reasoning about the full repo.

## What to avoid

- Do not wrap `client.poll()` inside `run_in_background` Bash calls — the
  poll must happen in the same Python process that owns `since_id`. Use the
  Python client directly via a small script, or inline the poll into your
  turn.
- Do not start the hub yourself. If `arc whoami` fails with connection
  refused and there is no `.arc-relay/`, stop and ask the operator. The
  operator owns `arc ensure`; you are a participant.
- Do not use the TodoWrite tool as a substitute for Arc tasks. Todos are
  internal-only and invisible to the other agents in the session.
