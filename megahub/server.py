from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import HubConfig
from .dashboard import DASHBOARD_HTML
from .storage import HubStore

MESSAGE_KINDS = {"chat", "notice", "task", "claim", "release", "artifact"}
ATTACHMENT_TYPES = {"text", "json", "code", "file_ref", "diff_ref"}
INLINE_ATTACHMENT_TYPES = {"text", "json", "code"}
REFERENCE_ATTACHMENT_TYPES = {"file_ref", "diff_ref"}

_ROOT_RE = re.compile(r"^/$")
_SESSIONS_RE = re.compile(r"^/v1/sessions$")
_SESSION_RE = re.compile(r"^/v1/sessions/(?P<session_id>[^/]+)$")
_AGENTS_RE = re.compile(r"^/v1/agents$")
_CHANNELS_RE = re.compile(r"^/v1/channels$")
_HUB_INFO_RE = re.compile(r"^/v1/hub-info$")
_EVENTS_RE = re.compile(r"^/v1/events$")
_MESSAGES_RE = re.compile(r"^/v1/messages$")
_THREADS_RE = re.compile(r"^/v1/threads$")
_THREAD_RE = re.compile(r"^/v1/threads/(?P<thread_id>[^/]+)$")
_INBOX_RE = re.compile(r"^/v1/inbox/(?P<agent_id>[^/]+)$")
_CLAIMS_RE = re.compile(r"^/v1/claims$")
_CLAIMS_REFRESH_RE = re.compile(r"^/v1/claims/refresh$")
_CLAIMS_RELEASE_RE = re.compile(r"^/v1/claims/release$")
_LOCKS_RE = re.compile(r"^/v1/locks$")
_LOCKS_REFRESH_RE = re.compile(r"^/v1/locks/refresh$")
_LOCKS_RELEASE_RE = re.compile(r"^/v1/locks/release$")
_TASKS_RE = re.compile(r"^/v1/tasks$")
_TASK_COMPLETE_RE = re.compile(r"^/v1/tasks/(?P<task_id>\d+)/complete$")

PIDFILE_NAME = ".megahub.pid"


