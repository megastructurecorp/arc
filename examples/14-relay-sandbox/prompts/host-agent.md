# Host agent — paste into the session running outside the sandbox

> You are the **host agent** in an Arc relay-sandbox recipe
> (`examples/14-relay-sandbox/`). You run on the host side and
> have direct HTTP access to the hub. Your counterpart is in a
> sandbox that cannot reach `127.0.0.1`, so it will join via
> the file relay. Your whole job here is to come online, wait
> for the sandboxed agent to say hello, confirm you see each
> other, and sign off. You are proving the transport works.
>
> Paired with `docs/AGENTS.md`. §2 (self-test, transport choice)
> is the one that matters most.
>
> **Your agent_id is `{{HOST_AGENT_ID}}`** (e.g. `host-rod`).
> **Spool dir:** `{{SPOOL_DIR}}` (default: `.arc-relay`).
> **Sandboxed partner's id:** `{{SANDBOX_AGENT_ID}}` — the
> operator will also paste this into the other session; both
> sides must agree.
>
> ## Step 1 — Confirm the hub and the relay are up
>
> Run:
>
> ```bash
> arc whoami --agent {{HOST_AGENT_ID}}
> ```
>
> Expect Case A (JSON with a `session` field). If you get
> Case C (connection refused), start the hub with the relay
> spool enabled:
>
> ```bash
> arc ensure --spool-dir {{SPOOL_DIR}}
> ```
>
> Then verify the relay is advertised:
>
> ```bash
> curl http://127.0.0.1:6969/v1/hub-info
> ```
>
> The `features` array in the response must contain `"relay"`.
> If it doesn't, your Arc install predates the relay and you
> need to upgrade it before the sandboxed side can join.
>
> Also check the spool directory exists and the subdirs for
> the sandbox partner are present-or-creatable:
>
> ```bash
> ls {{SPOOL_DIR}}                # shows requests/ and responses/
> ```
>
> ## Step 2 — Join the hub over HTTP and announce
>
> ```python
> import arc
>
> PARTNER = "{{SANDBOX_AGENT_ID}}"
>
> client = arc.ArcClient.quickstart(
>     "{{HOST_AGENT_ID}}",
>     display_name="Host agent — 14-relay-sandbox",
>     capabilities=["relay-demo", "host-side", "http"],
> )
> client.post(
>     "general",
>     f"{client.agent_id} online (HTTP) — waiting for sandboxed partner {PARTNER}",
>     kind="notice",
> )
> ```
>
> Confirm a round-trip — `client.poll(timeout=5,
> exclude_self=False)` should return your own hello.
>
> ## Step 3 — Wait for the sandboxed agent and confirm you see each other
>
> Long-poll `#general` for a `notice` from `{{SANDBOX_AGENT_ID}}`
> that reads `"<sb-id> online (relay)"`. Be patient — the
> sandbox's first few spool writes can take a second or two
> on slow filesystems.
>
> ```python
> import time
>
> deadline = time.monotonic() + 120.0   # give the sandbox up to 2 minutes to appear
> partner_seen = False
> while time.monotonic() < deadline and not partner_seen:
>     for msg in client.poll(timeout=30, channel="general"):
>         if (
>             msg.get("from_agent") == PARTNER
>             and msg.get("kind") == "notice"
>             and "online (relay)" in (msg.get("body") or "")
>         ):
>             partner_seen = True
>             break
> ```
>
> If the 2-minute timeout expires with no sandbox hello, stop
> and tell the operator. Do not start troubleshooting the
> sandbox from your side — only the operator can see both.
>
> ## Step 4 — Confirm the two-way link
>
> Post a `notice` specifically acknowledging you can see the
> sandboxed agent. It will do the same for you.
>
> ```python
> client.post(
>     "general",
>     f"{client.agent_id} sees {PARTNER} on the hub via relay. HTTP↔relay link ok.",
>     kind="notice",
> )
>
> # Wait up to 30s for the sandbox's matching ack.
> ack_seen = False
> for _ in range(2):
>     for msg in client.poll(timeout=15, channel="general"):
>         body = (msg.get("body") or "")
>         if (
>             msg.get("from_agent") == PARTNER
>             and msg.get("kind") == "notice"
>             and f"sees {client.agent_id}" in body
>         ):
>             ack_seen = True
>             break
>     if ack_seen:
>         break
> ```
>
> If `ack_seen` stays False, the sandbox → host direction is
> broken even though host → sandbox worked. Report exactly
> that to the operator (it is a useful diagnostic — one side
> of the relay is flowing, the other is not).
>
> ## Step 5 — Finish cleanly
>
> ```python
> try:
>     outcome = "OK" if partner_seen and ack_seen else "PARTIAL"
>     client.post(
>         "general",
>         f"{client.agent_id} signing off — 14-relay-sandbox: {outcome}",
>         kind="notice",
>     )
> finally:
>     client.close()
> ```
>
> Report the single-word outcome to the operator: `OK`,
> `PARTIAL`, or `NO-PARTNER`. If `PARTIAL`, quote the message
> history from `#general` so the operator can see which
> direction stalled.
