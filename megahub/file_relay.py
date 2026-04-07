from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SPOOL_DIR = ".megahub-relay"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_agent_id(agent_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(agent_id or "").strip())
    return cleaned or "agent"


def _spool_root(spool_dir: str | Path) -> Path:
    # Keep spool paths lexical instead of calling resolve(). Some sandbox
    # harnesses expose the shared workspace through odd mount paths, and
    # resolve() can turn a simple relative spool dir into an unusable
    # host-looking path string. We only need a stable absolute location.
    path = Path(spool_dir).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _requests_root(spool_dir: str | Path) -> Path:
    return _spool_root(spool_dir) / "requests"


def _responses_root(spool_dir: str | Path) -> Path:
    return _spool_root(spool_dir) / "responses"


def _agent_requests_dir(spool_dir: str | Path, agent_id: str) -> Path:
    return _requests_root(spool_dir) / _safe_agent_id(agent_id)


def _agent_responses_dir(spool_dir: str | Path, agent_id: str) -> Path:
    return _responses_root(spool_dir) / _safe_agent_id(agent_id)


def ensure_spool_dirs(spool_dir: str | Path, *, agent_id: str | None = None) -> Path:
    root = _spool_root(spool_dir)
    _requests_root(root).mkdir(parents=True, exist_ok=True)
    _responses_root(root).mkdir(parents=True, exist_ok=True)
    if agent_id is not None:
        _agent_requests_dir(root, agent_id).mkdir(parents=True, exist_ok=True)
        _agent_responses_dir(root, agent_id).mkdir(parents=True, exist_ok=True)
    return root


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _error_response(request_id: str, status: int, error: str, *, body: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "ok": False,
        "status": status,
        "completed_at": _iso_now(),
        "error": error,
    }
    if body is not None:
        payload["body"] = body
    return payload


def _validate_request_envelope(payload: Any, *, fallback_request_id: str, fallback_agent_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("request envelope must be a JSON object")
    request_id = str(payload.get("request_id") or fallback_request_id).strip() or fallback_request_id
    agent_id = str(payload.get("agent_id") or fallback_agent_id).strip() or fallback_agent_id
    method = str(payload.get("method") or "").upper()
    path = str(payload.get("path") or "")
    body = payload.get("body")
    if not method:
        raise ValueError("request envelope missing method")
    if not path.startswith("/"):
        raise ValueError("request path must start with '/'")
    if body is not None and not isinstance(body, dict):
        raise ValueError("request body must be a JSON object when provided")
    return {
        "request_id": request_id,
        "agent_id": agent_id,
        "method": method,
        "path": path,
        "body": body,
        "created_at": payload.get("created_at") or _iso_now(),
    }


def _forward_http(base_url: str, method: str, path: str, body: dict[str, Any] | None, timeout: float) -> tuple[int, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            raw = response.read().decode("utf-8")
            try:
                return status, json.loads(raw)
            except json.JSONDecodeError:
                return status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return 599, {"ok": False, "error": f"connection error: {exc}"}


@dataclass(slots=True)
class FileRelayConfig:
    base_url: str = "http://127.0.0.1:8765"
    spool_dir: str = DEFAULT_SPOOL_DIR
    poll_interval_sec: float = 0.25
    request_timeout_sec: float = 30.0


class FileRelayClient:
    def __init__(
        self,
        *,
        agent_id: str,
        spool_dir: str = DEFAULT_SPOOL_DIR,
        timeout: float = 30.0,
        poll_interval_sec: float = 0.1,
    ):
        self.agent_id = agent_id
        self.spool_dir = spool_dir
        self.timeout = timeout
        self.poll_interval_sec = poll_interval_sec

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        try:
            ensure_spool_dirs(self.spool_dir, agent_id=self.agent_id)
            request_path = _agent_requests_dir(self.spool_dir, self.agent_id) / f"{request_id}.json"
            response_path = _agent_responses_dir(self.spool_dir, self.agent_id) / f"{request_id}.json"
            request_payload = {
                "request_id": request_id,
                "agent_id": self.agent_id,
                "method": method.upper(),
                "path": path,
                "body": body,
                "created_at": _iso_now(),
            }
            _atomic_write_json(request_path, request_payload)
        except OSError as exc:
            return _error_response(
                request_id,
                597,
                f"relay spool write failed: {exc}",
            )

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if response_path.exists():
                payload = _load_json_file(response_path)
                if not isinstance(payload, dict):
                    return _error_response(request_id, 500, "relay produced invalid response payload", body=payload)
                return payload
            time.sleep(self.poll_interval_sec)
        return _error_response(request_id, 598, "relay timed out waiting for response")


class FileRelayServer:
    def __init__(self, config: FileRelayConfig):
        self.config = config
        self._stopped = False

    def request_stop(self) -> None:
        self._stopped = True

    def run(self) -> None:
        ensure_spool_dirs(self.config.spool_dir)
        while not self._stopped:
            self.process_once()
            time.sleep(self.config.poll_interval_sec)

    def process_once(self) -> int:
        processed = 0
        for request_path in sorted(_requests_root(self.config.spool_dir).glob("*/*.json")):
            processed += 1 if self._process_request_file(request_path) else 0
        return processed

    def _process_request_file(self, request_path: Path) -> bool:
        work_path = request_path.with_suffix(".work")
        try:
            request_path.replace(work_path)
        except FileNotFoundError:
            return False
        except OSError:
            return False

        agent_id = request_path.parent.name
        request_id = request_path.stem
        response_path = _agent_responses_dir(self.config.spool_dir, agent_id) / f"{request_id}.json"

        try:
            try:
                raw_payload = _load_json_file(work_path)
                payload = _validate_request_envelope(
                    raw_payload,
                    fallback_request_id=request_id,
                    fallback_agent_id=agent_id,
                )
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                _atomic_write_json(response_path, _error_response(request_id, 400, f"invalid relay request: {exc}"))
                return True

            status, body = _forward_http(
                self.config.base_url,
                payload["method"],
                payload["path"],
                payload["body"],
                self.config.request_timeout_sec,
            )
            ok = False
            error: str | None = None
            if isinstance(body, dict):
                ok = bool(body.get("ok", False))
                error = body.get("error") if isinstance(body.get("error"), str) else None
            elif 200 <= status < 300:
                ok = True
            else:
                error = str(body)

            response_payload: dict[str, Any] = {
                "request_id": payload["request_id"],
                "ok": ok,
                "status": status,
                "body": body,
                "completed_at": _iso_now(),
            }
            if error:
                response_payload["error"] = error
            _atomic_write_json(response_path, response_payload)
            return True
        finally:
            # Keep processed .work files on disk. Some sandbox mounts refuse
            # deletes even when reads and renames succeed. The relay spool is
            # intentionally append-only; callers can clean it explicitly later.
            pass


def run_file_relay(config: FileRelayConfig) -> None:
    FileRelayServer(config).run()
