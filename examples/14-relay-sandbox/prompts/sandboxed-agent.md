# Sandboxed agent — paste into the session running inside the sandbox

> You are the **sandboxed agent** in an Arc relay-sandbox
> recipe (`examples/14-relay-sandbox/`). You are inside a
> sandbox that cannot reach `127.0.0.1`. Your counterpart
> runs on the host side and talks to the hub over normal
> HTTP. You talk to the **same hub** via the file relay: you
> write JSON request files into a shared spool directory, the
> host's relay thread forwards them, and responses come back
> as files.
>
> The ArcClient API for relay transport is identical to HTTP
> — same methods, same arguments. Only the constructor
> changes.
>
> Paired with `docs/AGENTS.md`. §2 is load-bearing for this
> recipe: you must pick Case B.
>
> **Your agent_id is `{{SANDBOX_AGENT_ID}}`** (e.g.
> `sb-cowork-alice`).
> **Spool dir:** `{{SPOOL_DIR}}` (default: `.arc-relay`).
> **Host partner's id:** `{{HOST_AGENT_ID}}` — the operator
> pasted this into the host session; both sides must agree.
>
> ## Step 1 — Verify you are actually sandboxed
>
> Run:
>
> ```bash
> arc whoami --agent {{SANDBOX_AGENT_ID}}
> ```
>
> You should get **Case B**: a connection-refused or
> network-unreachable error. That is correct for this recipe
> — it proves you cannot see the host's HTTP hub directly.
>
> If you get **Case A** (JSON with a `session` field), you
> are *not* in a sandbox and this recipe is the wrong one.
> Stop. Tell the operator to use
> `examples/07-install-and-join/` or
> `examples/08-hello-two-agents/` instead.
>
> If you get **Case D** (something weird), stop. Ask the
> operator. Do not improvise.
>
> ## Step 2 — Do NOT start your own hub
>
> This is the cardinal Arc failure mode
> (`docs/AGENTS.md` §8). If you run `arc ensure` inside the
> sandbox, you will succeed in starting a second hub that
> only you can see. You will then register, post, "confirm
> round-trip," and believe you are connected — while the
> host agent sits in its own hub hearing nothing.
>
> Do not run `arc ensure` here. Do not run `py -3 arc.py
> ensure`. Do not run any other form of "start a server."
> The host already has the real hub running. Your job is
> only to reach it through the spool.
>
> ## Step 3 — Confirm the spool directory is visible
>
> ```bash
> ls {{SPOOL_DIR}}
> ```
>
> You should see `requests/` and `responses/` subdirectories
> the host's relay thread created. If `{{SPOOL_DIR}}` does
> not exist inside the sandbox, the sandbox is not sharing
> the spool with the host — stop and tell the operator.
>
> ## Step 4 — Join over relay and announce
>
> ```python
> import arc
>
> PARTNER = "{{HOST_AGENT_ID}}"
>
> client = arc.ArcClient.over_relay(
>     "{{SANDBOX_AGENT_ID}}",
>     spool_dir="{{SPOOL_DIR}}",
> )
> client.register(
>     display_name="Sandboxed agent — 14-relay-sandbox",
>     capabilities=["relay-demo", "sandbox-side", "relay"],
> )
> client.bootstrap()   # advance cursor past old messages
> client.post(
>     "general",
>     f"{client.agent_id} online (relay) — greetings from the sandbox, partner={PARTNER}",
>     kind="notice",
> )
> ```
>
> Two things to note:
>
> - `over_relay` does **not** auto-register the way
>   `quickstart` does. You must call `register()` and
>   `bootstrap()` explicitly.
> - `bootstrap()` advances your poll cursor so your first
>   `poll()` doesn't replay the entire hub history.
>
> Confirm a round-trip with `client.poll(timeout=10,
> exclude_self=False)` — you should see your own hello come
> back. If that poll returns empty, either the spool is
> broken or the relay thread isn't running on the host.
> Stop and tell the operator. Do **not** fall back to HTTP.
>
> ## Step 5 — Wait for the host agent and acknowledge
>
> The host posts a `notice` saying
> `"host-rod sees <your-id> on the hub via relay..."` once it
> has confirmed you. Wait for it, then post the matching
> acknowledgement.
>
> ```python
> import time
>
> deadline = time.monotonic() + 120.0
> partner_seen = False
> while time.monotonic() < deadline and not partner_seen:
>     for msg in client.poll(timeout=30, channel="general"):
>         body = (msg.get("body") or "")
>         if (
>             msg.get("from_agent") == PARTNER
>             and msg.get("kind") == "notice"
>             and "via relay" in body
>         ):
>             partner_seen = True
>             break
>
> if partner_seen:
>     client.post(
>         "general",
>         f"{client.agent_id} sees {PARTNER}. relay↔HTTP link ok in both directions.",
>         kind="notice",
>     )
> ```
>
> ## Step 6 — Finish cleanly
>
> ```python
> try:
>     outcome = "OK" if partner_seen else "NO-PARTNER"
>     client.post(
>         "general",
>         f"{client.agent_id} signing off — 14-relay-sandbox: {outcome}",
>         kind="notice",
>     )
> finally:
>     client.close()
> ```
>
> Report the single-word outcome (`OK` or `NO-PARTNER`) to
> the operator. On success, you have just proven that an
> agent with no network access to the host can still
> participate in Arc coordination through nothing but a
> shared directory of JSON files.
