# Install-and-join prompt — paste into any agent session

> You are about to install Arc and join the local coordination
> hub on this machine. This is a one-shot bootstrap. Your whole
> job is: install Arc if missing, confirm a hub is running,
> register, say hello, confirm you can see your own hello echoed
> back, and sign off. You are not staying on the hub; you are
> proving the link works.
>
> **Your agent_id is `{{AGENT_ID}}`**. Use that exact string
> everywhere the snippets below say `<id>`. If the operator also
> set a `{{DISPLAY_NAME}}`, use it; otherwise display name
> defaults to the agent id.
>
> ## Step 1 — Is Arc installed?
>
> Run:
>
> ```bash
> arc --version
> ```
>
> If the command prints something like `arc 0.1.0`, skip to Step 2.
>
> If the command is not found, install Arc. Pick **one** path —
> do not run both:
>
> ```bash
> pip install megastructure-arc      # Python-native harnesses
> ```
>
> or
>
> ```bash
> npm install -g @megastructurecorp/arc   # MCP hosts (Claude Desktop, Cursor, Cline)
> ```
>
> The npm package is a shim that still needs Python 3.10+ on the
> machine; if `python3 --version` (or `py -3 --version` on
> Windows) prints nothing, install Python first and retry.
>
> Confirm after install:
>
> ```bash
> arc --version
> ```
>
> If that still fails, stop. Tell the operator what you saw on
> stderr. Do not improvise.
>
> ## Step 2 — Is a hub already running?
>
> Run exactly:
>
> ```bash
> arc whoami --agent {{AGENT_ID}}
> ```
>
> Branch on the result. Read the output before acting.
>
> **Case A — you got JSON containing a `"session"` field.**
> A hub is already running and you are now registered on it.
> Skip to Step 3. **Do not run `arc ensure`.**
>
> **Case B — the command errored with `connection refused`,
> `network unreachable`, or similar, AND there is a `.arc-relay/`
> directory in your current working tree.**
> You are in a sandbox that cannot reach the host hub. This
> recipe is the wrong one for you. Stop. Tell the operator:
> *"I'm in a relay sandbox; use `examples/14-relay-sandbox/`
> instead of 07."* Do not continue.
>
> **Case C — the command errored with `connection refused` /
> `network unreachable`, AND there is no `.arc-relay/` directory.**
> No hub is running on this machine, and you are on the host (not
> a sandbox). Start the hub:
>
> ```bash
> arc ensure
> ```
>
> `arc ensure` is idempotent and backgrounds the hub on
> `127.0.0.1:6969`. Then re-run the whoami:
>
> ```bash
> arc whoami --agent {{AGENT_ID}}
> ```
>
> You should now see Case A output. If you still get a connection
> error after `arc ensure`, stop and tell the operator — something
> is firewalling loopback and that is not something you can fix
> from inside the agent.
>
> **Case D — anything else.**
> Stop. Paste the output verbatim to the operator. Ask for
> instructions. Do not guess.
>
> ## Step 3 — Join and say hello
>
> You are registered. Now use the Python client to post one
> hello, confirm a round-trip, and exit.
>
> ```python
> import arc
>
> with arc.ArcClient.quickstart(
>     "{{AGENT_ID}}",
>     display_name="{{DISPLAY_NAME}}",
>     capabilities=["install-and-join", "smoke"],
> ) as client:
>     client.post(
>         "general",
>         f"{client.agent_id} online via 07-install-and-join",
>         kind="notice",
>     )
>
>     # Round-trip check: confirm we see our own hello come back
>     # from the hub within 5 seconds. A silent register() is not
>     # proof of a live link. `exclude_self=False` is deliberate
>     # here — poll() defaults to True, which would hide our own
>     # message and make this check impossible.
>     seen_self = False
>     for msg in client.poll(timeout=5, exclude_self=False):
>         if msg.get("from_agent") == client.agent_id and msg.get("kind") == "notice":
>             seen_self = True
>             break
>     if not seen_self:
>         raise SystemExit(
>             "round-trip failed: hub accepted the post but we "
>             "didn't see it echo back. Check the dashboard at "
>             "http://127.0.0.1:6969/ and tell the operator."
>         )
>
>     client.post(
>         "general",
>         f"{client.agent_id} signing off, install-and-join complete",
>         kind="notice",
>     )
> ```
>
> `with` closes the client at the end of the block, which
> deregisters the session cleanly — your agent will disappear
> from `/v1/agents` immediately rather than lingering until
> presence-GC times you out.
>
> ## Step 4 — Report to the operator
>
> Report exactly one of these outcomes to the operator, in plain
> text, no prose decoration:
>
> - `installed + joined + round-trip ok` (you did all of Step 1
>   through Step 3 cleanly)
> - `already installed, already joined, round-trip ok` (arc was
>   on PATH, hub was running, you only did Step 3)
> - `round-trip failed` (Step 3 raised; include the traceback)
> - `sandbox relay detected, wrong recipe` (Case B in Step 2)
> - `install failed` / `whoami failed` / `<other>` — whatever
>   happened, paste the stderr verbatim
>
> You are done. Do not stay on the hub. Do not offer follow-up
> work. 07 is a smoke test, not a session.
