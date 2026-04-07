from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .file_relay import DEFAULT_SPOOL_DIR, FileRelayClient

DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_CHANNEL = "smoke-room"
DEFAULT_THREAD_ID = "smoke-relay-001"
DEFAULT_CLAIM_KEY = "smoke-claim-001"


class SmokeError(RuntimeError):
    pass


def _http_json(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None, *, timeout: float = 15.0) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "error": body, "status": exc.code}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"ok": False, "error": f"connection error: {exc}"}


@dataclass(slots=True)
class SmokeTransport:
    transport: str
    agent_id: str
    base_url: str = DEFAULT_BASE_URL
    relay_dir: str = DEFAULT_SPOOL_DIR
    timeout: float = 15.0

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.transport == "relay":
            client = FileRelayClient(
                agent_id=self.agent_id,
                spool_dir=self.relay_dir,
                timeout=self.timeout,
            )
            response = client.call(method, path, payload)
            body = response.get("body")
            if isinstance(body, dict):
                return body
            if response.get("ok"):
                return {"ok": True, "result": body}
            return {"ok": False, "error": response.get("error") or str(body)}
        return _http_json(self.base_url, method, path, payload, timeout=self.timeout)


def _require_ok(resp: dict[str, Any], action: str) -> dict[str, Any]:
    if not resp.get("ok"):
        raise SmokeError(f"{action} failed: {resp.get('error', 'unknown error')}")
    return resp


def _messages_path(channel: str, thread_id: str, since_id: int = 0) -> str:
    query = urllib.parse.urlencode({"channel": channel, "thread_id": thread_id, "since_id": str(since_id)})
    return f"/v1/messages?{query}"


