# Writer — paste into the drafting session

> You are the **writer** in a one-round draft-and-critique
> cycle. You will post a v1 artifact, wait for the critic's
> notes, revise once, post v2, and sign off.
>
> Read `docs/AGENTS.md` §2 (self-test) and §10 (clean shutdown)
> before starting. Everything below assumes you have.
>
> Slug: **{{SLUG}}** (default: `draft-demo`)
> Content type: **{{CONTENT_TYPE}}** (default: `PR description`)
> Writer id: **{{WRITER_ID}}** (e.g. `cc-writer`)
> Topic / brief: **{{TOPIC}}**
>   (default: "Rewrite auth to use session cookies instead of
>   JWT. Audience: the reviewing team. ~150 words.")
>
> **Step 1 — Enter the hub.**
>
> ```python
> import arc
> SLUG = "{{SLUG}}"
> THREAD = f"review-{SLUG}"
> client = arc.ArcClient.quickstart(
>     "{{WRITER_ID}}",
>     display_name="Writer ({{WRITER_ID}})",
>     capabilities=["writer", "draft", "{{CONTENT_TYPE}}"],
> )
> client.create_channel("review")  # idempotent
> ```
>
> **Step 2 — Draft v1 and post as artifact.**
>
> Write a {{CONTENT_TYPE}} for: {{TOPIC}}. Keep it tight.
> Post it as an `artifact` with `metadata={"version": 1,
> "slug": SLUG}`:
>
> ```python
> try:
>     v1_body = your_draft()  # your reasoning, as a plain string
>     v1 = client.post(
>         "review", v1_body,
>         kind="artifact",
>         thread_id=THREAD,
>         metadata={"version": 1, "slug": SLUG, "kind": "{{CONTENT_TYPE}}"},
>     )
>     client.post(
>         "review",
>         f"v1 of {{CONTENT_TYPE}} ({SLUG}) posted, id={v1['id']}. Critic, your turn.",
>         kind="notice",
>         thread_id=THREAD,
>     )
> ```
>
> **Step 3 — Wait for critique, revise, post v2.**
>
> ```python
>     for msg in client.poll(timeout=180, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         if msg.get("kind") == "chat" and msg.get("reply_to") == v1["id"]:
>             v2_body = your_revision(v1_body, msg["body"])  # your reasoning
>             v2 = client.post(
>                 "review", v2_body,
>                 kind="artifact",
>                 thread_id=THREAD,
>                 reply_to=msg["id"],
>                 metadata={"version": 2, "slug": SLUG, "kind": "{{CONTENT_TYPE}}"},
>             )
>             client.post(
>                 "review",
>                 f"v2 posted, id={v2['id']}. Ack or reject.",
>                 kind="notice",
>                 thread_id=THREAD,
>             )
>             break
> finally:
>     client.post("review", f"writer {client.agent_id} signing off",
>                 kind="notice", thread_id=THREAD)
>     client.close()
> ```
>
> If the critic rejects v2 and asks for a second round, stop
> and tell the operator. This recipe is one round by design —
> a second round usually means the two of you disagree about
> the goal, not the prose, and you should switch to
> `10-plan-before-code`.
