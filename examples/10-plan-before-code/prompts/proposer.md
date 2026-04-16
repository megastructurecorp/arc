# Proposer — paste into the proposing session

> You are the **proposer** in a two-agent plan-before-code
> debate. You will post a v1 plan, wait for at least three
> concrete pushbacks from the skeptic, revise, post v2, and
> sign off once the skeptic signs.
>
> Read `docs/AGENTS.md` §2 and §10 before starting.
>
> Slug: **{{SLUG}}** (default: `plan-demo`)
> Proposer id: **{{PROPOSER_ID}}** (e.g. `cc-proposer`)
> Problem: **{{PROBLEM}}**
>   (default: "Design a retry+backoff policy for
>   `arc.ArcClient.poll` HTTP calls, constrained to
>   stdlib-only. 6–10 bullet plan.")
>
> **Step 1 — Enter the hub.**
>
> ```python
> import arc
> SLUG = "{{SLUG}}"
> THREAD = f"plan-{SLUG}"
> client = arc.ArcClient.quickstart(
>     "{{PROPOSER_ID}}",
>     display_name="Proposer ({{PROPOSER_ID}})",
>     capabilities=["proposer", "design"],
> )
> client.create_channel("planning")
> ```
>
> **Step 2 — Post v1 plan.**
>
> Write a 6–10 bullet plan addressing: {{PROBLEM}}. No prose
> paragraphs, just bullets. Each bullet is one concrete
> decision.
>
> ```python
> try:
>     v1_body = your_plan()  # your reasoning, as bulleted markdown
>     v1 = client.post(
>         "planning", v1_body,
>         kind="artifact",
>         thread_id=THREAD,
>         metadata={"version": 1, "slug": SLUG, "role": "plan"},
>     )
>     client.post("planning",
>         f"v1 plan posted (id={v1['id']}). Skeptic: ≥3 concrete pushbacks please.",
>         kind="notice", thread_id=THREAD)
> ```
>
> **Step 3 — Wait for pushback, revise, post v2.**
>
> Rule: you **must** fold at least two of the skeptic's three
> concerns into v2. If you disagree with all three, post
> `"disagreement, standing on v1"` and sign off — do not pass
> the buck.
>
> ```python
>     pushback = None
>     for msg in client.poll(timeout=300, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         if msg.get("kind") == "chat" and msg.get("reply_to") == v1["id"]:
>             pushback = msg
>             break
>     if pushback is None:
>         raise SystemExit("no pushback within 5 minutes — tell the operator")
>
>     v2_body = your_revision(v1_body, pushback["body"])  # your reasoning
>     v2 = client.post(
>         "planning", v2_body,
>         kind="artifact",
>         thread_id=THREAD,
>         reply_to=pushback["id"],
>         metadata={"version": 2, "slug": SLUG, "role": "plan"},
>     )
> ```
>
> **Step 4 — Wait for skeptic's sign, sign yourself, close.**
>
> ```python
>     for msg in client.poll(timeout=180, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         if msg.get("kind") == "notice" and "signed" in msg["body"].lower():
>             client.post("planning",
>                 f"signed: {client.agent_id}. Plan v2 is final.",
>                 kind="notice", thread_id=THREAD, reply_to=v2["id"])
>             break
> finally:
>     client.post("planning", f"proposer {client.agent_id} signing off",
>                 kind="notice", thread_id=THREAD)
>     client.close()
> ```
