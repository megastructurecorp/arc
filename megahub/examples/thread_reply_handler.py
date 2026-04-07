from __future__ import annotations

import argparse
import json
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Example Agent Hub bridge handler")
    parser.add_argument("--agent-name", default="Agent")
    parser.add_argument(
        "--style",
        choices=("ack", "review"),
        default="ack",
        help="Reply style to emit for matched messages",
    )
    return parser


def build_reply(message: dict, *, agent_name: str, style: str) -> dict:
    from_agent = message.get("from_agent", "unknown")
    body = str(message.get("body", "") or "").strip()
    thread_id = message.get("thread_id")

    if style == "review":
        text = (
            f"{agent_name} reviewed message {message.get('id')} from {from_agent}. "
            f"Thread={thread_id or 'none'}. Suggested next step: convert the latest point "
            f"into a concrete task, claim, or artifact so the thread keeps moving."
        )
    else:
        excerpt = body[:140] + ("..." if len(body) > 140 else "")
        text = (
            f"{agent_name} received your message from {from_agent}. "
            f"Acknowledged on thread {thread_id or 'none'}. "
            f"Excerpt: {excerpt}"
        )

    return {
        "kind": "chat",
        "body": text,
        "metadata": {
            "handler": "thread_reply_handler",
            "style": style,
            "source_message_id": message.get("id"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw = sys.stdin.read()
    message = json.loads(raw) if raw.strip() else {}
    print(json.dumps(build_reply(message, agent_name=args.agent_name, style=args.style)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
