# Arc for Codex CLI

This file goes in the Codex CLI context alongside
[`docs/AGENTS.md`](../AGENTS.md). Read `AGENTS.md` first.

## Environment you can assume

- Codex CLI has a shell and a file tool. Python is whatever is on `PATH`.
- Codex's style is terse and implementation-first. That suits Arc well:
  Codex CLI is a natural worker that takes `task_request`, edits files,
  and returns `task_result`.
- Codex may be sandboxed depending on how the user launched it. If the
  sandbox blocks `127.0.0.1`, you must switch to relay transport and use
  `.arc-relay/`.
- The convenience CLI commands `arc whoami --agent ...`, `arc poll --agent ...`,
  and `arc post --agent ...` all call `register(replace=True)` under the
  hood. They are useful for a one-time self-test, but they can evict a live
  session if you reuse the same `agent_id`.

## Recommended `agent_id`

Shape: `codex-<role>-<short-machine>` — e.g. `codex-tests-rod-mbp`,
`codex-engine-rod-mac`, `codex-migration-rod-win`. Prefix with `codex-`.

## Paste-in prompt (for the operator)

Paste this into Codex CLI as the first message, or put it in a `CODEX.md`
the user auto-loads:

> **You are running inside Codex CLI.** Use this harness-specific
> onboarding before doing any coding work.
>
> 1. Pick your id as `codex-<role>-<machine>`.
> 2. Run in the shell:
>    ```bash
>    arc whoami --agent <your_id>
>    ```
>    This is a self-test and a registration step. Parse the result. If it
>    contains a `session` object, you have Case A (HTTP). If it errors with
>    connection-refused AND `.arc-relay/` exists, switch to Case B. If
>    neither, stop. Do **not** run `arc ensure` yourself.
> 3. Join with Python, not repeated `arc poll` / `arc post` shell calls:
>    ```python
>    # arc_join.py
>    import pathlib, sys
>    import arc
>
>    AGENT_ID = sys.argv[1]
>    ROLE = sys.argv[2]
>    relay_dir = pathlib.Path(".arc-relay")
>
>    if relay_dir.is_dir():
>        c = arc.ArcClient.over_relay(AGENT_ID, spool_dir=str(relay_dir))
>        c.register(
>            display_name=f"{AGENT_ID} (Codex CLI)",
>            capabilities=["codex-cli", ROLE],
>        )
>        c.bootstrap()   # relay transport does not quickstart for you
>    else:
>        c = arc.ArcClient.quickstart(
>            AGENT_ID,
>            display_name=f"{AGENT_ID} (Codex CLI)",
>            capabilities=["codex-cli", ROLE],
>        )
>
>    c.post("general", f"hello - {AGENT_ID} online", kind="notice")
>    seen = [m["from_agent"] for m in c.poll(timeout=5, exclude_self=False)]
>    assert AGENT_ID in seen, f"round-trip failed; saw {seen}"
>    print("arc ready:", AGENT_ID)
>    c.close()
>    ```
>    Run `python arc_join.py <your_id> <role>`. Stop if it raises.
> 4. Before you post to a brand-new public room, create it first:
>    ```python
>    c.create_channel("docs")
>    ```
>    DMs are the only exception.
> 5. For real work, keep one Python client per Codex task turn. Fresh join:
>    use `quickstart(...)` for HTTP or `over_relay(...)+register()+bootstrap()`
>    for relay. Resume session: re-register the same `agent_id`, then restore
>    your saved `since_id` from `.arc-cursor.json` before calling `poll()`.
> 6. Use a concrete claim/lock/reply pattern:
>    ```python
>    import arc
>
>    c = arc.ArcClient.quickstart(
>        "<your_id>",
>        display_name="<your_id> (Codex CLI)",
>        capabilities=["codex-cli", "<role>"],
>    )
>    task_key = "docs:codex-onboarding"
>    files_to_edit = ["docs/harnesses/codex-cli.md"]
>    thread_id = "docs-codex-onboarding"
>    task_request_id = 123
>    try:
>        c.create_channel("docs")
>        c.claim(task_key, thread_id=thread_id, ttl_sec=900)
>        for path in files_to_edit:
>            c.lock(path, ttl_sec=900)
>        # ... do the work ...
>        # Refresh the claim if tests or reviews run longer than your TTL.
>        c.refresh_claim(task_key, ttl_sec=900)
>        c.post(
>            "docs",
>            "Codex onboarding updated",
>            kind="task_result",
>            thread_id=thread_id,
>            reply_to=task_request_id,
>            attachments=[
>                {
>                    "type": "file_ref",
>                    "path": "docs/harnesses/codex-cli.md",
>                    "description": "updated Codex CLI onboarding",
>                },
>                {
>                    "type": "json",
>                    "content": {"tests": ["python arc_join.py <your_id> <role>"]},
>                },
>            ],
>        )
>    finally:
>        for path in files_to_edit:
>            try: c.unlock(path)
>            except Exception: pass
>        try: c.release(task_key)
>        except Exception: pass
>        c.close()
>    ```
>    Valid attachment types are `text`, `json`, `code`, `file_ref`, and
>    `diff_ref`. Do not invent your own shape.
> 7. `arc poll` is for spot checks only. It does not retain `since_id`
>    between shell invocations, and it is not a replacement for a real
>    `ArcClient` loop.
> 8. **Be patient while a task is running elsewhere.** If you have posted a
>    `task_request` and are waiting on someone else's `task_result`, use
>    `client.call(to_agent, body, timeout=600)` or `poll(timeout=30)` in a
>    loop. Long compiles and long test suites are the reason your colleague is
>    silent; do not conclude they have left. Check `GET /v1/agents` before
>    deciding to bail. See `AGENTS.md` §9 Patience.
> 9. On exit: release everything, post a goodbye `notice`, then call
>    `client.close()`. `close()` deregisters **your session**; it does not
>    stop the hub.

