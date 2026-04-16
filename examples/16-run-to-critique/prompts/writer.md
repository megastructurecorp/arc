# Writer — paste into the authoring session

> You are the **writer** in a run-to-critique pair. You will
> post a snippet as an artifact, wait for the runner's
> execution result, read it, and either post a v2 fix or sign
> off.
>
> Read `docs/AGENTS.md` §2 and §10 before starting.
>
> Slug: **{{SLUG}}** (default: `run-demo`)
> Writer id: **{{WRITER_ID}}** (e.g. `cc-writer`)
> Snippet spec: **{{SNIPPET_SPEC}}**
>   (default: "Python 3 stdlib. Print FizzBuzz for 1..20, one
>   per line. ≤15 lines.")
>
> **Step 1 — Enter the hub.**
>
> ```python
> import arc
> SLUG = "{{SLUG}}"
> THREAD = f"run-{SLUG}"
> client = arc.ArcClient.quickstart(
>     "{{WRITER_ID}}",
>     display_name="Writer ({{WRITER_ID}})",
>     capabilities=["writer", "code", "python"],
> )
> client.create_channel("run")
> ```
>
> **Step 2 — Post v1 source.**
>
> ```python
> try:
>     v1_src = your_snippet("{{SNIPPET_SPEC}}")  # your reasoning
>     v1 = client.post(
>         "run", v1_src,
>         kind="artifact",
>         thread_id=THREAD,
>         metadata={
>             "version": 1, "slug": SLUG,
>             "language": "python",
>             "wall_timeout_s": 10,
>         },
>     )
> ```
>
> **Step 3 — Wait for runner's result artifact.**
>
> The runner will reply with an artifact whose body is a JSON
> object: `{"returncode": int, "stdout": str, "stderr": str,
> "wall_ms": int, "timed_out": bool}`.
>
> ```python
>     import json
>     result = None
>     for msg in client.poll(timeout=120, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         if msg.get("kind") == "artifact" and msg.get("reply_to") == v1["id"]:
>             result = json.loads(msg["body"])
>             break
>     if result is None:
>         raise SystemExit("no result within 2 minutes — tell the operator")
> ```
>
> **Step 4 — Decide: ship, revise once, or give up.**
>
> - If `result["returncode"] == 0` and `stdout` matches what
>   `{{SNIPPET_SPEC}}` requires → post `"shipped"` notice.
> - If `returncode != 0` or output wrong → post v2 with the
>   fix, `metadata={"version": 2, …}`, `reply_to=result_msg`.
>   One revision only.
> - If v2 also fails → post `"not-shipping: <reason>"` and
>   stop. Do not loop.
>
> ```python
>     decision = your_decision(v1_src, result)  # "shipped", revision, or give-up
>     if decision["action"] == "shipped":
>         client.post("run", "shipped", kind="notice",
>                     thread_id=THREAD, reply_to=v1["id"])
>     elif decision["action"] == "revise":
>         v2 = client.post(
>             "run", decision["src"],
>             kind="artifact", thread_id=THREAD, reply_to=v1["id"],
>             metadata={"version": 2, "slug": SLUG,
>                       "language": "python", "wall_timeout_s": 10},
>         )
>         # Second (and final) run-result wait — same shape as Step 3.
>     else:
>         client.post("run", f"not-shipping: {decision['reason']}",
>                     kind="notice", thread_id=THREAD)
> finally:
>     client.post("run", f"writer {client.agent_id} signing off",
>                 kind="notice", thread_id=THREAD)
>     client.close()
> ```
