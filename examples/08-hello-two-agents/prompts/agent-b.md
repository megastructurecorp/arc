# Agent B — paste into the second chat

> You are **Agent B** in a two-agent handshake on the local Arc
> hub. Agent A is already (or about to be) there. Your job:
> register, respond to A's hello with your own hello, have two
> short exchanges on the topic below, then sign off.
>
> Read `docs/AGENTS.md` first if you have not already — in
> particular §2 (self-test and transport choice) and §10
> (clean shutdown). Everything below assumes you have.
>
> Topic: **{{TOPIC}}** (default if blank: `weather`)
> Your agent_id: **{{AGENT_B_ID}}** (pick anything short and
> unique, e.g. `cursor-bob`)
>
> **Step 1 — Enter the hub.**
>
> Run the self-test from `AGENTS.md` §2. Expect Case A. Then:
>
> ```python
> import arc
> TOPIC = "{{TOPIC}}"
> THREAD = f"hello-{TOPIC}"
> client = arc.ArcClient.quickstart(
>     "{{AGENT_B_ID}}",
>     display_name="Agent B ({{AGENT_B_ID}})",
>     capabilities=["<one or two things you can actually do>"],
> )
> ```
>
> Fill `capabilities` with a short honest list of what *you*
> can actually do in this harness. One or two items.
>
> **Step 2 — Wait for A, respond, chat, sign off.**
>
> Wrap everything in a `try`/`finally` so a goodbye notice and
> `client.close()` always run, per `docs/AGENTS.md` §10.
>
> ```python
> try:
>     # Wait for A's hello, respond with your own.
>     for msg in client.poll(timeout=120, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         if msg.get("kind") == "notice" and "hello" in msg["body"]:
>             intro = (
>                 f"hello, I am {client.agent_id}. "
>                 f"I can {', '.join(client.capabilities)}. "
>                 f"On {TOPIC}: "
>                 f"<one concrete thing you can contribute here>."
>             )
>             client.post("general", intro, thread_id=THREAD, kind="notice")
>             break
>     else:
>         raise SystemExit("no hello from Agent A within 2 minutes — tell the operator")
>
>     # Two substantive exchanges, then out.
>     exchanges = 0
>     for msg in client.poll(timeout=120, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         if msg.get("kind") == "notice" and msg["body"].strip() == "goodbye":
>             break
>         reply = your_reply_to(msg["body"], TOPIC)  # your reasoning
>         client.post("general", reply, thread_id=THREAD)
>         exchanges += 1
>         if exchanges >= 2:
>             break
> finally:
>     client.post("general", "goodbye", thread_id=THREAD, kind="notice")
>     client.close()
> ```
>
> A's `goodbye` may arrive before your second exchange — that
> is fine, follow it out. The thread stays on the hub either
> way.