def _wait_for_messages(
    transport: SmokeTransport,
    *,
    channel: str,
    thread_id: str,
    predicate,
    timeout_sec: float,
    poll_interval_sec: float,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        resp = transport.request("GET", _messages_path(channel, thread_id))
        if resp.get("ok"):
            messages = resp.get("result", [])
            if predicate(messages):
                return messages
        time.sleep(poll_interval_sec)
    raise SmokeError(f"timed out waiting for messages on thread {thread_id}")


def _contains_from(messages: list[dict[str, Any]], agent_id: str, *, kind: str | None = None, body_contains: str | None = None) -> bool:
    for item in messages:
        if item.get("from_agent") != agent_id:
            continue
        if kind is not None and item.get("kind") != kind:
            continue
        if body_contains is not None and body_contains not in str(item.get("body", "")):
            continue
        return True
    return False


def run_smoke_agent(
    *,
    role: str,
    transport_name: str,
    base_url: str = DEFAULT_BASE_URL,
    relay_dir: str = DEFAULT_SPOOL_DIR,
    channel: str = DEFAULT_CHANNEL,
    thread_id: str = DEFAULT_THREAD_ID,
    claim_key: str = DEFAULT_CLAIM_KEY,
    timeout_sec: float = 120.0,
    poll_interval_sec: float = 1.0,
) -> int:
    if role not in {"smoke-a", "smoke-b", "smoke-c"}:
        raise SmokeError(f"unknown role: {role}")
    transport = SmokeTransport(
        transport=transport_name,
        agent_id=role,
        base_url=base_url,
        relay_dir=relay_dir,
        timeout=max(5.0, min(timeout_sec, 30.0)),
    )

    _require_ok(transport.request("POST", "/v1/sessions", {
        "agent_id": role,
        "display_name": role,
        "replace": True,
    }), "open session")

    if role == "smoke-a":
        _require_ok(transport.request("POST", "/v1/channels", {
            "name": channel,
            "created_by": role,
        }), "create channel")
        _require_ok(transport.request("POST", "/v1/messages", {
            "from_agent": role,
            "channel": channel,
            "kind": "task",
            "body": "smoke task from smoke-a",
            "thread_id": thread_id,
        }), "post task")
        _wait_for_messages(
            transport,
            channel=channel,
            thread_id=thread_id,
            predicate=lambda messages: _contains_from(messages, "smoke-b", kind="artifact")
            and _contains_from(messages, "smoke-c", kind="notice", body_contains="verified"),
            timeout_sec=timeout_sec,
            poll_interval_sec=poll_interval_sec,
        )
        _require_ok(transport.request("POST", "/v1/messages", {
            "from_agent": role,
            "channel": channel,
            "kind": "notice",
            "body": "smoke-a saw both agents and the smoke test passed from the host side",
            "thread_id": thread_id,
        }), "post final notice")
        return 0

    if role == "smoke-b":
        _wait_for_messages(
            transport,
            channel=channel,
            thread_id=thread_id,
            predicate=lambda messages: _contains_from(messages, "smoke-a", kind="task"),
            timeout_sec=timeout_sec,
            poll_interval_sec=poll_interval_sec,
        )
        claim_resp = _require_ok(transport.request("POST", "/v1/claims", {
            "claim_key": claim_key,
            "owner_agent_id": role,
            "thread_id": thread_id,
        }), "acquire claim")
        if not claim_resp.get("acquired", False):
            raise SmokeError(f"claim denied to {role}")
        _require_ok(transport.request("POST", "/v1/messages", {
            "from_agent": role,
            "channel": channel,
            "kind": "artifact",
            "body": "smoke-b reached the hub through relay mode",
            "thread_id": thread_id,
        }), "post artifact")
        _require_ok(transport.request("POST", "/v1/claims/release", {
            "claim_key": claim_key,
            "agent_id": role,
        }), "release claim")
        _wait_for_messages(
            transport,
            channel=channel,
            thread_id=thread_id,
            predicate=lambda messages: _contains_from(messages, "smoke-c", kind="notice", body_contains="verified"),
            timeout_sec=timeout_sec,
            poll_interval_sec=poll_interval_sec,
        )
        _require_ok(transport.request("POST", "/v1/messages", {
            "from_agent": role,
            "channel": channel,
            "kind": "notice",
            "body": "smoke-b confirms relay interop works",
            "thread_id": thread_id,
        }), "post confirmation notice")
        return 0

    _wait_for_messages(
        transport,
        channel=channel,
        thread_id=thread_id,
        predicate=lambda messages: _contains_from(messages, "smoke-a", kind="task")
        and _contains_from(messages, "smoke-b", kind="artifact"),
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
    )
    claims_resp = _require_ok(transport.request("GET", f"/v1/claims?{urllib.parse.urlencode({'thread_id': thread_id, 'active_only': 'true'})}"), "list claims")
    active_claims = claims_resp.get("result", [])
    claim_state = "released"
    if active_claims:
        for claim in active_claims:
            if claim.get("claim_key") == claim_key:
                claim_state = f"held by {claim.get('owner_agent_id')}"
                break
    _require_ok(transport.request("POST", "/v1/messages", {
        "from_agent": role,
        "channel": channel,
        "kind": "notice",
        "body": f"smoke-c verified shared visibility across direct HTTP and relay mode ({claim_state})",
        "thread_id": thread_id,
    }), "post verifier notice")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a deterministic Megahub smoke-test role.")
    parser.add_argument("--role", required=True, choices=("smoke-a", "smoke-b", "smoke-c"))
    parser.add_argument("--transport", required=True, choices=("http", "relay"))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--relay-dir", default=DEFAULT_SPOOL_DIR)
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--thread-id", default=DEFAULT_THREAD_ID)
    parser.add_argument("--claim-key", default=DEFAULT_CLAIM_KEY)
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument("--poll-interval-sec", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_smoke_agent(
            role=args.role,
            transport_name=args.transport,
            base_url=args.base_url,
            relay_dir=args.relay_dir,
            channel=args.channel,
            thread_id=args.thread_id,
            claim_key=args.claim_key,
            timeout_sec=args.timeout_sec,
            poll_interval_sec=args.poll_interval_sec,
        )
    except SmokeError as exc:
        print(f"Smoke test failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
