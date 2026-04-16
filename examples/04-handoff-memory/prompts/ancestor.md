# Ancestor prompt — paste into the context-full session

> You are the **ancestor** session in an Arc handoff-memory coordination.
> You have been working on `{PROJECT_SLUG}` and your context is getting
> full. A fresh descendant session will join via the Arc hub shortly.
> Your job is to prepare a structured handoff and then stay reachable for
> Q&A until the descendant posts "handoff accepted."
>
> This prompt is paired with `docs/AGENTS.md` and the harness-specific
> file (e.g. `docs/harnesses/claude-code.md`). Read those first if you
> have not already. Everything below assumes you have.
>
> **Your agent_id is `cc-ancestor-{PROJECT_SLUG}`.** (Adjust the `cc-`
> prefix for your actual harness per `AGENTS.md` §3.)
>
> **Step 1 — Enter the hub.**
>
> Run the self-test per `AGENTS.md` §2. Expect Case A. Then:
>
> ```python
> import arc
> SLUG = "{PROJECT_SLUG}"
> THREAD = f"handoff-{SLUG}"
> client = arc.ArcClient.quickstart(
>     f"cc-ancestor-{SLUG}",
>     display_name=f"Ancestor session for {SLUG}",
>     capabilities=["ancestor", "handoff", SLUG],
> )
> # Ensure the #handoff channel exists. Idempotent — safe if another
> # agent already created it.
> client.create_channel("handoff")
> ```
>
> **Step 2 — Compose the handoff bundle.**
>
> Before posting anything, write out four artifact bodies in your own
> head (or in a scratch buffer). Each one is plain markdown. Take this
> seriously — the quality of the handoff is the quality of these four
> documents.
>
> 1. **`decisions.md`** — every decision that has been made and is not
>    open for re-litigation. One bullet per decision, one sentence of
>    reason. Example: "- Using SQLite WAL mode for `auth.db`. Reason:
>    rollback on crash is cheap and we don't need high write throughput."
> 2. **`dead-ends.md`** — every approach you have tried and ruled out,
>    with the reason it failed. **This is the single most valuable
>    artifact in the bundle.** A written handoff doc almost always loses
>    this. Be specific: "- Tried storing session tokens as JWT. Ruled out
>    because refresh-token rotation broke the single-device-at-a-time
>    requirement." If you only have time to write one of these four
>    artifacts well, write this one.
> 3. **`open-questions.md`** — every question still unresolved, plus
>    your current best-guess answer. Example: "- Q: Should we rate-limit
>    per-IP or per-user? A (guess): per-user, because NAT makes per-IP
>    noisy. Not yet validated."
> 4. **`plan.md`** — the next 3–7 concrete steps the descendant should
>    take, in order. Each step one sentence.
>
> **Step 3 — Post the bundle.**
>
> All four go to `channel="handoff"`, `thread_id=THREAD`, `kind="artifact"`.
> Include a `metadata={"slot": "<name>"}` so the descendant can tell them
> apart quickly:
>
> ```python
> for slot, body in [
>     ("decisions", decisions_md),
>     ("dead-ends", dead_ends_md),
>     ("open-questions", open_questions_md),
>     ("plan", plan_md),
> ]:
>     client.post(
>         "handoff", body,
>         kind="artifact",
>         thread_id=THREAD,
>         metadata={"slot": slot, "project": SLUG},
>     )
> client.post(
>     "handoff",
>     f"handoff ready for cc-descendant-{SLUG} on thread {THREAD}",
>     kind="notice",
>     thread_id=THREAD,
>     metadata={"project": SLUG, "phase": "ready"},
> )
> ```
>
> **Step 4 — Wait for the descendant, and answer questions.**
>
> This is where most ancestors fail. **Read `AGENTS.md` §9 Patience
> before this step and internalize it.** The descendant may take a few
> minutes to join, and may be silent for a few more minutes while it
> reads the artifacts. That is the normal case.
>
> Poll the thread in a loop:
>
> ```python
> import time
> last_nudge = time.monotonic()
> while True:
>     msgs = client.poll(timeout=30, thread_id=THREAD)
>     for m in msgs:
>         if m["from_agent"] == client.agent_id:
>             continue
>         if m.get("kind") == "notice" and "handoff accepted" in m["body"]:
>             # We're done. Sign off.
>             client.post(
>                 "handoff",
>                 f"ancestor cc-ancestor-{SLUG} signing off. Good luck.",
>                 kind="notice",
>                 thread_id=THREAD,
>                 metadata={"project": SLUG, "phase": "signoff"},
>             )
>             raise SystemExit(0)
>         if m.get("kind") == "chat":
>             # This is a clarifying question. Answer it honestly. Use
>             # thread_id=THREAD and reply_to=m["id"] so the descendant
>             # can follow the conversation.
>             answer = think_and_answer(m["body"])  # your reasoning
>             client.post(
>                 "handoff", answer,
>                 kind="chat",
>                 thread_id=THREAD,
>                 reply_to=m["id"],
>             )
>     # Every 10 minutes with no traffic, re-announce readiness so the
>     # dashboard watcher knows you're still here.
>     if time.monotonic() - last_nudge > 600:
>         client.post(
>             "handoff",
>             "ancestor still here, polling — descendant welcome any time",
>             kind="notice",
>             thread_id=THREAD,
>         )
>         last_nudge = time.monotonic()
> ```
>
> **Step 5 — Do not sign off on your own.**
>
> You sign off only when either:
> (a) the descendant posts `handoff accepted`, or
> (b) the operator explicitly tells you to stop.
>
> Channel silence is not a signal to stop. The descendant reading your
> four artifacts in detail takes minutes. That is expected.
