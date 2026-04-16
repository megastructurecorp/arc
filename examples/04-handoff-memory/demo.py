"""
Scripted smoke test for the handoff-memory pattern.

Runs two ArcClient instances — a simulated ancestor and descendant — against
a local Arc hub and walks the full handoff choreography end-to-end. Useful
for validating that your hub + long-poll + thread views all work before
trusting the pattern to a real handoff between two LLM sessions.

Usage:

    arc ensure                       # start the hub if it isn't already
    python examples/04-handoff-memory/demo.py [--slug SLUG] [--base-url URL]

The demo runs ancestor and descendant in two threads in the same process.
Real use spreads them across two separate agent sessions; the Arc hub is
indifferent to which process each side lives in, which is the whole point.
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


DECISIONS = """\
- Using SQLite WAL mode for auth.db. Reason: cheap rollback, low write volume.
- Session tokens live in an HttpOnly cookie, not localStorage. Reason: XSS exposure.
- Refresh tokens rotate on every use. Reason: limits blast radius of a stolen token.
"""

DEAD_ENDS = """\
- Tried JWT with refresh-token rotation. Ruled out: broke single-device-at-a-time requirement
  because the rotation is per-token, not per-device.
- Tried per-IP rate limiting. Ruled out: office NAT made the whole team share one bucket.
- Tried rolling our own HMAC signer. Ruled out: too easy to get timing-attack wrong;
  we use the stdlib `hmac.compare_digest` now.
"""

OPEN_QUESTIONS = """\
- Q: Should logout revoke *all* sessions for the user or just the current one?
  A (guess): just the current one, with an explicit "sign out everywhere" button separately.
  Not validated with product yet.
- Q: How long should the session TTL be?
  A (guess): 14 days sliding window. Product wants longer, security wants shorter;
  14 is where the last conversation landed but nobody signed off.
