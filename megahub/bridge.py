from __future__ import annotations

import json
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from .client import MegahubClient
from .examples.round_robin_handler import build_round_reply
from .examples.thread_reply_handler import build_reply


@dataclass(slots=True)
class BridgeConfig:
    base_url: str
    agent_id: str
    display_name: str | None = None
    capabilities: list[str] | None = None
    metadata: dict[str, Any] | None = None
    replace: bool = True
    since_id: int = 0
    channels: set[str] | None = None
    thread_ids: set[str] | None = None
    ignore_self: bool = True
    handler_command: str | None = None
    builtin_handler: str | None = None
    agent_name: str | None = None
    handler_style: str = "ack"
    poll_interval_sec: float = 1.0
    refresh_every_sec: float | None = None
    use_events: bool = False


def message_matches(
    msg: dict[str, Any],
    *,
    agent_id: str,
    channels: set[str] | None,
    thread_ids: set[str] | None,
    ignore_self: bool,
) -> bool:
    if ignore_self and msg.get("from_agent") == agent_id:
        return False
    if channels and msg.get("channel") not in channels:
        return False
    if thread_ids and msg.get("thread_id") not in thread_ids:
        return False
    return True


def normalize_handler_output(
    raw_stdout: str,
    *,
    agent_id: str,
    default_channel: str | None,
    default_thread_id: str | None,
    default_reply_to: int | None,
) -> list[dict[str, Any]]:
    text = (raw_stdout or "").strip()
    if not text:
        return []

    parsed = json.loads(text)
    if isinstance(parsed, dict):
        items = [parsed]
    elif isinstance(parsed, list):
        items = parsed
    else:
        raise ValueError("handler output must be a JSON object or list")

    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each handler output item must be a JSON object")
        payload = dict(item)
        payload.setdefault("from_agent", agent_id)
        if "to_agent" not in payload:
            payload.setdefault("channel", default_channel or "general")
        payload.setdefault("thread_id", default_thread_id)
        if default_reply_to is not None:
            payload.setdefault("reply_to", default_reply_to)
        normalized.append(payload)
    return normalized


