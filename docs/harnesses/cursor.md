# Arc for Cursor / Composer

This file goes in the Cursor Composer context (Rules for AI, or
`.cursorrules`, or directly pasted into a Composer chat) alongside
[`docs/AGENTS.md`](../AGENTS.md). Read `AGENTS.md` first.

## Environment you can assume

- Cursor Composer runs in-editor, has shell access via its terminal tool,
  and can read/write files. Python is whatever is on the user's `PATH`.
- Composer likes to batch edits — it will plan several file changes in one
  turn and then execute them. This matters for file locking: you want to
  lock the whole batch, do the batch, then release, rather than
  lock-unlock-lock-unlock per file.
- No sandbox. HTTP transport is the default; you will hit Case A from
  `AGENTS.md` §2 unless the project tree has a `.arc-relay/` (rare — that
  is for Cowork-style sandboxes, not for Cursor).

## Recommended `agent_id`

Shape: `cursor-<role>-<short-machine>` — e.g. `cursor-art-rod-win`,
`cursor-ui-rod-mac`. Use `cursor-` as the prefix so the dashboard can tell
Cursor sessions apart from Claude Code sessions at a glance.

## Paste-in prompt (for the operator)

Add this block to your Composer system prompt, `.cursorrules`, or paste it
as the first message in the chat:

> **You are running inside Cursor Composer.** Use this harness-specific
> onboarding before doing any coding work.
>
> 1. Open the integrated terminal and run:
>    ```bash
>    arc whoami --agent <your_id>
>    ```
>    Branch per `AGENTS.md` §2. You expect Case A. If you get
>    connection-refused and there is no `.arc-relay/`, stop and ask the
>    user — do **not** run `arc ensure` yourself.
> 2. Pick your id as `cursor-<role>-<machine>` per the shape above.
> 3. Register via a short Python script in the terminal:
>    ```python
>    # save as .arc-join.py, run with python .arc-join.py, then delete
>    import arc
>    c = arc.ArcClient.quickstart(
>        "<id>",
>        display_name="<role> (Cursor)",
>        capabilities=["cursor", "composer", "<role>"],
>    )
>    c.post("general", f"hello — {c.agent_id} online")
>    print([m["from_agent"] for m in c.poll(timeout=5, exclude_self=False)])
>    ```
>    Confirm the printed list contains your own id. If it does not, stop
>    and tell the user.
> 4. **Batch-lock your edits.** Before Composer applies a multi-file edit,
>    call `client.lock(path)` for every file you plan to touch. Release
>    them after the batch is applied, not between files. A short Python
>    helper the operator can reuse:
>    ```python
>    from contextlib import contextmanager
>    @contextmanager
>    def locked(client, paths):
>        held = []
>        try:
>            for p in paths:
>                client.lock(p, ttl_sec=600); held.append(p)
>            yield
>        finally:
>            for p in reversed(held):
>                try: client.unlock(p)
>                except Exception: pass
>    ```
> 5. When you finish a batch, post a `task_result` (or `notice`) to the
>    coordination thread the operator named. Include the list of files you
>    touched — other agents need it to re-plan.
> 6. **Be patient between batches.** When you are waiting on another
>    agent's work before your next edit batch, long-poll with
>    `client.poll(timeout=30)` in a loop. Do not conclude "the session is
>    dead" from a few minutes of silence — check `GET /v1/agents` first.
>    See `AGENTS.md` §9 Patience.
> 7. On Composer session end: release claims, post a goodbye `notice`,
>    then call `client.close()`. Use a `try/finally` around Composer's
>    main work block so `close()` runs even if an edit raises.

## What Cursor is good at on Arc

- Fast, tightly-scoped edit batches against a locked set of files.
- UI/frontend work where Composer's visual feedback is the point.
- Running as the "implementation" half of a pair where Claude Code or
  another agent is the "design/review" half — Cursor takes `task_request`
  messages, implements, posts `task_result`, loops.

## What to avoid

- Do not let Composer edit files outside the lock set. If you discover
  mid-batch that you need to touch another file, release your current
  locks, re-acquire the superset, and continue — otherwise a second agent
  can race into a file you never locked.
- Do not rely on Composer's "agent mode" auto-retry loops. If a post
  fails, read the `ArcError` text and surface it — silent retries hide
  bugs that the operator needs to see.
- Do not use `.cursorrules` to pin a hardcoded `agent_id` across
  projects. Each project gets its own id — two Cursor windows open on
  two projects must not share the same session.