"""

PLAN = """\
1. Add a sessions table keyed by (user_id, device_fingerprint).
2. Wire the /logout endpoint to delete exactly one row from that table.
3. Add a /logout-all endpoint for the "sign out everywhere" button.
4. Write integration tests for both endpoints (real SQLite, no mocks).
5. Ask product+security to sign off on the 14-day TTL.
"""


def run_ancestor(slug: str, base_url: str, stop_event: threading.Event) -> None:
    thread_id = f"handoff-{slug}"
    ancestor_id = f"demo-ancestor-{slug}"
    c = arc.ArcClient.quickstart(
        ancestor_id,
        base_url=base_url,
        display_name=f"Demo ancestor for {slug}",
        capabilities=["ancestor", "handoff", "demo", slug],
    )
    print(f"[ancestor] registered as {ancestor_id}", flush=True)
    try:
        _run_ancestor_inner(c, slug, thread_id, ancestor_id, stop_event)
    finally:
        c.close()
        print("[ancestor] deregistered", flush=True)


def _run_ancestor_inner(c, slug: str, thread_id: str, ancestor_id: str,
                         stop_event: threading.Event) -> None:
    # Ensure the #handoff channel exists before posting into it. Idempotent.
    c.create_channel("handoff")

    for slot, body in [
        ("decisions", DECISIONS),
        ("dead-ends", DEAD_ENDS),
        ("open-questions", OPEN_QUESTIONS),
        ("plan", PLAN),
    ]:
        c.post(
            "handoff", body,
            kind="artifact",
            thread_id=thread_id,
            metadata={"slot": slot, "project": slug},
        )
        print(f"[ancestor] posted artifact slot={slot}", flush=True)

    c.post(
        "handoff",
        f"handoff ready for demo-descendant-{slug} on thread {thread_id}",
        kind="notice",
        thread_id=thread_id,
        metadata={"project": slug, "phase": "ready"},
    )
    print("[ancestor] posted handoff-ready notice; polling for descendant", flush=True)

    # Poll until we see "handoff accepted" or the stop event fires.
    last_nudge = time.monotonic()
    while not stop_event.is_set():
        msgs = c.poll(timeout=5, thread_id=thread_id)  # short for demo speed
        for m in msgs:
            if m["from_agent"] == ancestor_id:
                continue
            body = m.get("body") or ""
            kind = m.get("kind")
            if kind == "notice" and "handoff accepted" in body:
                c.post(
                    "handoff",
                    f"ancestor {ancestor_id} signing off. Good luck.",
                    kind="notice",
                    thread_id=thread_id,
                    metadata={"project": slug, "phase": "signoff"},
                )
                print("[ancestor] saw handoff accepted; signing off", flush=True)
                return
            if kind == "chat":
                # Canned "answer" — a real ancestor would reason here.
                answer = f"(demo) re: {body!r}\n→ accept the best-guess answer in open-questions; no new info."
                c.post(
                    "handoff", answer,
                    kind="chat",
                    thread_id=thread_id,
                    reply_to=m["id"],
                )
                print(f"[ancestor] answered question id={m['id']}", flush=True)
        if time.monotonic() - last_nudge > 30:  # fast nudge for demo
            c.post(
                "handoff",
                "ancestor still here, polling — descendant welcome any time",
                kind="notice",
                thread_id=thread_id,
            )
            last_nudge = time.monotonic()
    print("[ancestor] stop event; exiting", flush=True)


def run_descendant(slug: str, base_url: str) -> bool:
    thread_id = f"handoff-{slug}"
    descendant_id = f"demo-descendant-{slug}"
    # Brief delay so the ancestor has posted the bundle before we arrive.
    time.sleep(1.5)
    c = arc.ArcClient.quickstart(
        descendant_id,
        base_url=base_url,
        display_name=f"Demo descendant for {slug}",
        capabilities=["descendant", "handoff", "demo", slug],
    )
    print(f"[descendant] registered as {descendant_id}", flush=True)
    try:
        return _run_descendant_inner(c, slug, thread_id, descendant_id)
    finally:
        c.close()
        print("[descendant] deregistered", flush=True)


def _run_descendant_inner(c, slug: str, thread_id: str, descendant_id: str) -> bool:
    c.post(
        "handoff",
        f"descendant {descendant_id} joining for handoff on thread {thread_id}",
        kind="notice",
        thread_id=thread_id,
        metadata={"project": slug, "phase": "joining"},
    )

    # Read the full thread.
    thread = c._call("GET", f"/v1/threads/{thread_id}")["result"]
    artifacts = {}
    for m in thread.get("messages", []):
        meta = m.get("metadata") or {}
        if m.get("kind") == "artifact" and meta.get("slot"):
            artifacts[meta["slot"]] = m
    missing = {"decisions", "dead-ends", "open-questions", "plan"} - set(artifacts)
    if missing:
        print(f"[descendant] WARNING: missing artifact slots {missing}", flush=True)
        return False
    print(f"[descendant] read {len(artifacts)} artifact slots from thread", flush=True)

    # Ask one canned clarifying question so the ancestor has something to answer.
    q = c.post(
        "handoff",
        "Quick clarifying question: on the 14-day TTL, is the sliding window refreshed on "
        "any API call or only on the /refresh endpoint?",
        kind="chat",
        thread_id=thread_id,
    )
    print(f"[descendant] posted question id={q['id']}", flush=True)

    # Wait for the answer.
    pending = {q["id"]}
    deadline = time.monotonic() + 30
    while pending and time.monotonic() < deadline:
        msgs = c.poll(timeout=5, thread_id=thread_id)
        for m in msgs:
            if m["from_agent"] == descendant_id:
                continue
            if m.get("reply_to") in pending and m.get("kind") == "chat":
                print(f"[descendant] got answer to id={m['reply_to']}", flush=True)
                pending.discard(m["reply_to"])
    if pending:
        print("[descendant] timed out waiting for answer", flush=True)
        return False

    # Accept the handoff.
    c.post(
        "handoff",
        f"handoff accepted — {descendant_id} taking over",
        kind="notice",
        thread_id=thread_id,
        metadata={"project": slug, "phase": "accepted"},
    )
    print("[descendant] posted handoff-accepted notice", flush=True)

    # Wait for the ancestor sign-off, briefly.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        for m in c.poll(timeout=2, thread_id=thread_id):
            if m.get("kind") == "notice" and "signing off" in (m.get("body") or ""):
                print("[descendant] saw ancestor sign-off; done", flush=True)
                return True
    print("[descendant] did not see ancestor sign-off (but handoff is accepted)", flush=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="demo-handoff")
    ap.add_argument("--base-url", default="http://127.0.0.1:6969")
    args = ap.parse_args()

    stop = threading.Event()
    ancestor_thread = threading.Thread(
        target=run_ancestor, args=(args.slug, args.base_url, stop), daemon=True
    )
    ancestor_thread.start()

    ok = False
    try:
        ok = run_descendant(args.slug, args.base_url)
    finally:
        stop.set()
        ancestor_thread.join(timeout=5)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
