# Library agent — paste into the session that will write `litecsv.py`

> You are the **library agent** in an Arc parallel-coding recipe.
> Your counterpart, the **tests agent**, is in a separate session
> and is working in parallel on `tests/test_litecsv.py`. You both
> talk through an Arc hub. You do not read each other's files
> directly — you coordinate on `#build`.
>
> This prompt is paired with `docs/AGENTS.md` and the
> harness-specific file (e.g. `docs/harnesses/claude-code.md`).
> Read both before continuing. Everything below assumes you have.
>
> **Your agent_id is `<harness>-lib-<short-tag>`**, e.g.
> `cc-lib-rod-mac` on Claude Code or `cursor-lib-rod-win` on
> Cursor. Derive the prefix from your harness file.
>
> ## Step 1 — Enter the hub
>
> Run the self-test per `AGENTS.md` §2. Expect Case A (HTTP). Then:
>
> ```python
> import arc
> client = arc.ArcClient.quickstart(
>     "<your id>",
>     display_name="Library agent — litecsv.py",
>     capabilities=["parallel-coding", "python", "library"],
> )
> client.create_channel("build")  # idempotent — safe if already made
> client.post(
>     "build",
>     f"hello — {client.agent_id} online, owning litecsv.py",
>     kind="notice",
> )
> ```
>
> Confirm a round-trip with one `client.poll(timeout=5, exclude_self=False)`
> and verify you see your own hello. A silent `register()` is not
> proof of a working link.
>
> ## Step 2 — The contract
>
> You are implementing a tiny CSV parser in `litecsv.py`. The
> **exact public API** is:
>
> ```python
> def parse(text: str) -> list[dict[str, str]]:
>     """First row is the header. Fields may be double-quoted.
>     Inside a quoted field, `""` is an escaped double quote.
>     Empty cells are the empty string. Returns a list of dicts
>     keyed by header name."""
>
> def dumps(rows: list[dict[str, str]]) -> str:
>     """Header order is taken from the keys of the first row.
>     Fields containing a comma, double quote, or newline are
>     quoted. Inner double quotes are escaped by doubling."""
> ```
>
> No other public names. The implementation is entirely up to
> you — pure Python, stdlib only, no imports from outside the
> standard library, no `csv` module (writing your own is the
> point).
>
> ## Step 3 — Lock the file and write the code
>
> ```python
> client.lock("litecsv.py", ttl_sec=900)
> ```
>
> Write `litecsv.py` from scratch. Start with the happy path —
> a simple `"a,b,c\n1,2,3\n4,5,6"` input — and confirm
> `parse()` returns the expected list of dicts before you worry
> about edge cases. Then layer on:
>
> - empty cells (`"a,b,c\n1,,3"`)
> - quoted fields containing commas (`'a,b\n"x,y",1'`)
> - quoted fields containing escaped double quotes (`'a\n"he said ""hi"""'`)
> - round-trip invariant: `parse(dumps(rows)) == rows` for
>   any `rows` where the keys of the first row cover all keys
>   of every row
>
> After each substantial change, post a one-line `notice` to
> `#build`:
>
> ```python
> client.post("build", "litecsv v0: parse + dumps happy path done", kind="notice")
> ```
>
> ## Step 4 — Read test failures and respond to them
>
> The tests agent will post results from `pytest` as `notice`
> messages on `#build`. Your main loop after you post v0 is:
>
> ```python
> import time
> while True:
>     msgs = client.poll(timeout=30)  # chain long-polls, no back-off
>     for m in msgs:
>         if m["from_agent"] == client.agent_id:
>             continue
>         body = m.get("body") or ""
>         # A test-failure notice looks like:
>         # "pytest: 8 passed, 2 failed — empty-cell test fails,
>         #  quoted-comma test fails"
>         if m.get("kind") == "notice" and "failed" in body.lower():
>             # Fix. You already have the lock on litecsv.py.
>             # Edit the file, post a follow-up notice:
>             # client.post("build", "litecsv v1: empty-cell fix pushed, retry", kind="notice")
>             pass
>         if m.get("kind") == "notice" and "all tests green" in body.lower():
>             break_loop = True
>     if any(...):  # all green seen
>         break
> ```
>
> Do **not** silently read `tests/test_litecsv.py` to figure
> out what the test expects. If a failure message is ambiguous,
> post a `chat` on `#build` asking the tests agent to expand on
> it. Coordination on the channel beats file-peeking — see
> `examples/03-parallel-coding/README.md` §Feedback loop.
>
> ## Step 5 — Patience
>
> The tests agent may take minutes to write its first pass.
> While you wait, poll with `client.poll(timeout=30)` in a loop
> — no short polls, no exponential back-off, no bailing out.
> Read `AGENTS.md` §9 Patience literally. The dashboard
> (`http://127.0.0.1:6969`) is the source of truth for who is
> alive, not the channel.
>
> Post a `notice` every 5–10 minutes during long idle stretches
> (e.g. "library agent still here, waiting for first test run")
> so the operator and the tests agent can see you are not stuck.
>
> ## Step 6 — Finish
>
> When the tests agent posts a `notice` containing
> `"all tests green"`:
>
> 1. Run your own sanity check (e.g. `python -c "import litecsv;
>    print(litecsv.parse('a,b\\n1,2'))"`) to confirm the module
>    imports cleanly.
> 2. Release your file lock:
>    ```python
>    client.unlock("litecsv.py")
>    ```
> 3. Post a goodbye notice:
>    ```python
>    client.post(
>        "build",
>        f"{client.agent_id} signing off, litecsv.py complete",
>        kind="notice",
>    )
>    ```
> 4. Call `client.close()` to deregister. Wrap the whole thing
>    in `try/finally` so `close()` runs even if your work
>    raised — see the canonical shape in `AGENTS.md` §10.
