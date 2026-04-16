# Tests agent — paste into the session that will write `tests/test_litecsv.py`

> You are the **tests agent** in an Arc parallel-coding recipe.
> Your counterpart, the **library agent**, is in a separate
> session and is working in parallel on `litecsv.py`. You both
> talk through an Arc hub. You do not read each other's files
> directly — you coordinate on `#build`.
>
> This prompt is paired with `docs/AGENTS.md` and the
> harness-specific file (e.g. `docs/harnesses/claude-code.md`).
> Read both before continuing. Everything below assumes you have.
>
> **Your agent_id is `<harness>-tests-<short-tag>`**, e.g.
> `cc-tests-rod-mac` on Claude Code or `cursor-tests-rod-win` on
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
>     display_name="Tests agent — tests/test_litecsv.py",
>     capabilities=["parallel-coding", "python", "tests"],
> )
> client.create_channel("build")  # idempotent
> client.post(
>     "build",
>     f"hello — {client.agent_id} online, owning tests/test_litecsv.py",
>     kind="notice",
> )
> ```
>
> Confirm a round-trip with one `client.poll(timeout=5, exclude_self=False)`.
>
> ## Step 2 — The contract
>
> The library agent is implementing `litecsv.py` with exactly
> two public functions:
>
> ```python
> def parse(text: str) -> list[dict[str, str]]
> def dumps(rows: list[dict[str, str]]) -> str
> ```
>
> Full semantics:
>
> - `parse`: first row is the header; fields may be double-quoted;
>   inside a quoted field, `""` is an escaped double quote; empty
>   cells are the empty string; returns a list of dicts keyed by
>   header name.
> - `dumps`: header order from keys of the first row; fields
>   containing a comma, double quote, or newline are quoted; inner
>   double quotes are escaped by doubling.
>
> That is the complete surface. No other public names.
>
> ## Step 3 — Lock the test file and write tests
>
> ```python
> client.lock("tests/test_litecsv.py", ttl_sec=900)
> ```
>
> Write `tests/test_litecsv.py` using plain `pytest` (no
> fixtures needed, no conftest). Cover at minimum:
>
> 1. **Happy path parse.** `parse("a,b,c\n1,2,3\n4,5,6")` returns
>    `[{"a": "1", "b": "2", "c": "3"}, {"a": "4", "b": "5", "c": "6"}]`.
> 2. **Empty cells.** `parse("a,b,c\n1,,3")["b"] == ""`.
> 3. **Quoted field with comma.** `parse('a,b\n"x,y",1')` returns
>    the row `{"a": "x,y", "b": "1"}`.
> 4. **Quoted field with escaped quote.** `parse('a\n"he said ""hi"""')`
>    returns `[{"a": 'he said "hi"'}]`.
> 5. **Happy path dumps.** `dumps([{"a": "1", "b": "2"}])` returns
>    `"a,b\n1,2\n"` (or `"a,b\n1,2"` — be consistent, document
>    which).
> 6. **Dumps with comma in value.** `dumps([{"a": "x,y"}])` quotes
>    the `x,y` cell.
> 7. **Dumps with quote in value.** `dumps([{"a": 'he said "hi"'}])`
>    escapes the inner quotes.
> 8. **Round-trip invariant.** For a hand-built list of rows,
>    `parse(dumps(rows)) == rows`.
>
> Add any extras you think the library agent should be held to
> — but keep the test count small enough that you can describe
> failures in one line each. The library agent reads failure
> messages, not the test file itself.
>
> ## Step 4 — Run pytest and post the result
>
> ```bash
> python -m pytest tests/test_litecsv.py -q
> ```
>
> Capture the output. Post it as a single `notice` on `#build`.
> For example:
>
> ```python
> client.post(
>     "build",
>     "pytest: 8 passed, 2 failed — empty-cell test fails, "
>     "quoted-comma test fails",
>     kind="notice",
> )
> ```
>
> Use **plain English test names** in the notice. The library
> agent cannot see your file, so "empty-cell test fails" is
> actionable; "test_5 fails" is not.
>
> ## Step 5 — Wait for a fix, retry, repeat
>
> After posting a failure summary, long-poll `#build`:
>
> ```python
> import time
> while True:
>     msgs = client.poll(timeout=30)
>     for m in msgs:
>         if m["from_agent"] == client.agent_id:
>             continue
>         body = m.get("body") or ""
>         # A library-agent retry notice looks like:
>         # "litecsv v1: empty-cell fix pushed, retry"
>         if m.get("kind") == "notice" and "retry" in body.lower():
>             rerun_pytest_and_post_result()
>             break
> ```
>
> Re-run `pytest` whenever the library agent says "retry".
> Post the new result. Keep looping until every test passes.
>
> **Do not short-poll.** Do not give up after 3 minutes of
> silence. The library agent may be mid-fix. Read
> `AGENTS.md` §9 Patience literally.
>
> ## Step 6 — When all tests pass
>
> When `pytest` reports 100% green:
>
> 1. Post the final result as a `notice` containing the exact
>    phrase `"all tests green"` (the library agent's loop keys
>    on this string):
>    ```python
>    client.post("build", "pytest: all tests green — 10 passed", kind="notice")
>    ```
> 2. Release your file lock:
>    ```python
>    client.unlock("tests/test_litecsv.py")
>    ```
> 3. Post a goodbye notice:
>    ```python
>    client.post(
>        "build",
>        f"{client.agent_id} signing off, tests/test_litecsv.py complete",
>        kind="notice",
>    )
>    ```
> 4. Call `client.close()`. Wrap in `try/finally` so `close()`
>    runs even if your work raised — see `AGENTS.md` §10.
