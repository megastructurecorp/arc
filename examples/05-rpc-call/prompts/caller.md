# Caller agent — paste into the session that will call `lint-spec`

> You are a **caller agent** making synchronous RPC calls to
> `lint-spec`, a Python lint specialist already running on
> `#rpc`. You are going to call it three times with three
> different snippets, print each report, and sign off. You do
> not need to do any other work.
>
> This prompt is paired with `docs/AGENTS.md` and the
> harness-specific file (e.g. `docs/harnesses/claude-code.md`).
> Read both before continuing. Everything below assumes you
> have.
>
> **Your agent_id is `<harness>-rpc-caller-<short-tag>`**, e.g.
> `cc-rpc-caller-rod-mac`.
>
> ## Step 1 — Verify the specialist is up
>
> Before calling anything, check that `lint-spec` is currently
> registered on the hub:
>
> ```bash
> curl "http://127.0.0.1:6969/v1/agents" | python -m json.tool
> ```
>
> Look for an entry with `"agent_id": "lint-spec"`. If it is
> not there, stop and tell the operator — do not proceed.
> Running the caller without the specialist parked will just
> time out.
>
> ## Step 2 — Enter the hub
>
> Run the self-test per `AGENTS.md` §2. Expect Case A. Then:
>
> ```python
> import arc
> client = arc.ArcClient.quickstart(
>     "<your id>",
>     display_name="RPC caller agent",
>     capabilities=["rpc", "caller"],
> )
> client.create_channel("rpc")   # idempotent
> client.post("rpc", f"caller {client.agent_id} online, about to make 3 calls", kind="notice")
> ```
>
> ## Step 3 — Make three calls
>
> ```python
> import json
>
> SNIPPETS = {
>     "clean": """
> def add(a, b):
>     return a + b
>
> class Point:
>     def __init__(self, x, y):
>         self.x = x
>         self.y = y
> """.strip(),
>
>     "broken": """
> def oops(
>     return 1
> """.strip(),
>
>     "multi": """
> def fetch(url):
>     return url
>
> async def fetch_async(url):
>     return url
>
> class Fetcher:
>     pass
>
> class RetryFetcher(Fetcher):
>     pass
> """.strip(),
> }
>
> for name, source in SNIPPETS.items():
>     reply = client.call(
>         "lint-spec",
>         source,
>         channel="rpc",
>         timeout=30.0,
>     )
>     print(f"--- {name} ---")
>     try:
>         report = json.loads(reply["body"])
>         print(json.dumps(report, indent=2))
>     except json.JSONDecodeError:
>         print(reply["body"])
>     print()
> ```
>
> What you should see:
>
> - `clean` → `{"ok": true, "functions": ["add"], "classes": ["Point"], ...}`
> - `broken` → `{"ok": false, "error": "SyntaxError: ...", ...}`
> - `multi`  → functions `["fetch", "async fetch_async"]`, classes `["Fetcher", "RetryFetcher"]`
>
> If any call raises `arc.ArcError` with "RPC to lint-spec
> timed out", the specialist is not actually answering.
> Check `/v1/agents` again and look at the specialist's
> session — the most common cause is the specialist forgot
> to set `reply_to=msg["id"]` on its response, so `client.call`
> cannot find the matching reply.
>
> ## Step 4 — Sign off
>
> ```python
> client.post(
>     "rpc",
>     f"{client.agent_id} signing off, 3 calls complete",
>     kind="notice",
> )
> client.close()
> ```
>
> You are a one-shot caller — once the three calls are done,
> your job is complete. The specialist keeps running for the
> next caller.
>
> ## What to do if you need more than three calls
>
> Replace the `SNIPPETS` dict with whatever you actually want
> to lint. `client.call` is sequential — each call blocks
> until the previous one finishes. If you need concurrent
> calls, spawn multiple caller sessions or post
> `task_request` messages manually and scan for
> `task_result` replies yourself (see `docs/PROTOCOL.md` §6
> for the full wire format).
