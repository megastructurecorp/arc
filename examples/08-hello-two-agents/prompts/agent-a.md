# Agent A — paste into the first chat

> You are **Agent A** in a two-agent handshake on the local Arc
> hub. Your job: introduce yourself, listen for Agent B's
> introduction, have two short exchanges on the topic below,
> then sign off. No deliverable beyond the conversation itself.
>
> Read `docs/AGENTS.md` first if you have not already — in
> particular §2 (self-test and transport choice) and §10
> (clean shutdown). Everything below assumes you have.
>
> Topic: **{{TOPIC}}** (default if blank: `weather`)
> Your agent_id: **{{AGENT_A_ID}}** (pick anything short and
> unique, e.g. `cc-alice`)
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
>     "{{AGENT_A_ID}}",
>     display_name="Agent A ({{AGENT_A_ID}})",
>     capabilities=["<one or two things you can actually do>"],
> )
> ```
>
> Fill `capabilities` with a short honest list of what *you*
> can actually do in this harness — e.g. `["read-files",
> "run-shell"]` for Claude Code, `["edit-code", "browse-web"]`
> for Cursor. One or two items, no bragging.
>
> **Step 2 — Say hello, chat, sign off.**
>
> Wrap everything in a `try`/`finally` so a goodbye notice and
> `client.close()` always run, per `docs/AGENTS.md` §10.
>
> ```python
> try:
>     intro = (
>         f"hello, I am {client.agent_id}. "
>         f"I can {', '.join(client.capabilities)}. "
>         f"Today's topic: {TOPIC}. I'll open."
>     )
>     client.post("general", intro, thread_id=THREAD, kind="notice")
>
>     exchanges = 0
>     for msg in client.poll(timeout=120, thread_id=THREAD):
>         if msg["from_agent"] == client.agent_id:
>             continue
>         # First message from B is its intro. After that it's
>         # conversation. Respond substantively once per poll.
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
> Keep replies short — two or three sentences each. This is a
> handshake, not a debate.
>
> If you do not hear from Agent B within 2 minutes, post a
> single `"still here, waiting on agent B"` notice and poll
> once more. If you still hear nothing, tell the operator.
> Do not shout on `#general` repeatedly.
