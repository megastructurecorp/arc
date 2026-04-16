"""
Scripted smoke test for the parallel-coding pattern.

Runs two ArcClient instances — a simulated library agent and a simulated
tests agent — against a local Arc hub and walks a minimal coordination
end-to-end: hello on #build, lock both files, exchange three rounds of
"v0 posted / pytest fails / fix pushed / pytest green", release locks,
sign off.

Neither agent actually writes litecsv.py or runs pytest here. The demo
is about exercising the *coordination primitives* — file locks, channel
posts, long-poll, notice keywords — not about building a real CSV
library. Use it to validate that your hub, locks, and long-poll all
behave correctly before trusting the pattern to two real LLM sessions.

Usage:

    arc ensure                          # start the hub if it isn't already
    python examples/03-parallel-coding/demo.py [--base-url URL]

The demo runs both agents in two threads in the same process. Real use
spreads them across two separate agent sessions; the Arc hub is
indifferent to which process each side lives in.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

try:
    import arc
except ImportError:
    sys.exit("error: `arc` not importable. Install megastructure-arc or run from the repo root.")


CHANNEL = "build"
LIB_PATH = "litecsv.py"
TEST_PATH = "tests/test_litecsv.py"


def run_library(base_url: str, stop_event: threading.Event,
                tests_ready: threading.Event) -> bool:
    agent_id = "demo-lib"
    c = arc.ArcClient.quickstart(
        agent_id,
        base_url=base_url,
        display_name="Demo library agent (litecsv.py)",
        capabilities=["parallel-coding", "demo", "library"],
    )
    print(f"[lib  ] registered as {agent_id}", flush=True)
    try:
        return _run_library_inner(c, agent_id, stop_event, tests_ready)
    finally:
        c.close()
        print("[lib  ] deregistered", flush=True)


def _run_library_inner(c, agent_id: str, stop_event: threading.Event,
                        tests_ready: threading.Event) -> bool:
    c.create_channel(CHANNEL)
    c.post(CHANNEL, f"hello — {agent_id} online, owning {LIB_PATH}", kind="notice")
    c.lock(LIB_PATH, ttl_sec=600)
    print(f"[lib  ] locked {LIB_PATH}", flush=True)

    # Wait for the tests agent to register before posting v0 so it sees
    # the notice (quickstart now calls bootstrap, which advances since_id
    # past anything that existed before registration).
    tests_ready.wait(timeout=10)
    time.sleep(0.3)
    c.post(CHANNEL, "litecsv v0: parse + dumps happy path done", kind="notice")
    print("[lib  ] posted v0 notice", flush=True)

    version = 0
    while not stop_event.is_set():
        msgs = c.poll(timeout=5)  # short poll for demo speed
        for m in msgs:
            if m["from_agent"] == agent_id:
                continue
            body = (m.get("body") or "").lower()
            if m.get("kind") != "notice":
                continue
            if "all tests green" in body:
                c.unlock(LIB_PATH)
                c.post(
                    CHANNEL,
                    f"{agent_id} signing off, {LIB_PATH} complete",
                    kind="notice",
                )
                print("[lib  ] saw all-green; unlocked and signed off", flush=True)
                return True
            if "failed" in body or "fails" in body:
                version += 1
                time.sleep(0.5)  # pretend to fix
                c.post(
                    CHANNEL,
                    f"litecsv v{version}: empty-cell fix pushed, retry",
                    kind="notice",
                )
                print(f"[lib  ] posted v{version} retry notice", flush=True)
    print("[lib  ] stop event; exiting", flush=True)
    return False


def run_tests(base_url: str, tests_ready: threading.Event) -> bool:
    agent_id = "demo-tests"
    c = arc.ArcClient.quickstart(
        agent_id,
        base_url=base_url,
        display_name="Demo tests agent (tests/test_litecsv.py)",
        capabilities=["parallel-coding", "demo", "tests"],
    )
    print(f"[tests] registered as {agent_id}", flush=True)
    # Signal the library agent that we are online and polling.
    tests_ready.set()
    try:
        return _run_tests_inner(c, agent_id)
    finally:
        c.close()
        print("[tests] deregistered", flush=True)


def _run_tests_inner(c, agent_id: str) -> bool:
    c.create_channel(CHANNEL)
    c.post(CHANNEL, f"hello — {agent_id} online, owning {TEST_PATH}", kind="notice")
    c.lock(TEST_PATH, ttl_sec=600)
    print(f"[tests] locked {TEST_PATH}", flush=True)

    # Canned sequence: two failures, then pass.
    # Each iteration waits for a retry notice (or v0), then posts a result.
    results = [
        "pytest: 8 passed, 2 failed — empty-cell test fails, quoted-comma test fails",
        "pytest: 9 passed, 1 failed — quoted-comma test still fails",
        "pytest: all tests green — 10 passed",
    ]

    # Wait for v0 or a retry then post the next result. Loop until we've
    # posted the final green result.
    sent = 0
    deadline = time.monotonic() + 45
    while sent < len(results) and time.monotonic() < deadline:
        msgs = c.poll(timeout=5)
        for m in msgs:
            if m["from_agent"] == agent_id:
                continue
            body = (m.get("body") or "").lower()
            if m.get("kind") != "notice":
                continue
            if "v0" in body or "retry" in body:
                c.post(CHANNEL, results[sent], kind="notice")
                print(f"[tests] posted result {sent}: {results[sent]}", flush=True)
                sent += 1
                break

    if sent < len(results):
        print("[tests] timed out before posting all results", flush=True)
        return False

    c.unlock(TEST_PATH)
    c.post(
        CHANNEL,
        f"{agent_id} signing off, {TEST_PATH} complete",
        kind="notice",
    )
    print("[tests] unlocked and signed off", flush=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:6969")
    args = ap.parse_args()

    stop = threading.Event()
    tests_ready = threading.Event()
    lib_ok = {"v": False}

    def lib_wrapper():
        lib_ok["v"] = run_library(args.base_url, stop, tests_ready)

    lib_thread = threading.Thread(target=lib_wrapper, daemon=True)
    lib_thread.start()

    tests_ok = False
    try:
        tests_ok = run_tests(args.base_url, tests_ready)
    finally:
        stop.set()
        lib_thread.join(timeout=10)

    ok = tests_ok and lib_ok["v"]
    print(f"[demo ] {'SUCCESS' if ok else 'FAILURE'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
