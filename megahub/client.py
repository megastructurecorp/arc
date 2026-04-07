from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class MegahubError(Exception):
    """Raised by MegahubClient when raise_on_error=True and the hub returns ok=False."""

    def __init__(self, message: str, status: int | None = None, response: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.response = response or {}


class MegahubClient:
    """Synchronous Python client for a megahub server. Zero external dependencies.

    By default, all methods return the raw JSON dict from the hub (callers check
    ``resp["ok"]``).  Pass ``raise_on_error=True`` to the constructor to raise
    :class:`MegahubError` on non-ok responses instead.
    """

    DEFAULT_TIMEOUT: float = 30.0

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float | None = None,
        raise_on_error: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        self.raise_on_error = raise_on_error
        self.last_instance_id: str | None = None
        self.last_response_headers: dict[str, str] = {}

    def __enter__(self) -> MegahubClient:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        pass

    @staticmethod
    def wait_for_hub(
        base_url: str = "http://127.0.0.1:8765",
        *,
        timeout: float = 10.0,
        poll_interval: float = 0.25,
    ) -> bool:
        """Block until the hub is reachable or *timeout* seconds elapse.

        Returns True if the hub responded, False on timeout.  Useful as a
        bootstrap helper after starting the hub in the background.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                req = urllib.request.Request(f"{base_url.rstrip('/')}/v1/channels", method="GET")
                with urllib.request.urlopen(req, timeout=2):
                    return True
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_interval, remaining))
        return False

    def open_session(
        self,
        agent_id: str,
        *,
        display_name: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Register a session for *agent_id*. Use ``replace=True`` to recover from restarts."""
        return self._json("POST", "/v1/sessions", {
            "agent_id": agent_id,
            "display_name": display_name,
            "capabilities": capabilities or [],
            "metadata": metadata or {},
            "replace": replace,
        })

    def close_session(self, session_id: str) -> dict[str, Any]:
        """Deactivate the session identified by *session_id*."""
        return self._json("DELETE", f"/v1/sessions/{session_id}")

    def list_agents(self) -> dict[str, Any]:
        """Return all agents with active, non-expired sessions."""
        return self._json("GET", "/v1/agents")

    def list_channels(self) -> dict[str, Any]:
        """Return all channels on the hub."""
        return self._json("GET", "/v1/channels")

    def get_hub_info(self) -> dict[str, Any]:
        """Return storage and instance metadata for the hub process behind this base URL."""
        return self._json("GET", "/v1/hub-info")

    def create_channel(
        self,
        name: str,
        *,
        created_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a channel (idempotent — returns existing channel if name already taken)."""
        return self._json("POST", "/v1/channels", {
            "name": name, "created_by": created_by, "metadata": metadata or {},
        })

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Post a message. *payload* must include at least ``from_agent`` and ``body``."""
        return self._json("POST", "/v1/messages", payload)

    def get_messages(
        self,
        channel: str | None = None,
        *,
        thread_id: str | None = None,
        since_id: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Query channel or thread messages. At least one of *channel* / *thread_id* required."""
        params: dict[str, str] = {"since_id": str(since_id), "limit": str(limit)}
        if channel:
            params["channel"] = channel
        if thread_id:
            params["thread_id"] = thread_id
        return self._json("GET", f"/v1/messages?{urllib.parse.urlencode(params)}")

    def list_threads(self) -> dict[str, Any]:
        """Return thread summaries for every known thread."""
        return self._json("GET", "/v1/threads")

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        """Return detail for a single thread, including messages, tasks, claims, and locks."""
        encoded_id = urllib.parse.quote(thread_id, safe="")
        return self._json("GET", f"/v1/threads/{encoded_id}")

    def get_events(
        self,
        agent_id: str,
        *,
        channel: str | None = None,
        thread_id: str | None = None,
        since_id: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return broadcast and direct messages visible to *agent_id*."""
        params: dict[str, str] = {
            "agent_id": agent_id,
            "since_id": str(since_id),
            "limit": str(limit),
        }
        if channel:
            params["channel"] = channel
        if thread_id:
            params["thread_id"] = thread_id
        return self._json("GET", f"/v1/events?{urllib.parse.urlencode(params)}")

    def get_inbox(self, agent_id: str, *, since_id: int = 0, limit: int = 100) -> dict[str, Any]:
        """Return direct messages addressed to *agent_id*."""
        encoded_id = urllib.parse.quote(agent_id, safe="")
        params = urllib.parse.urlencode({"since_id": str(since_id), "limit": str(limit)})
        return self._json("GET", f"/v1/inbox/{encoded_id}?{params}")

    def acquire_claim(
        self,
        owner_agent_id: str,
        *,
        claim_key: str | None = None,
        task_message_id: int | None = None,
        thread_id: str | None = None,
        ttl_sec: int = 300,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Acquire (or refresh) a claim. Returns ``acquired: true/false`` in the response."""
        payload: dict[str, Any] = {"owner_agent_id": owner_agent_id}
        if claim_key is not None:
            payload["claim_key"] = claim_key
        if task_message_id is not None:
            payload["task_message_id"] = task_message_id
        if thread_id is not None:
            payload["thread_id"] = thread_id
        if ttl_sec != 300:
            payload["ttl_sec"] = ttl_sec
        if metadata:
            payload["metadata"] = metadata
        return self._json("POST", "/v1/claims", payload)

    def refresh_claim(
        self,
        claim_key: str,
        owner_agent_id: str,
        *,
        ttl_sec: int = 300,
    ) -> dict[str, Any]:
        """Extend the TTL on an existing claim held by *owner_agent_id*."""
        return self._json("POST", "/v1/claims/refresh", {
            "claim_key": claim_key,
            "owner_agent_id": owner_agent_id,
            "ttl_sec": ttl_sec,
        })

    def release_claim(self, claim_key: str, agent_id: str) -> dict[str, Any]:
        """Release a claim. Only the owner can release."""
        return self._json("POST", "/v1/claims/release", {
            "claim_key": claim_key, "agent_id": agent_id,
        })

    def list_claims(
        self,
        *,
        thread_id: str | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        """List claims, optionally filtered by *thread_id* and/or *active_only*."""
        params: dict[str, str] = {}
        if thread_id:
            params["thread_id"] = thread_id
        if active_only:
            params["active_only"] = "true"
        qs = f"?{urllib.parse.urlencode(params)}" if params else ""
        return self._json("GET", f"/v1/claims{qs}")

    def acquire_lock(
        self,
        agent_id: str,
        file_path: str,
        *,
        ttl_sec: int = 300,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Acquire a file lock. Returns ``acquired: true/false`` in the response."""
        payload: dict[str, Any] = {"agent_id": agent_id, "file_path": file_path}
        if ttl_sec != 300:
            payload["ttl_sec"] = ttl_sec
        if metadata:
            payload["metadata"] = metadata
        return self._json("POST", "/v1/locks", payload)

    def release_lock(self, file_path: str, agent_id: str) -> dict[str, Any]:
        """Release a file lock. Only the holder can release."""
        return self._json("POST", "/v1/locks/release", {
            "file_path": file_path, "agent_id": agent_id,
        })

    def refresh_lock(self, file_path: str, agent_id: str, *, ttl_sec: int = 300) -> dict[str, Any]:
        """Extend the TTL on an existing lock held by *agent_id*."""
        return self._json("POST", "/v1/locks/refresh", {
            "file_path": file_path,
            "agent_id": agent_id,
            "ttl_sec": ttl_sec,
        })

    def list_locks(
        self,
        *,
        agent_id: str | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        """List file locks, optionally filtered by *agent_id* and/or *active_only*."""
        params: dict[str, str] = {}
        if agent_id:
            params["agent_id"] = agent_id
        if active_only:
            params["active_only"] = "true"
        qs = f"?{urllib.parse.urlencode(params)}" if params else ""
        return self._json("GET", f"/v1/locks{qs}")

    def list_tasks(
        self,
        *,
        parent_id: int | None = None,
        status: str | None = None,
        channel: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """List tasks with optional filters for parent_id, status, channel, and thread_id."""
        params: dict[str, str] = {}
        if parent_id is not None:
            params["parent_id"] = str(parent_id)
        if status:
            params["status"] = status
        if channel:
            params["channel"] = channel
        if thread_id:
            params["thread_id"] = thread_id
        qs = f"?{urllib.parse.urlencode(params)}" if params else ""
        return self._json("GET", f"/v1/tasks{qs}")

    def complete_task(self, task_id: int) -> dict[str, Any]:
        """Mark a task as done. Triggers completion rollup if all subtasks are done."""
        return self._json("POST", f"/v1/tasks/{task_id}/complete")

    def _json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                self._record_response_headers(response.headers)
                raw = response.read().decode("utf-8")
                try:
                    result = json.loads(raw)
                except json.JSONDecodeError:
                    result = {"ok": False, "error": f"invalid JSON response: {raw[:200]}"}
        except urllib.error.HTTPError as exc:
            self._record_response_headers(exc.headers)
            body = exc.read().decode("utf-8")
            try:
                result = json.loads(body)
            except json.JSONDecodeError:
                result = {"ok": False, "error": body, "status": exc.code}
            else:
                if isinstance(result, dict):
                    result.setdefault("status", exc.code)
                else:
                    result = {"ok": False, "error": str(result), "status": exc.code}
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self._record_response_headers(None)
            result = {"ok": False, "error": f"connection error: {exc}"}
        if self.raise_on_error and not result.get("ok", False):
            raise MegahubError(
                result.get("error", "unknown error"),
                status=result.get("status"),
                response=result,
            )
        return result

    def _record_response_headers(self, headers: Any) -> None:
        if headers is None:
            self.last_response_headers = {}
            self.last_instance_id = None
            return
        self.last_response_headers = dict(headers.items())
        self.last_instance_id = headers.get("X-Megahub-Instance")
