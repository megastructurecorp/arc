"""Lightweight helper for making Megahub API calls from the command line.

Supports direct HTTP mode and sandbox-friendly relay mode.

Usage:
  py _hub.py METHOD PATH [BODY_FILE] [--transport http|relay]

Environment variables:
  MEGAHUB_TRANSPORT=http|relay
  MEGAHUB_BASE_URL=http://127.0.0.1:8765
  MEGAHUB_RELAY_DIR=.megahub-relay
  MEGAHUB_AGENT_ID=my-agent
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from megahub.file_relay import DEFAULT_SPOOL_DIR, FileRelayClient

DEFAULT_BASE_URL = "http://127.0.0.1:8765"


def _load_body(src: str | None) -> dict[str, Any] | None:
    if not src:
        return None
    if src == "-":
        payload = json.load(sys.stdin)
    else:
        with open(src, encoding="utf-8") as handle:
            payload = json.load(handle)
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("body payload must be a JSON object")
    return payload


def _call_http(base_url: str, method: str, path: str, body: dict[str, Any] | None, timeout: float) -> int:
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            print(raw)
            return 0
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        print(raw, file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


def _call_relay(spool_dir: str, agent_id: str, method: str, path: str, body: dict[str, Any] | None, timeout: float) -> int:
    client = FileRelayClient(agent_id=agent_id, spool_dir=spool_dir, timeout=timeout)
    result = client.call(method, path, body)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Megahub CLI helper")
    parser.add_argument("method", help="HTTP method (GET, POST, DELETE, ...)")
    parser.add_argument("path", help="API path (e.g. /v1/channels)")
    parser.add_argument("body_file", nargs="?", default=None, help="JSON body file (or - for stdin)")
    parser.add_argument("--transport", choices=["http", "relay"], default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    transport = args.transport or os.environ.get("MEGAHUB_TRANSPORT", "http")
    body = _load_body(args.body_file)

    if transport == "relay":
        spool_dir = os.environ.get("MEGAHUB_RELAY_DIR", DEFAULT_SPOOL_DIR)
        agent_id = os.environ.get("MEGAHUB_AGENT_ID", "agent")
        return _call_relay(spool_dir, agent_id, args.method, args.path, body, args.timeout)
    else:
        base_url = os.environ.get("MEGAHUB_BASE_URL", DEFAULT_BASE_URL)
        return _call_http(base_url, args.method, args.path, body, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
