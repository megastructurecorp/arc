"""
Scripted smoke test for the RPC-call pattern.

Runs `lint-spec` in one thread and a caller in another, against a local
Arc hub. The caller makes three example RPC calls — a clean snippet, a
broken snippet, and a multi-def snippet — and prints the structured
reports the specialist returns. Useful for validating that your hub,
long-poll, and the task_request/task_result round-trip all behave
before trusting the pattern to two real LLM sessions.

Usage:

    arc ensure                          # start the hub if it isn't already
    python examples/05-rpc-call/demo.py [--base-url URL]

The demo runs both sides in the same process. Real use spreads them
across two separate agent sessions; the Arc hub is indifferent to which
process each side lives in.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import threading
import time
import traceback

try:
    import arc
except ImportError:
    sys.exit("error: `arc` not importable. Install megastructure-arc or run from the repo root.")


RPC_CHANNEL = "rpc"
SPECIALIST_ID = "lint-spec"
SHUTDOWN_KEYWORD = "lint-spec shutdown"


def lint(body: str) -> str:
    """Stdlib-only lint: parses the body and returns a JSON report."""
    report = {
        "ok": True,
        "error": None,
        "functions": [],
        "classes": [],
        "longest_line": max((len(line) for line in body.splitlines()), default=0),
    }
    try:
        tree = ast.parse(body)
        compile(tree, "<rpc>", "exec")
    except SyntaxError as exc:
        report["ok"] = False
        report["error"] = f"SyntaxError: {exc.msg} at line {exc.lineno}"
        return json.dumps(report)

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            report["functions"].append(node.name)
        elif isinstance(node, ast.AsyncFunctionDef):
            report["functions"].append(f"async {node.name}")
        elif isinstance(node, ast.ClassDef):
            report["classes"].append(node.name)

    return json.dumps(report)


def run_specialist(base_url: str, stop_event: threading.Event) -> None:
    c = arc.ArcClient.quickstart(
        SPECIALIST_ID,
        base_url=base_url,
        display_name="Python lint specialist (demo)",
        capabilities=["rpc", "specialist", "python", "lint", "demo"],
    )
    # quickstart() now calls bootstrap() automatically, advancing the poll
    # cursor past anything already on the hub.
    print(f"[spec  ] registered as {SPECIALIST_ID}", flush=True)
    try:
        c.create_channel(RPC_CHANNEL)
        c.post(RPC_CHANNEL, "lint-spec ready — callers may start making requests", kind="notice")
        print("[spec  ] ready", flush=True)

        while not stop_event.is_set():
            for msg in c.poll(timeout=5, channel=RPC_CHANNEL):
                kind = msg.get("kind")
                body = msg.get("body") or ""

                if kind in ("chat", "notice") and SHUTDOWN_KEYWORD in body:
                    print("[spec  ] saw shutdown keyword", flush=True)
                    return

                if kind != "task_request":
                    continue
                if msg.get("to_agent") != SPECIALIST_ID:
                    continue

                try:
                    result = lint(body)
                    # NOTE: do NOT set to_agent on the response. The reply
                    # must be a public channel message so that client.call's
                    # channel scan can find it — the hub filters DMs out of
                    # GET /v1/messages?channel= views. reply_to is what
                    # threads the response to the request.
                    c.post(
                        RPC_CHANNEL,
                        result,
                        kind="task_result",
                        reply_to=msg["id"],
                    )
                    print(f"[spec  ] answered request id={msg['id']}", flush=True)
                except Exception as exc:
                    c.post(
                        RPC_CHANNEL,
                        f"internal error: {exc}\n{traceback.format_exc()}",
                        kind="task_result",
                        reply_to=msg["id"],
                        metadata={"error": True},
                    )
                    print(f"[spec  ] error answering id={msg['id']}: {exc}", flush=True)
    finally:
        try:
            c.post(RPC_CHANNEL, "lint-spec shutting down", kind="notice")
        except Exception:
            pass
        c.close()
        print("[spec  ] deregistered", flush=True)


def run_caller(base_url: str) -> bool:
    # Small delay so the specialist posts its "ready" notice before we
    # start calling. In real use you check /v1/agents instead.
    time.sleep(1.0)

    c = arc.ArcClient.quickstart(
        "demo-rpc-caller",
        base_url=base_url,
        display_name="RPC caller (demo)",
        capabilities=["rpc", "caller", "demo"],
    )
    print(f"[caller] registered as demo-rpc-caller", flush=True)

    snippets = {
        "clean": (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "class Point:\n"
            "    def __init__(self, x, y):\n"
            "        self.x = x\n"
            "        self.y = y\n"
        ),
        "broken": (
            "def oops(\n"
            "    return 1\n"
        ),
        "multi": (
            "def fetch(url):\n"
            "    return url\n"
            "\n"
            "async def fetch_async(url):\n"
            "    return url\n"
            "\n"
            "class Fetcher:\n"
            "    pass\n"
            "\n"
            "class RetryFetcher(Fetcher):\n"
            "    pass\n"
        ),
    }

    try:
        c.create_channel(RPC_CHANNEL)
        c.post(RPC_CHANNEL, "demo caller online, about to make 3 calls", kind="notice")

        ok = True
        for name, source in snippets.items():
            try:
                reply = c.call(SPECIALIST_ID, source, channel=RPC_CHANNEL, timeout=15.0)
            except arc.ArcError as exc:
                print(f"[caller] ERROR calling lint-spec for '{name}': {exc}", flush=True)
                ok = False
                continue

            print(f"[caller] --- {name} ---", flush=True)
            try:
                report = json.loads(reply["body"])
                print(json.dumps(report, indent=2), flush=True)
            except json.JSONDecodeError:
                print(reply["body"], flush=True)
            print("", flush=True)

        c.post(RPC_CHANNEL, "demo caller signing off", kind="notice")
        return ok
    finally:
        c.close()
        print("[caller] deregistered", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:6969")
    args = ap.parse_args()

    stop = threading.Event()
    spec_thread = threading.Thread(
        target=run_specialist, args=(args.base_url, stop), daemon=True
    )
    spec_thread.start()

    ok = False
    try:
        ok = run_caller(args.base_url)
    finally:
        # Tell the specialist to shut down via a shutdown-keyword notice.
        try:
            shutdown = arc.ArcClient.quickstart(
                "demo-rpc-shutdown",
                base_url=args.base_url,
                display_name="demo shutdown signaller",
            )
            shutdown.create_channel(RPC_CHANNEL)
            shutdown.post(RPC_CHANNEL, SHUTDOWN_KEYWORD, kind="notice")
            shutdown.close()
        except Exception as exc:
            print(f"[demo  ] could not send shutdown: {exc}", flush=True)
        stop.set()
        spec_thread.join(timeout=10)

    print(f"[demo  ] {'SUCCESS' if ok else 'FAILURE'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
