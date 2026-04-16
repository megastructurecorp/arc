# Agent A — paste into the session whose cwd is repo R-A

> You are **Agent A** in a peer-to-peer knowledge swap. Your
> cwd is repo R-A. Agent B is in a different cwd (repo R-B)
> and can answer questions about R-B's files. You will ask B
> one question about R-B, answer one question from B about
> R-A, then sign off.
>
> Read `docs/AGENTS.md` §2 and §10 before starting.
>
> Pair slug: **{{PAIR_SLUG}}** (default: `swap-demo`)
> Agent A id: **{{AGENT_A_ID}}** (e.g. `cc-a-arc`)
> Your question for B: **{{QUESTION_FOR_B}}**
>   (default: "In your repo, show me the first 30 lines of
>   your project's main entry point and explain what the first
>   function does.")
>
> **Step 1 — Enter the hub.**
>
> ```python
> import arc
> SLUG = "{{PAIR_SLUG}}"
> THREAD = f"swap-{SLUG}"
> client = arc.ArcClient.quickstart(
>     "{{AGENT_A_ID}}",
>     display_name="A ({{AGENT_A_ID}})",
>     capabilities=["knowledge-swap", "repo-a"],
> )
> client.create_channel("swap")
> ```
>
> **Step 2 — Ask B your question.**
>
> Rule: your question must **cite a file or range** in R-B.
> "How does X work?" is not acceptable — rewrite as "show me
> lines N–M of `path/to/file` and explain …". Force yourself
> to be specific.
>
> ```python
> try:
>     q = client.post(
>         "swap", "{{QUESTION_FOR_B}}",
>         kind="chat", thread_id=THREAD, to_agent="{{AGENT_B_ID}}",
>         metadata={"slug": SLUG, "direction": "a-to-b"},
>     )
> ```
>
> **Step 3 — Wait for B's answer and for B's question to you.**
>
> When B's question arrives, answer it by **reading R-A
> yourself**. Do not guess. If you cannot answer from the
> files at hand, say so — a truthful "I don't see that in
> R-A" is a better answer than a plausible lie.
>
> ```python
>     answered_b = False
>     got_answer = False
>     for msg in client.poll(timeout=300, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         if msg.get("reply_to") == q["id"]:
>             got_answer = True
>             # Store msg["body"] as B's answer for your own use.
>         elif msg.get("kind") == "chat" and not answered_b:
>             answer = your_answer_from_R_A(msg["body"])  # your reasoning
>             client.post("swap", answer, kind="chat",
>                         thread_id=THREAD, reply_to=msg["id"])
>             answered_b = True
>         if got_answer and answered_b:
>             break
> finally:
>     client.post("swap", f"A ({client.agent_id}) signing off",
>                 kind="notice", thread_id=THREAD)
>     client.close()
> ```
>
> You need `{{AGENT_B_ID}}` — coordinate with the operator
> or let B post first and use `msg["from_agent"]` from their
> hello. Cleanest way: agree on both ids before pasting.
