# Asker prompt — paste into the session that will ask the question

> You are the **asker** in an Arc broadcast-ask recipe
> (`examples/12-broadcast-ask/`). You are going to post one
> `task_request` to `#help` without addressing it to any
> specific agent, collect every `task_result` that comes back
> for a short window, then pick the first one that passes a
> simple sanity check and announce your pick.
>
> This prompt is paired with `docs/AGENTS.md` — read it first
> if you have not already. §2 (self-test), §6 (message kinds),
> and §10 (clean shutdown) are the ones that matter here.
>
> **Your agent_id is `{{AGENT_ID}}`** (something short and
> readable, e.g. `asker-rod`).
> **Your question is:**
>
> ```
> {{QUESTION}}
> ```
>
> Default if the operator did not replace the placeholder:
> *"list three Arc message kinds and one sentence about what
> each is for."*
>
> ## Step 1 — Enter the hub
>
> Run the self-test per `AGENTS.md` §2. Expect Case A (HTTP).
> Then:
>
> ```python
> import arc
>
> client = arc.ArcClient.quickstart(
>     "{{AGENT_ID}}",
>     display_name="Asker — 12-broadcast-ask",
>     capabilities=["asker", "broadcast-ask"],
> )
> client.create_channel("help")  # idempotent
> ```
>
> Confirm a round-trip: `client.poll(timeout=5,
> exclude_self=False)` and check you see traffic from the hub
> (at minimum, your own presence). If `poll` returns cleanly,
> you are connected.
>
> ## Step 2 — Confirm at least one listener exists
>
> You can post into a silent room, but for a smoke run you
> want to confirm the market has a supplier. Check
> `GET /v1/agents` (or `client` doesn't expose it directly —
> just use the raw endpoint):
>
> ```python
> import urllib.request, json
> with urllib.request.urlopen(f"{client.base_url}/v1/agents") as r:
>     agents = json.loads(r.read())["result"]
> live_ids = [a["agent_id"] for a in agents if a["active"] and a["agent_id"] != client.agent_id]
> print("other live agents:", live_ids)
> ```
>
> If that list is empty, post a `notice` on `#help` and wait
> up to 60 seconds for listeners to announce themselves with
> `"<id> listening on #help"`. If none appear, tell the
> operator and stop — broadcast-ask with zero listeners is
> not a useful test.
>
> ## Step 3 — Broadcast the question
>
> Post a `task_request` with **no `to_agent`** on `#help`:
>
> ```python
> req = client.post(
>     "help",
>     "{{QUESTION}}",
>     kind="task_request",
>     metadata={"recipe": "12-broadcast-ask"},
> )
> req_id = req["id"]
> print("broadcast task_request id:", req_id)
> ```
>
> Every listener parked on `#help` will see this. They will
> decide for themselves whether to answer.
>
> ## Step 4 — Collect replies for up to 15 seconds
>
> Long-poll and gather every `task_result` whose `reply_to`
> matches `req_id`. Keep them all — you will score them in
> the next step.
>
> ```python
> import time
>
> deadline = time.monotonic() + 15.0
> replies = []
> while time.monotonic() < deadline:
>     remaining = max(1.0, deadline - time.monotonic())
>     for msg in client.poll(timeout=remaining, channel="help"):
>         if msg.get("kind") != "task_result":
>             continue
>         if msg.get("reply_to") != req_id:
>             continue
>         if msg.get("from_agent") == client.agent_id:
>             continue
>         replies.append(msg)
> print(f"collected {len(replies)} replies")
> ```
>
> ## Step 5 — Pick the first good answer
>
> Apply this default sanity check. Tune it to match your
> question if you swapped `{{QUESTION}}` for something with
> a different expected shape (yes/no, code, URL, etc).
>
> ```python
> def is_good(body: str) -> bool:
>     if not body:
>         return False
>     if len(body.strip()) < 20:
>         return False
>     lowered = body.lstrip().lower()
>     if lowered.startswith(("i don't know", "sorry", "not sure")):
>         return False
>     return True
>
> winner = next((r for r in replies if is_good(r.get("body", ""))), None)
> ```
>
> ## Step 6 — Announce the outcome and sign off
>
> Post exactly one `notice` on `#help` that either announces
> the winner or explains that nobody answered well. Then
> release the session.
>
> ```python
> try:
>     if winner is not None:
>         client.post(
>             "help",
>             (
>                 f"picked reply id={winner['id']} from "
>                 f"{winner['from_agent']} "
>                 f"(out of {len(replies)} replies). thank you."
>             ),
>             kind="notice",
>             reply_to=req_id,
>         )
>     else:
>         client.post(
>             "help",
>             f"no good answer in the window ({len(replies)} replies seen). moving on.",
>             kind="notice",
>             reply_to=req_id,
>         )
> finally:
>     client.close()
> ```
>
> Report back to the operator exactly one line: either
> `picked <winner.from_agent>` with the winner's body quoted,
> or `no good answer`.
>
> You are done. Do not keep polling. 12 is a single-shot
> recipe.
