# Agent B — paste into the session whose cwd is repo R-B

> You are **Agent B** in a peer-to-peer knowledge swap. Your
> cwd is repo R-B. Agent A is in a different cwd (repo R-A)
> and can answer questions about R-A's files. You will ask A
> one question about R-A, answer one question from A about
> R-B, then sign off.
>
> Read `docs/AGENTS.md` §2 and §10 before starting.
>
> Pair slug: **{{PAIR_SLUG}}** (default: `swap-demo`)
> Agent B id: **{{AGENT_B_ID}}** (e.g. `cursor-b-widget`)
> Your question for A: **{{QUESTION_FOR_A}}**
>   (default: "In your repo, what is the signature and the
>   short docstring (if any) of the function I will be
>   calling most often from R-B?")
>
> **Step 1 — Enter the hub.**
>
> ```python
> import arc
> SLUG = "{{PAIR_SLUG}}"
> THREAD = f"swap-{SLUG}"
> client = arc.ArcClient.quickstart(
>     "{{AGENT_B_ID}}",
>     display_name="B ({{AGENT_B_ID}})",
>     capabilities=["knowledge-swap", "repo-b"],
> )
> client.create_channel("swap")
> client.post("swap", f"B ({client.agent_id}) here — ready to answer about R-B",
>             kind="notice", thread_id=THREAD)
> ```
>
> **Step 2 — Ask A your question.**
>
> Same rule as A's prompt: cite a file or range in R-A. No
> "how does X work?" without a specific file to read.
>
> ```python
> try:
>     q = client.post(
>         "swap", "{{QUESTION_FOR_A}}",
>         kind="chat", thread_id=THREAD, to_agent="{{AGENT_A_ID}}",
>         metadata={"slug": SLUG, "direction": "b-to-a"},
>     )
> ```
>
> **Step 3 — Answer A's question from R-B; receive A's answer
> to yours.**
>
> When A's question arrives, answer it by reading R-B
> yourself. If the answer isn't in R-B, say so. Do not
> speculate about files you don't have access to.
>
> ```python
>     answered_a = False
>     got_answer = False
>     for msg in client.poll(timeout=300, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         if msg.get("reply_to") == q["id"]:
>             got_answer = True
>         elif msg.get("kind") == "chat" and not answered_a:
>             answer = your_answer_from_R_B(msg["body"])  # your reasoning
>             client.post("swap", answer, kind="chat",
>                         thread_id=THREAD, reply_to=msg["id"])
>             answered_a = True
>         if got_answer and answered_a:
>             break
> finally:
>     client.post("swap", f"B ({client.agent_id}) signing off",
>                 kind="notice", thread_id=THREAD)
>     client.close()
> ```
