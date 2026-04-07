from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Round-robin Agent Hub handler")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--agent-name")
    parser.add_argument(
        "--mode",
        choices=("concise", "review"),
        default="concise",
        help="Reply style for each autonomous turn",
    )
    return parser


def _loop_state(message: dict[str, Any]) -> dict[str, Any]:
    metadata = message.get("metadata") or {}
    loop = metadata.get("loop") or {}
    if not isinstance(loop, dict):
        return {}
    return loop


def build_round_reply(
    message: dict[str, Any],
    *,
    agent_id: str,
    agent_name: str,
    mode: str,
) -> dict[str, Any] | None:
    loop = _loop_state(message)
    participants = loop.get("participants") or []
    next_agent = loop.get("next_agent")
    remaining_turns = loop.get("remaining_turns")

    if not isinstance(participants, list) or len(participants) < 2:
        return None
    participants = [str(item) for item in participants]

    if next_agent != agent_id:
        return None

    try:
        remaining_turns = int(remaining_turns)
    except (TypeError, ValueError):
        return None
    if remaining_turns <= 0:
        return None

    try:
        idx = participants.index(agent_id)
    except ValueError:
        return None

    next_idx = (idx + 1) % len(participants)
    next_turn_agent = participants[next_idx]
    new_remaining = remaining_turns - 1
    turn_number = int(loop.get("turn_number", 0) or 0) + 1

    source_id = message.get("id")
    source_body = str(message.get("body", "") or "").strip()
    source_excerpt = source_body[:180] + ("..." if len(source_body) > 180 else "")

    if mode == "review":
        body = (
            f"{agent_name} turn {turn_number}: reviewed message {source_id}. "
            f"Key idea carried forward: {source_excerpt or 'no body supplied'}. "
            f"Handing off to {next_turn_agent}."
        )
    else:
        body = (
            f"{agent_name} turn {turn_number}: building on message {source_id}. "
            f"Current focus: {source_excerpt or 'continue the thread'}. "
            f"Next turn owner: {next_turn_agent}."
        )

    if new_remaining <= 0:
        body += " Loop complete after this reply."

    return {
        "kind": "chat",
        "body": body,
        "metadata": {
            "handler": "round_robin_handler",
            "loop": {
                "participants": participants,
                "next_agent": next_turn_agent,
                "remaining_turns": new_remaining,
                "turn_number": turn_number,
            },
            "source_message_id": source_id,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw = sys.stdin.read()
    message = json.loads(raw) if raw.strip() else {}
    agent_name = args.agent_name or args.agent_id
    reply = build_round_reply(
        message,
        agent_id=args.agent_id,
        agent_name=agent_name,
        mode=args.mode,
    )
    if reply is not None:
        print(json.dumps(reply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
