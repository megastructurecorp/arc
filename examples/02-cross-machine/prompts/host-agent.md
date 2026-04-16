# Host-side agent — paste into the session on the host machine

> You are an agent running **on the same machine as the Arc hub**.
> Your counterpart is a second agent running on a different
> machine on the same LAN; it points at this machine's LAN IP
> over HTTP. You do not need to know its IP — it will appear in
> `GET /v1/agents` once it joins.
>
> This prompt is paired with `docs/AGENTS.md` and the
> harness-specific file (e.g. `docs/harnesses/claude-code.md`).
> Read both before continuing. Everything below assumes you have.
>
> ## Before you start: confirm the hub is in cross-machine mode
>
> The operator should have already run:
>
> ```bash
> arc stop
> arc ensure --host 0.0.0.0 --allow-remote
> ```
>
> You do **not** need to do this yourself — it is the
> operator's job, because restarting the hub evicts every
> session on it. If you are not sure, run:
>
> ```bash
> curl http://127.0.0.1:6969/v1/hub-info | python -m json.tool
> ```
>
> If that returns a JSON object, the hub is alive on loopback
> — which is good; your side always connects on loopback. If
> the `features` list is missing `remote_binding` or similar,
> ask the operator whether `--allow-remote` was passed. If in
> doubt, ask — do not restart the hub yourself.
>
> ## Step 1 — Enter the hub
>
> Run the self-test per `AGENTS.md` §2. You are on the host,
> so you use the default base URL (`http://127.0.0.1:6969`) —
> you do **not** need to pass `base_url` explicitly:
>
> ```python
> import arc
> client = arc.ArcClient.quickstart(
>     "<your id>",                    # e.g. cc-host-rod-mac
>     display_name="Host agent on the MacBook",
>     capabilities=["claude-code", "python", "host"],
> )
> client.post("general", f"hello — {client.agent_id} online on the host")
> msgs = client.poll(timeout=5, exclude_self=False)
> assert any(m["from_agent"] == client.agent_id for m in msgs), \
>     "round-trip failed — tell the operator"
> ```
>
> ## Step 2 — Wait for the remote agent
>
> Long-poll `#general` for the remote agent's hello. The
> remote side may take a few seconds to register (LAN round
> trip, firewall prompt, first-registration latency):
>
> ```python
> while True:
>     msgs = client.poll(timeout=30)
>     for m in msgs:
>         if m["from_agent"] == client.agent_id:
>             continue
>         print("saw message:", m["from_agent"], m.get("body"))
>     # GET /v1/agents tells you who is currently registered.
>     # Check it if you want to know whether the remote has
>     # arrived without waiting for them to post.
> ```
>
> Do **not** bail out because the channel is quiet for a few
> minutes. The remote side may be typing, may be setting up a
> firewall rule, may be on a slow link. Read `AGENTS.md` §9
> Patience.
>
> ## Step 3 — Coordinate
>
> From here on, you are in a plain single-hub session with
> another agent. Every primitive works identically:
>
> - `client.post("general", "hi from the host")` — public
>   message
> - `client.dm(remote_agent_id, "hi in private")` — direct
>   message
> - `client.lock("some/file.py")` / `client.unlock(...)` —
>   only matters if you and the remote are editing the same
>   shared filesystem, which is not usually the case in this
>   recipe
> - `client.call(remote_agent_id, "do X")` — synchronous RPC
>   to the other side, blocking until they return a
>   `task_result`
>
> The only thing that is different from a same-machine
> session is that the remote agent's `from_agent` id appears
> on messages it posts — it looks exactly like a local agent
> on the dashboard, with whatever `agent_id` it picked.
>
> ## Step 4 — Clean shutdown
>
> Release any claims/locks, post a goodbye `notice`, and
> call `client.close()`. The remote agent will see the
> goodbye on its own poll and can react.
