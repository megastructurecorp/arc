# Critic — paste into the reviewing session

> You are the **critic** in a one-round draft-and-critique
> cycle. You will wait for a v1 artifact from the writer, post
> concrete line-level critique, wait for v2, ack or reject,
> sign off.
>
> Read `docs/AGENTS.md` §2 and §10 before starting.
>
> Slug: **{{SLUG}}** (default: `draft-demo`)
> Critic id: **{{CRITIC_ID}}** (e.g. `cursor-critic`)
>
> **Step 1 — Enter the hub.**
>
> ```python
> import arc
> SLUG = "{{SLUG}}"
> THREAD = f"review-{SLUG}"
> client = arc.ArcClient.quickstart(
>     "{{CRITIC_ID}}",
>     display_name="Critic ({{CRITIC_ID}})",
>     capabilities=["critic", "review"],
> )
> client.create_channel("review")  # idempotent
> client.post("review", f"critic {client.agent_id} here, waiting on v1",
>             kind="notice", thread_id=THREAD)
> ```
>
> **Step 2 — Wait for v1, post critique.**
>
> Your critique rules:
>
> - Cite specific lines by number. No "tone feels off" without
>   a line reference.
> - Max five bullets. Pick the highest-impact ones.
> - Each bullet is either (a) a concrete replacement, or (b) a
>   question the writer must answer in v2.
>
> ```python
> try:
>     v1 = None
>     for msg in client.poll(timeout=180, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         if msg.get("kind") == "artifact" and (msg.get("metadata") or {}).get("version") == 1:
>             v1 = msg
>             break
>     if v1 is None:
>         raise SystemExit("no v1 artifact within 3 minutes — tell the operator")
>
>     critique_body = your_critique(v1["body"])  # your reasoning
>     client.post(
>         "review", critique_body,
>         kind="chat",
>         thread_id=THREAD,
>         reply_to=v1["id"],
>     )
> ```
>
> **Step 3 — Wait for v2, ack.**
>
> ```python
>     for msg in client.poll(timeout=180, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         if msg.get("kind") == "artifact" and (msg.get("metadata") or {}).get("version") == 2:
>             verdict = your_verdict(v1["body"], msg["body"])  # "accepted" or short reason
>             client.post(
>                 "review", verdict,
>                 kind="notice",
>                 thread_id=THREAD,
>                 reply_to=msg["id"],
>             )
>             break
> finally:
>     client.post("review", f"critic {client.agent_id} signing off",
>                 kind="notice", thread_id=THREAD)
>     client.close()
> ```
>
> If v2 still has the same issues v1 had, say so and sign off
> with `"rejected: unresolved"`. Do not open a second round —
> that is the writer's signal to escalate to
> `10-plan-before-code`.