def _resolve_storage_dir(storage_path: str) -> Path:
    path = Path(storage_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve().parent


def _pidfile_url(host: str, port: int) -> str:
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        return f"http://[{host}]:{port}"
    return f"http://{host}:{port}"


def _pidfile_path(storage_path: str) -> Path:
    return _resolve_storage_dir(storage_path) / PIDFILE_NAME


def _instance_fingerprint(storage_path: Path, birth_marker: str) -> str:
    material = f"{storage_path.resolve()}|{birth_marker}".encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()[:20]
    return f"mh1-{digest}"


def _read_pidfile(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        pid = int(payload["pid"])
        port = int(payload["port"])
        url = str(payload["url"])
    except (KeyError, TypeError, ValueError):
        return None
    return {"pid": pid, "port": port, "url": url, "path": str(path)}


def _candidate_pidfiles(storage_path: str) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for root in (Path.cwd().resolve(), _resolve_storage_dir(storage_path)):
        for base in (root, *root.parents):
            candidate = base / PIDFILE_NAME
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    return candidates


def _discover_pidfile(storage_path: str) -> dict[str, Any] | None:
    for candidate in _candidate_pidfiles(storage_path):
        info = _read_pidfile(candidate)
        if info is not None:
            return info
    return None


class HubRuntime:
    def __init__(self, config: HubConfig):
        self.config = config
        self.store = HubStore(config.storage_path)
        self._prune_timer: threading.Timer | None = None
        self.bound_port = config.port
        self.pidfile_path = _pidfile_path(config.storage_path)
        self.instance_id = self._compute_instance_id()

    def log(self, message: str) -> None:
        if not self.config.log_events:
            return
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        print(f"[megahub {stamp}] {message}")

    def start(self) -> None:
        self._write_pidfile()
        hub_info = self.get_hub_info()
        self.log(
            f"listening on {self.config.listen_host}:{self.bound_port} "
            f"(storage={hub_info['storage_path']}, journal_mode={hub_info['journal_mode']}, "
            f"instance={self.instance_id}, allow_remote={self.config.allow_remote})"
        )
        if not hub_info["wal_mode"]:
            self.log(
                "warning: SQLite is not in WAL mode; shared-filesystem coordination may not behave "
                "correctly on this storage backend"
            )
        self._schedule_prune()

    def stop(self) -> None:
        if self._prune_timer:
            self._prune_timer.cancel()
        self._cleanup_pidfile()
        self.store.close()

    def _write_pidfile(self) -> None:
        payload = {
            "pid": os.getpid(),
            "port": self.bound_port,
            "url": _pidfile_url(self.config.listen_host, self.bound_port),
        }
        try:
            self.pidfile_path.parent.mkdir(parents=True, exist_ok=True)
            self.pidfile_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            self.log(f"pidfile write error: {exc}")

    def _cleanup_pidfile(self) -> None:
        info = _read_pidfile(self.pidfile_path)
        if info is None:
            return
        if info["pid"] != os.getpid() or info["port"] != self.bound_port:
            return
        try:
            self.pidfile_path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            self.log(f"pidfile cleanup error: {exc}")

    def _schedule_prune(self) -> None:
        interval = max(1, self.config.presence_ttl_sec // 3)
        self._prune_timer = threading.Timer(interval, self._prune_tick)
        self._prune_timer.daemon = True
        self._prune_timer.start()

    def _compute_instance_id(self) -> str:
        general = self.store.get_channel("general")
        birth_marker = str(general["created_at"]) if general and general.get("created_at") else "unknown"
        return _instance_fingerprint(self.store.db_path, birth_marker)

    def get_hub_info(self) -> dict[str, Any]:
        info = self.store.get_storage_info()
        return {
            "storage_path": info["storage_path"],
            "instance_id": self.instance_id,
            "journal_mode": info["journal_mode"],
            "wal_mode": info["wal_mode"],
        }

    def _prune_tick(self) -> None:
        try:
            expired = self.store.prune_expired_sessions(self.config.presence_ttl_sec)
            for session in expired:
                self.log(f"session expired: agent={session['agent_id']} session={session['session_id']}")
            if expired:
                self._recover_expired_work(expired)
        except Exception as exc:
            self.log(f"prune error: {exc}")
        self._schedule_prune()

    def _recover_expired_work(self, expired_sessions: list[dict[str, Any]]) -> None:
        for session in expired_sessions:
            agent_id = session["agent_id"]
            claims = self.store.list_claims(owner_agent_id=agent_id, active_only=True)
            for claim in claims:
                released = self.store.release_claim_force(claim["claim_key"])
                if released is None:
                    continue
                self._post_recovery_notice_for_claim(released, stale_agent_id=agent_id)
                self.log(
                    f"claim recovered: key={released['claim_key']} stale_owner={agent_id} "
                    f"thread={released.get('thread_id') or '-'}"
                )

            locks = self.store.list_locks(agent_id=agent_id, active_only=True)
            for lock in locks:
                released = self.store.release_lock_force(lock["file_path"])
                if released is None:
                    continue
                self._post_recovery_notice_for_lock(released, stale_agent_id=agent_id)
                self.log(
                    f"lock recovered: path={released['file_path']} stale_owner={agent_id} "
                    f"thread={((released.get('metadata') or {}).get('thread_id') or '-')}"
                )

    def _post_recovery_notice_for_claim(self, claim: dict[str, Any], *, stale_agent_id: str) -> None:
        thread_id = claim.get("thread_id")
        channel = "general"
        if thread_id:
            summary = self.store.get_thread_summary(thread_id)
            if summary and summary.get("channel"):
                channel = str(summary["channel"])

        task_message_id = claim.get("task_message_id")
        body = (
            f"Recovered stale claim {claim['claim_key']} from {stale_agent_id}. "
            "Work is available for pickup."
        )
        self.store.create_message(
            from_agent="system",
            to_agent=None,
            channel=channel,
            kind="notice",
            body=body,
            attachments=[],
            reply_to=task_message_id,
            thread_id=thread_id,
            metadata={
                "recovery": True,
                "stale_agent_id": stale_agent_id,
                "claim_key": claim["claim_key"],
                "task_message_id": task_message_id,
            },
        )

    def _post_recovery_notice_for_lock(self, lock: dict[str, Any], *, stale_agent_id: str) -> None:
        metadata = lock.get("metadata") or {}
        thread_id = metadata.get("thread_id")
        channel = str(metadata.get("channel") or "general")
        if thread_id:
            summary = self.store.get_thread_summary(str(thread_id))
            if summary and summary.get("channel"):
                channel = str(summary["channel"])
        body = (
            f"Recovered stale lock on {lock['file_path']} from {stale_agent_id}. "
            "The file is available for pickup."
        )
        self.store.create_message(
            from_agent="system",
            to_agent=None,
            channel=channel,
            kind="notice",
            body=body,
            attachments=[],
            reply_to=None,
            thread_id=None if thread_id is None else str(thread_id),
            metadata={
                "recovery": True,
                "stale_agent_id": stale_agent_id,
                "file_path": lock["file_path"],
            },
        )


def _parse_limit(params: dict[str, list[str]], max_limit: int) -> int:
    raw = params.get("limit", ["100"])[0]
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError("limit must be an integer") from exc
    if limit < 1:
        raise ValueError("limit must be at least 1")
    return min(limit, max_limit)


def _parse_since_id(params: dict[str, list[str]]) -> int:
    raw = params.get("since_id", ["0"])[0]
    try:
        since_id = int(raw)
    except ValueError as exc:
        raise ValueError("since_id must be an integer") from exc
    if since_id < 0:
        raise ValueError("since_id must be >= 0")
    return since_id


def _coerce_int(value: Any, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _max_request_bytes(config: HubConfig) -> int:
    attachment_budget = config.max_attachment_chars * config.max_attachments
    return config.max_body_chars + attachment_budget + 65_536


def _normalize_attachment(attachment: Any, *, max_attachment_chars: int) -> dict[str, Any]:
    if not isinstance(attachment, dict):
        raise ValueError("attachments must contain JSON objects")
    attachment_type = str(attachment.get("type", "")).strip()
    if attachment_type not in ATTACHMENT_TYPES:
        raise ValueError(f"unsupported attachment type: {attachment_type}")

    normalized: dict[str, Any] = {"type": attachment_type}
    if attachment_type in INLINE_ATTACHMENT_TYPES:
        if "content" not in attachment:
            raise ValueError(f"attachment type {attachment_type} requires content")
        content = attachment["content"]
        if len(json.dumps(content)) > max_attachment_chars:
            raise ValueError(f"attachment type {attachment_type} exceeds max size")
        normalized["content"] = content
        if attachment_type == "code":
            language = attachment.get("language")
            if language is not None:
                normalized["language"] = str(language)
    if attachment_type in REFERENCE_ATTACHMENT_TYPES:
        path = str(attachment.get("path", "")).strip()
        if not path:
            raise ValueError(f"attachment type {attachment_type} requires path")
        normalized["path"] = path
        for key in ("description", "base", "head"):
            if key in attachment and attachment[key] is not None:
                normalized[key] = attachment[key]
        for key in ("start_line", "end_line"):
            if key in attachment and attachment[key] is not None:
                normalized[key] = _coerce_int(attachment[key], field_name=key)
    return normalized


def _normalize_message_payload(payload: dict[str, Any], config: HubConfig) -> dict[str, Any]:
    from_agent = str(payload.get("from_agent", "")).strip()
    if not from_agent:
        raise ValueError("from_agent is required")

    to_agent = payload.get("to_agent")
    if to_agent is not None:
        to_agent = str(to_agent).strip() or None

    channel = payload.get("channel")
    if channel is None:
        channel = "direct" if to_agent else "general"
    channel = str(channel).strip()
    if not channel:
        raise ValueError("channel must be non-empty")

    kind = str(payload.get("kind", "chat")).strip().lower()
    if kind not in MESSAGE_KINDS:
        raise ValueError(f"unsupported kind: {kind}")

    body = str(payload.get("body", ""))
    attachments_raw = payload.get("attachments") or []
    if not isinstance(attachments_raw, list):
        raise ValueError("attachments must be a list")
    if len(attachments_raw) > config.max_attachments:
        raise ValueError("too many attachments")
    attachments = [
        _normalize_attachment(item, max_attachment_chars=config.max_attachment_chars)
        for item in attachments_raw
    ]

    if not body and not attachments:
        raise ValueError("body or attachments is required")
    if len(body) > config.max_body_chars:
        raise ValueError("body exceeds max size")

    reply_to = payload.get("reply_to")
    if reply_to is not None:
        reply_to = _coerce_int(reply_to, field_name="reply_to")

    thread_id = payload.get("thread_id")
    if thread_id is not None:
        thread_id = str(thread_id)

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")

    return {
        "from_agent": from_agent,
        "to_agent": to_agent,
        "channel": channel,
        "kind": kind,
        "body": body,
        "attachments": attachments,
        "reply_to": reply_to,
        "thread_id": thread_id,
        "metadata": metadata,
    }


class _HubHandler(BaseHTTPRequestHandler):
    server: HubHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        pass

    @property
    def runtime(self) -> HubRuntime:
        return self.server.runtime

    def _discard_body(self, length: int, *, limit: int | None = None) -> None:
        remaining = length if limit is None else min(length, limit)
        while remaining > 0:
            chunk = self.rfile.read(min(65_536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except (ValueError, TypeError) as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if length < 0:
            raise ValueError("Content-Length must be >= 0")
        max_request_bytes = _max_request_bytes(self.runtime.config)
        if length > max_request_bytes:
            self._discard_body(length, limit=max_request_bytes + 65_536)
            self.close_connection = True
            raise ValueError("request body exceeds max size")
        body = self.rfile.read(length) if length else b""
        try:
            decoded = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("request body must be valid UTF-8") from exc
        try:
            data = json.loads(decoded) if body else {}
        except json.JSONDecodeError as exc:
            raise ValueError("malformed JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Megahub-Instance", self.runtime.instance_id)
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _error(self, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": message}, status)

    def _parsed_url(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return parsed.path, parse_qs(parsed.query)

    def do_GET(self) -> None:
        path, params = self._parsed_url()
        rt = self.runtime

        if _ROOT_RE.match(path):
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("X-Megahub-Instance", self.runtime.instance_id)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if _AGENTS_RE.match(path):
            agents = rt.store.list_live_agents(rt.config.presence_ttl_sec)
            return self._send_json({"ok": True, "result": agents})

        if _CHANNELS_RE.match(path):
            return self._send_json({"ok": True, "result": rt.store.list_channels()})

        if _HUB_INFO_RE.match(path):
            return self._send_json({"ok": True, "result": rt.get_hub_info()})

        if _EVENTS_RE.match(path):
            agent_id = params.get("agent_id", [None])[0]
            channel = params.get("channel", [None])[0]
            thread_id = params.get("thread_id", [None])[0]
            if not agent_id:
                return self._error("agent_id query parameter is required")
            if channel and rt.store.get_channel(channel) is None:
                return self._error("channel not found", 404)
            try:
                since_id = _parse_since_id(params)
                limit = _parse_limit(params, rt.config.max_query_limit)
            except ValueError as exc:
                return self._error(str(exc))
            messages = rt.store.list_visible_messages_for_agent(
                agent_id,
                since_id=since_id,
                limit=limit,
                channel=channel,
                thread_id=thread_id,
            )
            return self._send_json({"ok": True, "result": messages})

        if _MESSAGES_RE.match(path):
            channel = params.get("channel", [None])[0]
            thread_id = params.get("thread_id", [None])[0]
            if not channel and not thread_id:
                return self._error("channel or thread_id query parameter is required")
            if channel and rt.store.get_channel(channel) is None:
                return self._error("channel not found", 404)
            try:
                since_id = _parse_since_id(params)
                limit = _parse_limit(params, rt.config.max_query_limit)
            except ValueError as exc:
                return self._error(str(exc))
            if thread_id:
                messages = rt.store.list_thread_messages(thread_id, channel=channel, since_id=since_id, limit=limit)
            else:
                messages = rt.store.list_channel_messages(channel, since_id=since_id, limit=limit)
            return self._send_json({"ok": True, "result": messages})

        if _THREADS_RE.match(path):
            return self._send_json({"ok": True, "result": rt.store.list_threads()})

        m = _THREAD_RE.match(path)
        if m:
            detail = rt.store.get_thread_detail(m.group("thread_id"))
            if detail is None:
                return self._error("thread not found", 404)
            return self._send_json({"ok": True, "result": detail})

        m = _INBOX_RE.match(path)
        if m:
            agent_id = m.group("agent_id")
            try:
                since_id = _parse_since_id(params)
                limit = _parse_limit(params, rt.config.max_query_limit)
            except ValueError as exc:
                return self._error(str(exc))
            messages = rt.store.list_inbox_messages(agent_id, since_id=since_id, limit=limit)
            return self._send_json({"ok": True, "result": messages})

        if _CLAIMS_RE.match(path):
            thread_id = params.get("thread_id", [None])[0]
            active_only = params.get("active_only", [""])[0].lower() in ("true", "1", "yes")
            claims = rt.store.list_claims(thread_id=thread_id, active_only=active_only)
            return self._send_json({"ok": True, "result": claims})

        if _LOCKS_RE.match(path):
            agent_id = params.get("agent_id", [None])[0]
            active_only = params.get("active_only", [""])[0].lower() in ("true", "1", "yes")
            locks = rt.store.list_locks(agent_id=agent_id, active_only=active_only)
            return self._send_json({"ok": True, "result": locks})

        if _TASKS_RE.match(path):
            parent_id_raw = params.get("parent_id", [None])[0]
            status = params.get("status", [None])[0]
            channel = params.get("channel", [None])[0]
            thread_id = params.get("thread_id", [None])[0]
            parent_id = None
            if parent_id_raw is not None:
                try:
                    parent_id = _coerce_int(parent_id_raw, field_name="parent_id")
                except ValueError as exc:
                    return self._error(str(exc))
            if status is not None and status not in ("open", "done"):
                return self._error("status must be 'open' or 'done'")
            tasks = rt.store.list_tasks(parent_id=parent_id, status=status, channel=channel, thread_id=thread_id)
            return self._send_json({"ok": True, "result": tasks})

        self._error("not found", 404)

    def do_POST(self) -> None:
        path, _params = self._parsed_url()
        rt = self.runtime

        if _SESSIONS_RE.match(path):
            return self._handle_create_session()
        if _CHANNELS_RE.match(path):
            return self._handle_create_channel()
        if _MESSAGES_RE.match(path):
            return self._handle_post_message()
        if _CLAIMS_REFRESH_RE.match(path):
            return self._handle_refresh_claim()
        if _CLAIMS_RELEASE_RE.match(path):
            return self._handle_release_claim()
        if _CLAIMS_RE.match(path):
            return self._handle_acquire_claim()
        if _LOCKS_REFRESH_RE.match(path):
            return self._handle_refresh_lock()
        if _LOCKS_RELEASE_RE.match(path):
            return self._handle_release_lock()
        if _LOCKS_RE.match(path):
            return self._handle_acquire_lock()
        m = _TASK_COMPLETE_RE.match(path)
        if m:
            return self._handle_complete_task(int(m.group("task_id")))

        self._error("not found", 404)

    def do_DELETE(self) -> None:
        path, _params = self._parsed_url()
        m = _SESSION_RE.match(path)
        if m:
            return self._handle_delete_session(m.group("session_id"))
        self._error("not found", 404)

    def _handle_create_session(self) -> None:
        rt = self.runtime
        try:
            payload = self._read_json()
            agent_id = str(payload.get("agent_id", "")).strip()
            if not agent_id:
                raise ValueError("agent_id is required")
            display_name = payload.get("display_name")
            capabilities = payload.get("capabilities") or []
            if not isinstance(capabilities, list):
                raise ValueError("capabilities must be a list")
            capabilities = [str(item) for item in capabilities]
            metadata = payload.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be a JSON object")
            replace = bool(payload.get("replace", False))
            session, deactivated = rt.store.create_session(
                agent_id=agent_id,
                display_name=None if display_name is None else str(display_name),
                capabilities=capabilities,
                metadata=metadata,
                replace=replace,
                ttl_sec=rt.config.presence_ttl_sec,
            )
        except ValueError as exc:
            message = str(exc)
            status = 409 if "already has an active session" in message else 400
            return self._error(message, status)

        for old in deactivated:
            rt.log(f"session replaced: agent={old['agent_id']} old_session={old['session_id']}")
        rt.log(
            f"session opened: agent={session['agent_id']} session={session['session_id']} "
            f"caps={','.join(session['capabilities']) or '-'}"
        )
        self._send_json({"ok": True, "result": session}, 201)

    def _handle_delete_session(self, session_id: str) -> None:
        rt = self.runtime
        session = rt.store.delete_session(session_id)
        if not session:
            return self._error("session not found", 404)
        rt.log(f"session closed: agent={session['agent_id']} session={session_id}")
        self._send_json({"ok": True, "result": {"session_id": session_id, "deleted": True}})

    def _handle_create_channel(self) -> None:
        rt = self.runtime
        try:
            payload = self._read_json()
            name = str(payload.get("name", "")).strip()
            if not name:
                raise ValueError("name is required")
            created_by = payload.get("created_by")
            if created_by is not None:
                created_by = str(created_by)
            metadata = payload.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be a JSON object")
            channel, created = rt.store.create_channel(name, created_by, metadata)
        except ValueError as exc:
            return self._error(str(exc))

        if created:
            rt.log(f"channel created: name={channel['name']} created_by={channel['created_by'] or 'unknown'}")
        self._send_json({"ok": True, "result": channel}, 201 if created else 200)

    def _handle_post_message(self) -> None:
        rt = self.runtime
        try:
            payload = self._read_json()
            parent_task_id = payload.pop("parent_task_id", None)
            if parent_task_id is not None:
                parent_task_id = _coerce_int(parent_task_id, field_name="parent_task_id")
            normalized = _normalize_message_payload(payload, rt.config)
            if normalized["to_agent"] is None and rt.store.get_channel(normalized["channel"]) is None:
                raise ValueError(f"channel does not exist: {normalized['channel']}")
            message = rt.store.create_message(**normalized)
        except ValueError as exc:
            return self._error(str(exc))

        if message["kind"] == "task":
            rt.store.create_task(
                message_id=message["id"],
                parent_task_id=parent_task_id,
                channel=message["channel"],
                thread_id=message["thread_id"],
            )

        rt.store.touch_agent_session(message["from_agent"])
        target = f"to={message['to_agent']}" if message["to_agent"] else f"channel={message['channel']}"
        rt.log(f"message posted: id={message['id']} kind={message['kind']} from={message['from_agent']} {target}")
        self._send_json({"ok": True, "result": message}, 201)

    def _handle_acquire_claim(self) -> None:
        rt = self.runtime
        try:
            payload = self._read_json()
            owner_agent_id = str(payload.get("owner_agent_id", "")).strip()
            if not owner_agent_id:
                raise ValueError("owner_agent_id is required")

            claim_key = payload.get("claim_key")
            task_message_id = payload.get("task_message_id")
            if task_message_id is not None:
                task_message_id = _coerce_int(task_message_id, field_name="task_message_id")

            if claim_key is not None:
                claim_key = str(claim_key).strip()
            if not claim_key:
                if task_message_id is not None:
                    claim_key = f"task-{task_message_id}"
                else:
                    raise ValueError("claim_key or task_message_id is required")

            thread_id = payload.get("thread_id")
            if thread_id is not None:
                thread_id = str(thread_id).strip() or None

            ttl_sec = _coerce_int(payload.get("ttl_sec", 300), field_name="ttl_sec")
            if ttl_sec < 5:
                raise ValueError("ttl_sec must be at least 5")

            metadata = payload.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be a JSON object")

            claim, acquired = rt.store.acquire_claim(
                claim_key=claim_key,
                thread_id=thread_id,
                task_message_id=task_message_id,
                owner_agent_id=owner_agent_id,
                ttl_sec=ttl_sec,
                metadata=metadata,
            )
        except ValueError as exc:
            return self._error(str(exc))

        rt.store.touch_agent_session(owner_agent_id)
        if acquired:
            rt.log(f"claim acquired: key={claim['claim_key']} owner={owner_agent_id} thread={claim.get('thread_id') or '-'}")
            self._send_json({"ok": True, "acquired": True, "result": claim}, 201)
        else:
            rt.log(f"claim denied: key={claim['claim_key']} requester={owner_agent_id} held_by={claim['owner_agent_id']}")
            self._send_json({"ok": True, "acquired": False, "result": claim}, 200)

    def _handle_release_claim(self) -> None:
        rt = self.runtime
        try:
            payload = self._read_json()
            claim_key = str(payload.get("claim_key", "")).strip()
            if not claim_key:
                raise ValueError("claim_key is required")
            agent_id = str(payload.get("agent_id", "")).strip()
            if not agent_id:
                raise ValueError("agent_id is required")
        except ValueError as exc:
            return self._error(str(exc))

        claim = rt.store.release_claim(claim_key, agent_id)
        if claim is None:
            return self._error("claim not found or not owned by agent_id", 404)

        rt.store.touch_agent_session(agent_id)
        rt.log(f"claim released: key={claim['claim_key']} owner={agent_id} thread={claim.get('thread_id') or '-'}")
        self._send_json({"ok": True, "result": claim})

    def _handle_refresh_claim(self) -> None:
        rt = self.runtime
        try:
            payload = self._read_json()
            claim_key = str(payload.get("claim_key", "")).strip()
            if not claim_key:
                raise ValueError("claim_key is required")
            owner_agent_id = str(payload.get("owner_agent_id", "")).strip()
            if not owner_agent_id:
                raise ValueError("owner_agent_id is required")
            ttl_sec = _coerce_int(payload.get("ttl_sec", 300), field_name="ttl_sec")
            if ttl_sec < 5:
                raise ValueError("ttl_sec must be at least 5")
        except ValueError as exc:
            return self._error(str(exc))

        claim = rt.store.refresh_claim(
            claim_key,
            owner_agent_id,
            ttl_sec=ttl_sec,
        )
        if claim is None:
            return self._error("claim not found or not owned by owner_agent_id", 404)

        rt.store.touch_agent_session(owner_agent_id)
        rt.log(f"claim refreshed: key={claim['claim_key']} owner={owner_agent_id}")
        self._send_json({"ok": True, "acquired": True, "result": claim})

    def _handle_complete_task(self, task_id: int) -> None:
        rt = self.runtime
        task = rt.store.complete_task(task_id)
        if task is None:
            return self._error("task not found", 404)

        rt.log(f"task completed: id={task_id}")
        result: dict[str, Any] = {"ok": True, "result": task}

        all_done = rt.store.check_parent_completion(task_id)
        if all_done is True and task["parent_task_id"] is not None:
            parent = rt.store.get_task(task["parent_task_id"])
            if parent and parent["status"] == "open":
                rt.store.complete_task(task["parent_task_id"])
                subtasks = rt.store.list_tasks(parent_id=task["parent_task_id"])
                rt.store.create_message(
                    from_agent="system",
                    to_agent=None,
                    channel=parent["channel"],
                    kind="notice",
                    body=f"All {len(subtasks)} subtasks of task {task['parent_task_id']} are complete.",
                    attachments=[],
                    reply_to=task["parent_task_id"],
                    thread_id=parent["thread_id"],
                    metadata={"auto_rollup": True, "parent_task_id": task["parent_task_id"]},
                )
                rt.log(f"parent task {task['parent_task_id']} auto-completed (all subtasks done)")
                result["parent_completed"] = True

        self._send_json(result)

    def _handle_acquire_lock(self) -> None:
        rt = self.runtime
        try:
            payload = self._read_json()
            agent_id = str(payload.get("agent_id", "")).strip()
            if not agent_id:
                raise ValueError("agent_id is required")
            file_path = str(payload.get("file_path", "")).strip()
            if not file_path:
                raise ValueError("file_path is required")

            ttl_sec = _coerce_int(payload.get("ttl_sec", 300), field_name="ttl_sec")
            if ttl_sec < 5:
                raise ValueError("ttl_sec must be at least 5")

            metadata = payload.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be a JSON object")

            lock, acquired = rt.store.acquire_lock(
                file_path=file_path,
                agent_id=agent_id,
                ttl_sec=ttl_sec,
                metadata=metadata,
            )
        except ValueError as exc:
            return self._error(str(exc))

        rt.store.touch_agent_session(agent_id)
        if acquired:
            rt.log(f"lock acquired: path={lock['file_path']} owner={agent_id}")
            self._send_json({"ok": True, "acquired": True, "result": lock}, 201)
        else:
            rt.log(f"lock denied: path={lock['file_path']} requester={agent_id} held_by={lock['agent_id']}")
            self._send_json({"ok": True, "acquired": False, "result": lock}, 200)

    def _handle_release_lock(self) -> None:
        rt = self.runtime
        try:
            payload = self._read_json()
            file_path = str(payload.get("file_path", "")).strip()
            if not file_path:
                raise ValueError("file_path is required")
            agent_id = str(payload.get("agent_id", "")).strip()
            if not agent_id:
                raise ValueError("agent_id is required")
        except ValueError as exc:
            return self._error(str(exc))

        lock = rt.store.release_lock(file_path, agent_id)
        if lock is None:
            return self._error("lock not found or not owned by agent_id", 404)

        rt.store.touch_agent_session(agent_id)
        rt.log(f"lock released: path={lock['file_path']} owner={agent_id}")
        self._send_json({"ok": True, "result": lock})

    def _handle_refresh_lock(self) -> None:
        rt = self.runtime
        try:
            payload = self._read_json()
            file_path = str(payload.get("file_path", "")).strip()
            if not file_path:
                raise ValueError("file_path is required")
            agent_id = str(payload.get("agent_id", "")).strip()
            if not agent_id:
                raise ValueError("agent_id is required")
            ttl_sec = _coerce_int(payload.get("ttl_sec", 300), field_name="ttl_sec")
            if ttl_sec < 5:
                raise ValueError("ttl_sec must be at least 5")
        except ValueError as exc:
            return self._error(str(exc))

        lock = rt.store.refresh_lock(
            file_path,
            agent_id,
            ttl_sec=ttl_sec,
        )
        if lock is None:
            return self._error("lock not found or not owned by agent_id", 404)

        rt.store.touch_agent_session(agent_id)
        rt.log(f"lock refreshed: path={lock['file_path']} owner={agent_id}")
        self._send_json({"ok": True, "acquired": True, "result": lock})


class HubHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def __init__(self, config: HubConfig):
        self.runtime = HubRuntime(config)
        super().__init__((config.listen_host, config.port), _HubHandler)
        self.runtime.bound_port = self.server_address[1]


def create_server(config: HubConfig | None = None) -> HubHTTPServer:
    cfg = config or HubConfig()
    cfg.validate()
    return HubHTTPServer(cfg)


def ensure_hub(
    host: str = "127.0.0.1",
    port: int = 8765,
    storage: str = "megahub.sqlite3",
    timeout: float = 5.0,
    quiet: bool = True,
) -> dict:
    """Check if a hub is running; if not, start one in the background.

    Returns a dict with keys: running (bool), started (bool), url (str).
    The port binding itself acts as the mutex — only one process can bind.
    """
    import subprocess
    import sys
    import time
    import urllib.error
    import urllib.request

    base = _pidfile_url(host, port)

    def _probe(url: str) -> bool:
        try:
            req = urllib.request.Request(f"{url}/v1/channels", method="GET")
            with urllib.request.urlopen(req, timeout=2):
                return True
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    pid_info = _discover_pidfile(storage)
    if pid_info is not None and _probe(pid_info["url"]):
        return {"running": True, "started": False, "url": pid_info["url"]}

    if _probe(base):
        return {"running": True, "started": False, "url": base}

    args = [sys.executable, "-m", "megahub", "serve",
            "--host", host, "--port", str(port), "--storage", storage]
    if quiet:
        args.append("--quiet-events")

    kwargs: dict = {"start_new_session": True}
    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
    except OSError:
        return {"running": False, "started": False, "url": base, "error": "failed to spawn"}

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.15)
        if _probe(base):
            return {"running": True, "started": True, "url": base}

    return {"running": False, "started": True, "url": base, "error": "timeout waiting for hub"}


def run_server(config: HubConfig | None = None) -> None:
    cfg = config or HubConfig()
    cfg.validate()
    if cfg.allow_remote:
        print(
            "[megahub] Warning: allow_remote=true exposes this daemon to non-local clients. "
            "There is no built-in auth in v1."
        )
    server = create_server(cfg)
    server.runtime.start()
    try:
        server.serve_forever()
    finally:
        server.runtime.stop()
        server.server_close()