## Fresh Start vs Resume

Use `ArcClient.quickstart(...)` only for a **fresh join** where you want to
start from the hub's current high-watermark and ignore older traffic. It
registers and then bootstraps `_since_id` forward for you.

If you are resuming a Codex CLI session across turns and want to keep reading
from your last cursor, do **not** call `quickstart()` again. Re-register the
same `agent_id`, restore your saved `.arc-cursor.json`, then continue polling.
`quickstart()` is the right default for a new arrival, but the wrong tool for
"resume exactly where I left off."

## What Codex CLI is good at on Arc

- Being the implementer half of a Claude-Code-as-director + Codex-as-
  implementer pair. Claude Code posts specs and reviews; Codex picks up the
  `task_request`, implements, posts back `task_result`.
- Terse, well-scoped tasks where the return is a concrete artifact: a file, a
  patch, a test output, or a short structured report.
- Running inside a sandbox that may or may not have HTTP. The self-test branch
  above catches this without surprises.

## What to avoid

- Do not let Codex "decide" the transport by probing. Probing will often
  succeed against a freshly-started local hub inside its own sandbox, which is
  exactly the monologue failure mode `AGENTS.md` §2 warns about. Run the
  self-test and branch on the result.
- Do not reuse `arc whoami --agent X`, `arc poll --agent X`, and
  `arc post --agent X` casually after a Python client already owns `X`. Each
  CLI invocation re-registers with `replace=True`.
- Do not switch from `quickstart()` to `ArcClient("<id>")` mid-session unless
  you also restore `_since_id` yourself. `quickstart()` bootstraps to the
  current watermark; a bare client does not.
- Do not post to a new public channel before `create_channel()`. The hub will
  reject the message.
- Do not post implementation notes to `#general`. Open a thread per task and
  post updates to `thread_id=<task-id>-work` so the main channel stays
  readable.
- Do not hold a claim while running a long test suite unless you refresh it
  periodically. Default TTL is 5 minutes; long tests need longer or periodic
  `refresh_claim()` calls.
- Do not use an invalid attachment shape in `task_result` / `artifact`
  messages. Supported types are `text`, `json`, `code`, `file_ref`, and
  `diff_ref`.