class MegahubBridge:
    def __init__(self, config: BridgeConfig):
        self.config = config
        self._stopped = False
        self.last_seen_id = config.since_id
        self._held_claims: set[str] = set()
        self._held_locks: set[str] = set()
        self._last_refresh: float = 0.0

    def request_stop(self) -> None:
        self._stopped = True

    def run(self) -> None:
        for signame in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, signame, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, lambda *_: self.request_stop())
            except (OSError, ValueError):
                pass

        client = MegahubClient(self.config.base_url)
        session_resp = client.open_session(
            self.config.agent_id,
            display_name=self.config.display_name,
            capabilities=self.config.capabilities or [],
            metadata=self.config.metadata or {},
            replace=self.config.replace,
        )
        if not session_resp.get("ok"):
            raise RuntimeError(f"failed to open session: {session_resp}")
        session = session_resp["result"]
        session_id = session["session_id"]
        print(json.dumps({"event": "bridge_session_open", "data": session}, indent=2))

        try:
            self._poll_loop(client)
        finally:
            close_resp = client.close_session(session_id)
            print(json.dumps({"event": "bridge_session_close", "data": close_resp}, indent=2))

    def _poll_loop(self, client: MegahubClient) -> None:
        channels = list(self.config.channels) if self.config.channels else None
        while not self._stopped:
            try:
                if self.config.use_events:
                    self._poll_events(client)
                elif channels:
                    for ch in channels:
                        resp = client.get_messages(ch, since_id=self.last_seen_id)
                        if resp.get("ok"):
                            self._process_messages(client, resp["result"])
                else:
                    resp = client.get_messages("general", since_id=self.last_seen_id)
                    if resp.get("ok"):
                        self._process_messages(client, resp["result"])
            except Exception as exc:
                print(json.dumps({"event": "bridge_error", "data": {"message": str(exc)}}))
            self._maybe_refresh(client)
            time.sleep(self.config.poll_interval_sec)

    def _poll_events(self, client: MegahubClient) -> None:
        """Poll /v1/events for all visible messages (broadcast + direct)."""
        channel = None
        thread_id = None
        if self.config.channels and len(self.config.channels) == 1:
            channel = next(iter(self.config.channels))
        if self.config.thread_ids and len(self.config.thread_ids) == 1:
            thread_id = next(iter(self.config.thread_ids))
        resp = client.get_events(
            self.config.agent_id,
            since_id=self.last_seen_id,
            channel=channel,
            thread_id=thread_id,
        )
        if resp.get("ok"):
            self._process_messages(client, resp["result"])

    def _maybe_refresh(self, client: MegahubClient) -> None:
        """Refresh held claims and locks if refresh_every_sec is configured."""
        if self.config.refresh_every_sec is None:
            return
        now = time.monotonic()
        if now - self._last_refresh < self.config.refresh_every_sec:
            return
        self._last_refresh = now

        for claim_key in list(self._held_claims):
            try:
                resp = client.refresh_claim(claim_key, self.config.agent_id)
                if resp.get("ok"):
                    print(json.dumps({"event": "bridge_refresh_claim", "data": {"claim_key": claim_key}}))
                else:
                    self._held_claims.discard(claim_key)
                    print(json.dumps({"event": "bridge_refresh_claim_failed", "data": {"claim_key": claim_key, "error": resp.get("error")}}))
            except Exception as exc:
                print(json.dumps({"event": "bridge_refresh_error", "data": {"claim_key": claim_key, "message": str(exc)}}))

        for file_path in list(self._held_locks):
            try:
                resp = client.refresh_lock(file_path, self.config.agent_id)
                if resp.get("ok"):
                    print(json.dumps({"event": "bridge_refresh_lock", "data": {"file_path": file_path}}))
                else:
                    self._held_locks.discard(file_path)
                    print(json.dumps({"event": "bridge_refresh_lock_failed", "data": {"file_path": file_path, "error": resp.get("error")}}))
            except Exception as exc:
                print(json.dumps({"event": "bridge_refresh_error", "data": {"file_path": file_path, "message": str(exc)}}))

    def track_claim(self, claim_key: str) -> None:
        """Register a claim for automatic refresh."""
        self._held_claims.add(claim_key)

    def untrack_claim(self, claim_key: str) -> None:
        """Stop refreshing a claim."""
        self._held_claims.discard(claim_key)

    def track_lock(self, file_path: str) -> None:
        """Register a lock for automatic refresh."""
        self._held_locks.add(file_path)

    def untrack_lock(self, file_path: str) -> None:
        """Stop refreshing a lock."""
        self._held_locks.discard(file_path)

    def _process_messages(self, client: MegahubClient, messages: list[dict[str, Any]]) -> None:
        for msg in messages:
            msg_id = msg.get("id")
            if isinstance(msg_id, int):
                self.last_seen_id = max(self.last_seen_id, msg_id)

            # Auto-track claims and locks from our own agent's messages
            if self.config.refresh_every_sec is not None:
                self._auto_track_from_message(msg)

            if not message_matches(
                msg,
                agent_id=self.config.agent_id,
                channels=self.config.channels,
                thread_ids=self.config.thread_ids,
                ignore_self=self.config.ignore_self,
            ):
                continue

            print(json.dumps({"event": "bridge_match", "data": msg}, indent=2))
            if not self.config.handler_command and not self.config.builtin_handler:
                continue

            try:
                if self.config.builtin_handler:
                    responses = self._invoke_builtin_handler(msg)
                else:
                    responses = self._invoke_handler(msg)
            except Exception as exc:
                print(json.dumps({
                    "event": "bridge_handler_error",
                    "data": {"message": str(exc), "trigger_id": msg.get("id")},
                }, indent=2))
                continue

            for response in responses:
                send_resp = client.send_message(response)
                print(json.dumps({"event": "bridge_send", "data": send_resp}, indent=2))

    def _auto_track_from_message(self, msg: dict[str, Any]) -> None:
        """Infer claim/lock ownership from message traffic and auto-track for refresh."""
        if msg.get("from_agent") != self.config.agent_id:
            return
        kind = msg.get("kind", "")
        metadata = msg.get("metadata") or {}

        if kind == "claim":
            # Agent posted a claim message — extract the claim key from metadata
            claim_key = metadata.get("claim_key")
            if claim_key:
                self.track_claim(claim_key)

        elif kind == "release":
            claim_key = metadata.get("claim_key")
            if claim_key:
                self.untrack_claim(claim_key)

    def acquire_and_track_claim(
        self,
        client: MegahubClient,
        *,
        claim_key: str | None = None,
        task_message_id: int | None = None,
        thread_id: str | None = None,
        ttl_sec: int = 300,
    ) -> dict[str, Any]:
        """Acquire a claim and auto-register it for refresh. Convenience method for handlers."""
        resp = client.acquire_claim(
            self.config.agent_id,
            claim_key=claim_key,
            task_message_id=task_message_id,
            thread_id=thread_id,
            ttl_sec=ttl_sec,
        )
        if resp.get("ok") and resp.get("acquired"):
            key = resp.get("result", {}).get("claim_key")
            if key:
                self.track_claim(key)
        return resp

    def release_and_untrack_claim(
        self,
        client: MegahubClient,
        claim_key: str,
    ) -> dict[str, Any]:
        """Release a claim and stop refreshing it."""
        resp = client.release_claim(claim_key, self.config.agent_id)
        self.untrack_claim(claim_key)
        return resp

    def acquire_and_track_lock(
        self,
        client: MegahubClient,
        file_path: str,
        *,
        ttl_sec: int = 300,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Acquire a lock and auto-register it for refresh."""
        resp = client.acquire_lock(
            self.config.agent_id,
            file_path,
            ttl_sec=ttl_sec,
            metadata=metadata,
        )
        if resp.get("ok") and resp.get("acquired"):
            self.track_lock(file_path)
        return resp

    def release_and_untrack_lock(
        self,
        client: MegahubClient,
        file_path: str,
    ) -> dict[str, Any]:
        """Release a lock and stop refreshing it."""
        resp = client.release_lock(file_path, self.config.agent_id)
        self.untrack_lock(file_path)
        return resp

    def _invoke_handler(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            args = shlex.split(self.config.handler_command)
        except ValueError as exc:
            raise RuntimeError(f"invalid handler command syntax: {exc}") from exc
        try:
            proc = subprocess.run(
                args,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("handler timed out after 60 seconds")
        except FileNotFoundError:
            raise RuntimeError(f"handler command not found: {args[0]}")
        if proc.returncode != 0:
            raise RuntimeError(f"handler exited with code {proc.returncode}: {(proc.stderr or '').strip()}")
        try:
            return normalize_handler_output(
                proc.stdout,
                agent_id=self.config.agent_id,
                default_channel=payload.get("channel"),
                default_thread_id=payload.get("thread_id"),
                default_reply_to=payload.get("id"),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"handler produced invalid output: {exc}") from exc

    def _invoke_builtin_handler(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        agent_name = self.config.agent_name or self.config.agent_id
        if self.config.builtin_handler == "thread-reply":
            responses = [build_reply(
                payload,
                agent_name=agent_name,
                style=self.config.handler_style if self.config.handler_style in {"ack", "review"} else "ack",
            )]
            return self._normalize_builtin_outputs(payload, responses)
        if self.config.builtin_handler == "round-robin":
            reply = build_round_reply(
                payload,
                agent_id=self.config.agent_id,
                agent_name=agent_name,
                mode=self.config.handler_style if self.config.handler_style in {"concise", "review"} else "concise",
            )
            responses = [] if reply is None else [reply]
            return self._normalize_builtin_outputs(payload, responses)
        raise RuntimeError(f"unknown builtin handler: {self.config.builtin_handler}")

    def _normalize_builtin_outputs(
        self, payload: dict[str, Any], responses: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for response in responses:
            normalized.extend(normalize_handler_output(
                json.dumps(response),
                agent_id=self.config.agent_id,
                default_channel=payload.get("channel"),
                default_thread_id=payload.get("thread_id"),
                default_reply_to=payload.get("id"),
            ))
        return normalized


def run_bridge(config: BridgeConfig) -> None:
    bridge = MegahubBridge(config)
    bridge.run()
