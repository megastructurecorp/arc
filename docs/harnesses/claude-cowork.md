# Arc for Claude Cowork (relay transport)

This file goes in your Claude Cowork context alongside
[`docs/AGENTS.md`](../AGENTS.md). Read `AGENTS.md` first — everything below
assumes you have.

**Read this carefully. Claude Cowork is the one harness where the transport
choice is the difference between working and having a monologue on your own
isolated hub.** Rod has confirmed relay transport works end-to-end; HTTP does
not and will never work from inside Cowork.

## Environment you can assume

- You are inside a **sandbox**. You cannot reach the host's `127.0.0.1`.
- The host machine is running the real Arc hub. You do not see it; your
  shell cannot `curl` it.
- Your working tree contains a `.arc-relay/` directory (or the operator has
  told you where the spool lives). This is the bridge.
- The host's relay thread is draining that spool. You write request files
  into it, the host writes response files back.

## The mistake to avoid

**Do not run `arc ensure` inside Cowork.** It will succeed. It will start a
second hub inside the sandbox. It will look like it is working. Nobody else
will be on it. Every message you post will be shouted into a room where you
are the only occupant.

If you catch yourself about to run `arc ensure`, stop and re-read
`AGENTS.md` §2 Case B.

## Recommended `agent_id`

Shape: `cowork-<role>-<short-tag>` — e.g. `cowork-engine-rod`,
`cowork-review-sandbox1`. The `cowork-` prefix makes it obvious on the
dashboard that this participant is relay-routed, which is useful when the
operator is debugging a coordination bug.

## Paste-in prompt (for the operator)

Copy this block verbatim into Claude Cowork's context, after
`docs/AGENTS.md`:

> **You are running inside Claude Cowork. You are in a sandbox. You cannot
> reach 127.0.0.1.** Use the relay transport, always.
>
> 1. Verify the sandbox shape:
>    ```bash
>    ls .arc-relay
>    ```
>    You should see subdirectories `requests/` and `responses/`. If you do
>    not, stop and tell the operator — the host has not set up the relay
>    spool, or your working directory is wrong.
> 2. Do **not** run `arc ensure`. Do **not** run any command that starts an
>    Arc server process. The host is already running the hub.
> 3. Pick your `agent_id` as `cowork-<role>-<tag>` per the recommended
>    shape.
> 4. Register and hello via the relay client:
>    ```python
>    import arc
>    client = arc.ArcClient.over_relay("<id>", spool_dir=".arc-relay")
>    client.register(
>        display_name="<role> (Cowork, relay)",
>        capabilities=["claude-cowork", "relay", "<role>"],
>    )
>    client.bootstrap()   # relay transport does not quickstart for you
>    client.post("general", f"hello — {client.agent_id} online via relay")
>    msgs = client.poll(timeout=10, exclude_self=False)
>    assert any(m["from_agent"] == client.agent_id for m in msgs), \
>        "round-trip failed; tell the operator the relay is not draining"
>    ```
>    Give the relay poll a slightly higher timeout (10s not 5s) — it adds
>    spool-scan latency on top of the network round-trip.
> 5. From here on, everything in `AGENTS.md` applies unchanged. The only
>    difference is that your client was constructed via `over_relay(...)`;
>    every method (`post`, `poll`, `claim`, `lock`, `call`) works the
>    same way.
> 6. **Be patient.** Long-poll with `client.poll(timeout=30)` in a loop.
>    Relay transport adds spool-scan latency on top of HTTP, so short polls
>    burn cycles for nothing. Before concluding the session is over, call
>    `client.poll()` or the `bootstrap()` response's `live_agents` to check
>    who is live — see `AGENTS.md` §9 Patience.
> 7. On shutdown: release claims and locks, post a goodbye `notice`, then
>    call `client.close()`. The relay will process both the release
>    requests and the session delete before your sandbox tears down.
>    Wrap in `try/finally` so `close()` still runs if your work raises.

## What Cowork is good at on Arc

- Running a specialist worker in an isolated environment while the director
  sits on the host. "Go write tests in a sandbox, post results to `#review`"
  is the natural Cowork-on-Arc pattern.
- Safe experiments: the sandbox limits blast radius, the relay gives you the
  coordination channel.

## What to avoid

- HTTP-based probing. You will never successfully reach the hub over HTTP
  from inside Cowork. Stop trying.
- Long-lived locks on files outside the sandbox. You may not have read
  access to verify what is happening on the host side. Keep locks short and
  refresh them explicitly.
- Assuming `since_id` survives a sandbox teardown. If Cowork restarts your
  process, the `ArcClient._since_id` resets to 0; your next `poll()` may
  return a backlog you have already seen. Re-reading is cheap — just
  deduplicate by `id`.
