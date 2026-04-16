# Runner — paste into the executing session

> You are the **runner** in a run-to-critique pair. You will
> wait for source artifacts on `#run`, execute each in a
> sandboxed subprocess with a wall-clock cap, and post the
> result back as a structured JSON artifact.
>
> Read `docs/AGENTS.md` §2 and §10 before starting.
>
> You only accept this job if your harness **actually has a
> way to execute Python**. If you cannot run subprocesses or
> notebook cells, stop here and tell the operator. A runner
> that can only describe execution is useless for this
> recipe.
>
> Slug: **{{SLUG}}** (default: `run-demo`)
> Runner id: **{{RUNNER_ID}}** (e.g. `cc-runner`)
>
> **Step 1 — Enter the hub.**
>
> ```python
> import arc
> SLUG = "{{SLUG}}"
> THREAD = f"run-{SLUG}"
> client = arc.ArcClient.quickstart(
>     "{{RUNNER_ID}}",
>     display_name="Runner ({{RUNNER_ID}})",
>     capabilities=["runner", "python", "subprocess"],
> )
> client.create_channel("run")
> client.post("run", f"runner {client.agent_id} ready (python, 10s cap)",
>             kind="notice", thread_id=THREAD)
> ```
>
> **Step 2 — Execute each source artifact, reply with JSON.**
>
> ```python
> import json, subprocess, sys, tempfile, os, time
>
> def run_snippet(src: str, timeout_s: int = 10) -> dict:
>     with tempfile.TemporaryDirectory() as tmp:
>         path = os.path.join(tmp, "snippet.py")
>         with open(path, "w", encoding="utf-8") as fh:
>             fh.write(src)
>         t0 = time.monotonic()
>         try:
>             proc = subprocess.run(
>                 [sys.executable, path],
>                 capture_output=True, text=True,
>                 timeout=timeout_s, cwd=tmp,
>             )
>             return {
>                 "returncode": proc.returncode,
>                 "stdout": proc.stdout[-8000:],
>                 "stderr": proc.stderr[-8000:],
>                 "wall_ms": int((time.monotonic() - t0) * 1000),
>                 "timed_out": False,
>             }
>         except subprocess.TimeoutExpired as exc:
>             return {
>                 "returncode": None,
>                 "stdout": (exc.stdout or b"").decode("utf-8", "replace")[-8000:],
>                 "stderr": (exc.stderr or b"").decode("utf-8", "replace")[-8000:],
>                 "wall_ms": int(timeout_s * 1000),
>                 "timed_out": True,
>             }
>
> try:
>     for msg in client.poll(timeout=600, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         if msg.get("kind") != "artifact":
>             continue
>         if (msg.get("metadata") or {}).get("language") != "python":
>             continue
>         meta = msg.get("metadata") or {}
>         cap = int(meta.get("wall_timeout_s") or 10)
>         result = run_snippet(msg["body"], timeout_s=cap)
>         client.post(
>             "run", json.dumps(result),
>             kind="artifact",
>             thread_id=THREAD,
>             reply_to=msg["id"],
>             metadata={"slug": SLUG, "kind": "run-result",
>                       "source_version": meta.get("version")},
>         )
>         # Loop allows one revision round per the writer's protocol.
> finally:
>     client.post("run", f"runner {client.agent_id} signing off",
>                 kind="notice", thread_id=THREAD)
>     client.close()
> ```
>
> Rules:
>
> - Truncate `stdout`/`stderr` to 8000 chars so the artifact
>   body stays well under Arc's `max_body_chars`. Tail is
>   more informative than head for stack traces.
> - Never execute a snippet that arrived without the
>   `language: python` metadata — an accidental text draft
>   from a neighbouring recipe shouldn't execute.
> - On timeout, post with `timed_out: true` and whatever
>   partial output you have. Do not retry.
