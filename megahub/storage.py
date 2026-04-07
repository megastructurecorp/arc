from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def from_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


class HubStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        if not self.db_path.is_absolute():
            self.db_path = (Path.cwd() / self.db_path)
        self.db_path = self.db_path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.journal_mode = "unknown"
        self._init_db()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_db(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS channels (
                    name TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    created_by TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    from_agent TEXT NOT NULL,
                    to_agent TEXT,
                    channel TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    body TEXT NOT NULL,
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    reply_to INTEGER,
                    thread_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_messages_channel_id
                    ON messages(channel, id);
                CREATE INDEX IF NOT EXISTS idx_messages_to_agent_id
                    ON messages(to_agent, id);

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_agent
                    ON sessions(agent_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_active_last_seen
                    ON sessions(active, last_seen);

                CREATE INDEX IF NOT EXISTS idx_messages_thread_id
                    ON messages(thread_id, id);

                CREATE TABLE IF NOT EXISTS claims (
                    claim_key TEXT PRIMARY KEY,
                    thread_id TEXT,
                    task_message_id INTEGER,
                    owner_agent_id TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    released_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_claims_thread_id
                    ON claims(thread_id);

                CREATE TABLE IF NOT EXISTS locks (
                    file_path TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    locked_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    released_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_locks_agent_id
                    ON locks(agent_id);

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id INTEGER PRIMARY KEY,
                    parent_task_id INTEGER,
                    channel TEXT NOT NULL,
                    thread_id TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (parent_task_id) REFERENCES tasks(task_id)
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_parent
                    ON tasks(parent_task_id);
                CREATE INDEX IF NOT EXISTS idx_tasks_status
                    ON tasks(status);
                """
            )
            journal_row = cur.execute("PRAGMA journal_mode").fetchone()
            if journal_row is not None:
                self.journal_mode = str(journal_row[0]).lower()
            self._conn.commit()
        self.ensure_channel("general", created_by="system", metadata={})
        self.ensure_channel("direct", created_by="system", metadata={})

    @property
    def wal_enabled(self) -> bool:
        return self.journal_mode == "wal"

    def get_storage_info(self) -> dict[str, Any]:
        return {
            "storage_path": str(self.db_path),
            "journal_mode": self.journal_mode,
            "wal_mode": self.wal_enabled,
        }

    def _fetch_active_session(self, agent_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute(
            """
            SELECT * FROM sessions
            WHERE agent_id = ? AND active = 1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (agent_id,),
        )
        return cur.fetchone()

    def reset_active_sessions(self) -> None:
        with self._lock:
            self._conn.execute("UPDATE sessions SET active = 0 WHERE active = 1")
            self._conn.commit()

    def ensure_channel(self, name: str, created_by: str | None, metadata: dict[str, Any]) -> dict[str, Any]:
        now = to_iso(utcnow())
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO channels(name, created_at, created_by, metadata_json)
                VALUES (?, ?, ?, ?)
                """,
                (name, now, created_by, json.dumps(metadata or {})),
            )
            self._conn.commit()
        return self.get_channel(name)

    def get_channel(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM channels WHERE name = ?",
                (name,),
            ).fetchone()
        return self._channel_row_to_dict(row) if row else None

    def list_channels(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM channels ORDER BY name ASC"
            ).fetchall()
        return [self._channel_row_to_dict(row) for row in rows]

    def create_channel(
        self,
        name: str,
        created_by: str | None,
        metadata: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        existing = self.get_channel(name)
        if existing:
            return existing, False
        created = self.ensure_channel(name, created_by=created_by, metadata=metadata or {})
        return created, True

    def create_session(
        self,
        agent_id: str,
        display_name: str | None,
        capabilities: list[str] | None,
        metadata: dict[str, Any] | None,
        replace: bool,
        ttl_sec: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        now = utcnow()
        deactivated: list[dict[str, Any]] = []
        with self._lock:
            active = self._fetch_active_session(agent_id)
            if active:
                expired = from_iso(active["last_seen"]) < (now - timedelta(seconds=ttl_sec))
                if not expired and not replace:
                    raise ValueError("agent_id already has an active session")
                self._conn.execute(
                    "UPDATE sessions SET active = 0 WHERE session_id = ?",
                    (active["session_id"],),
                )
                deactivated.append(self._session_row_to_dict(active))

            session = {
                "session_id": str(uuid.uuid4()),
                "agent_id": agent_id,
                "display_name": display_name or agent_id,
                "capabilities": list(capabilities or []),
                "metadata": dict(metadata or {}),
                "created_at": to_iso(now),
                "last_seen": to_iso(now),
                "active": True,
            }
            self._conn.execute(
                """
                INSERT INTO sessions(
                    session_id, agent_id, display_name, capabilities_json,
                    metadata_json, created_at, last_seen, active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    session["session_id"],
                    session["agent_id"],
                    session["display_name"],
                    json.dumps(session["capabilities"]),
                    json.dumps(session["metadata"]),
                    session["created_at"],
                    session["last_seen"],
                ),
            )
            self._conn.commit()
        return session, deactivated

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self._session_row_to_dict(row) if row else None

    def delete_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ? AND active = 1",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            self._conn.execute(
                "UPDATE sessions SET active = 0 WHERE session_id = ?",
                (session_id,),
            )
            self._conn.commit()
        return self._session_row_to_dict(row)

    def touch_session(self, session_id: str) -> bool:
        now = to_iso(utcnow())
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE sessions
                SET last_seen = ?
                WHERE session_id = ? AND active = 1
                """,
                (now, session_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def prune_expired_sessions(self, ttl_sec: int) -> list[dict[str, Any]]:
        cutoff = utcnow() - timedelta(seconds=ttl_sec)
        expired: list[dict[str, Any]] = []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM sessions
                WHERE active = 1 AND last_seen < ?
                """,
                (to_iso(cutoff),),
            ).fetchall()
            for row in rows:
                expired.append(self._session_row_to_dict(row))
            if expired:
                self._conn.execute(
                    "UPDATE sessions SET active = 0 WHERE active = 1 AND last_seen < ?",
                    (to_iso(cutoff),),
                )
                self._conn.commit()
        return expired

    def list_live_agents(self, ttl_sec: int) -> list[dict[str, Any]]:
        self.prune_expired_sessions(ttl_sec)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM sessions
                WHERE active = 1
                ORDER BY agent_id ASC
                """
            ).fetchall()
        return [self._session_row_to_dict(row) for row in rows]

    def create_message(
        self,
        *,
        from_agent: str,
        to_agent: str | None,
        channel: str,
        kind: str,
        body: str,
        attachments: list[dict[str, Any]],
        reply_to: int | None,
        thread_id: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        now = to_iso(utcnow())
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO messages(
                    ts, from_agent, to_agent, channel, kind,
                    body, attachments_json, reply_to, thread_id, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    from_agent,
                    to_agent,
                    channel,
                    kind,
                    body,
                    json.dumps(attachments),
                    reply_to,
                    thread_id,
                    json.dumps(metadata or {}),
                ),
            )
            self._conn.commit()
            message_id = cur.lastrowid
            row = self._conn.execute(
                "SELECT * FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        return self._message_row_to_dict(row)

    def list_channel_messages(
        self,
        channel: str,
        since_id: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM messages
                WHERE channel = ? AND to_agent IS NULL AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (channel, since_id, limit),
            ).fetchall()
        return [self._message_row_to_dict(row) for row in rows]

    def list_inbox_messages(
        self,
        agent_id: str,
        since_id: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM messages
                WHERE to_agent = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (agent_id, since_id, limit),
            ).fetchall()
        return [self._message_row_to_dict(row) for row in rows]

    def list_visible_messages_for_agent(
        self,
        agent_id: str,
        since_id: int = 0,
        limit: int = 500,
        *,
        channel: str | None = None,
        thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            conditions = ["id > ?", "(to_agent IS NULL OR to_agent = ?)"]
            params: list[Any] = [since_id, agent_id]
            if channel is not None:
                conditions.append("channel = ?")
                params.append(channel)
            if thread_id is not None:
                conditions.append("thread_id = ?")
                params.append(thread_id)
            params.append(limit)
            rows = self._conn.execute(
                f"""
                SELECT * FROM messages
                WHERE {' AND '.join(conditions)}
                ORDER BY id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._message_row_to_dict(row) for row in rows]

    def touch_agent_session(self, agent_id: str) -> bool:
        """Touch the active session for an agent_id (for HTTP activity tracking)."""
        now = to_iso(utcnow())
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE sessions
                SET last_seen = ?
                WHERE agent_id = ? AND active = 1
                """,
                (now, agent_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def list_thread_messages(
        self,
        thread_id: str,
        channel: str | None = None,
        since_id: int = 0,
        limit: int = 100,
        *,
        include_direct: bool = False,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if channel:
                visibility_clause = "" if include_direct else "AND to_agent IS NULL"
                rows = self._conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE thread_id = ? AND channel = ? {visibility_clause} AND id > ?
                    ORDER BY id ASC
                    LIMIT ?
                    """.format(visibility_clause=visibility_clause),
                    (thread_id, channel, since_id, limit),
                ).fetchall()
            else:
                visibility_clause = "" if include_direct else "AND to_agent IS NULL"
                rows = self._conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE thread_id = ? {visibility_clause} AND id > ?
                    ORDER BY id ASC
                    LIMIT ?
                    """.format(visibility_clause=visibility_clause),
                    (thread_id, since_id, limit),
                ).fetchall()
        return [self._message_row_to_dict(row) for row in rows]

    def list_all_thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM messages
                WHERE thread_id = ?
                ORDER BY id ASC
                """,
                (thread_id,),
            ).fetchall()
        return [self._message_row_to_dict(row) for row in rows]

    def acquire_claim(
        self,
        *,
        claim_key: str,
        thread_id: str | None,
        task_message_id: int | None,
        owner_agent_id: str,
        ttl_sec: int = 300,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically acquire a claim. Returns (claim_dict, acquired).
        acquired=False means an active claim already exists by another owner."""
        now = utcnow()
        expires_at = now + timedelta(seconds=ttl_sec)
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM claims WHERE claim_key = ?",
                (claim_key,),
            ).fetchone()
            if existing:
                is_released = existing["released_at"] is not None
                is_expired = from_iso(existing["expires_at"]) < now
                if not is_released and not is_expired:
                    if existing["owner_agent_id"] == owner_agent_id:
                        self._conn.execute(
                            "UPDATE claims SET expires_at = ? WHERE claim_key = ?",
                            (to_iso(expires_at), claim_key),
                        )
                        self._conn.commit()
                        row = self._conn.execute(
                            "SELECT * FROM claims WHERE claim_key = ?",
                            (claim_key,),
                        ).fetchone()
                        return self._claim_row_to_dict(row), True
                    else:
                        return self._claim_row_to_dict(existing), False
                self._conn.execute(
                    """
                    UPDATE claims SET
                        thread_id = ?, task_message_id = ?, owner_agent_id = ?,
                        claimed_at = ?, expires_at = ?, released_at = NULL,
                        metadata_json = ?
                    WHERE claim_key = ?
                    """,
                    (
                        thread_id, task_message_id, owner_agent_id,
                        to_iso(now), to_iso(expires_at),
                        json.dumps(metadata or {}), claim_key,
                    ),
                )
                self._conn.commit()
            else:
                self._conn.execute(
                    """
                    INSERT INTO claims(
                        claim_key, thread_id, task_message_id, owner_agent_id,
                        claimed_at, expires_at, released_at, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        claim_key, thread_id, task_message_id, owner_agent_id,
                        to_iso(now), to_iso(expires_at),
                        json.dumps(metadata or {}),
                    ),
                )
                self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM claims WHERE claim_key = ?",
                (claim_key,),
            ).fetchone()
        return self._claim_row_to_dict(row), True

    def release_claim(
        self,
        claim_key: str,
        agent_id: str,
    ) -> dict[str, Any] | None:
        """Release a claim. Returns the claim dict or None if not found / not owned."""
        now = to_iso(utcnow())
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM claims WHERE claim_key = ?",
                (claim_key,),
            ).fetchone()
            if not existing:
                return None
            if existing["owner_agent_id"] != agent_id:
                return None
            if existing["released_at"] is not None:
                return self._claim_row_to_dict(existing)
            self._conn.execute(
                "UPDATE claims SET released_at = ? WHERE claim_key = ?",
                (now, claim_key),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM claims WHERE claim_key = ?",
                (claim_key,),
            ).fetchone()
        return self._claim_row_to_dict(row)

    def release_claim_force(self, claim_key: str) -> dict[str, Any] | None:
        """Release a claim without owner checks. Used for recovery flows."""
        now = to_iso(utcnow())
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM claims WHERE claim_key = ?",
                (claim_key,),
            ).fetchone()
            if not existing:
                return None
            if existing["released_at"] is not None:
                return self._claim_row_to_dict(existing)
            self._conn.execute(
                "UPDATE claims SET released_at = ? WHERE claim_key = ?",
                (now, claim_key),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM claims WHERE claim_key = ?",
                (claim_key,),
            ).fetchone()
        return self._claim_row_to_dict(row)

    def list_claims(
        self,
        thread_id: str | None = None,
        active_only: bool = False,
        owner_agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        now = to_iso(utcnow())
        with self._lock:
            conditions = []
            params: list[Any] = []
            if thread_id is not None:
                conditions.append("thread_id = ?")
                params.append(thread_id)
            if owner_agent_id is not None:
                conditions.append("owner_agent_id = ?")
                params.append(owner_agent_id)
            if active_only:
                conditions.append("released_at IS NULL")
                conditions.append("expires_at >= ?")
                params.append(now)
            where = " AND ".join(conditions) if conditions else "1=1"
            rows = self._conn.execute(
                f"SELECT * FROM claims WHERE {where} ORDER BY claimed_at ASC",
                params,
            ).fetchall()
        return [self._claim_row_to_dict(row) for row in rows]

    def refresh_claim(
        self,
        claim_key: str,
        owner_agent_id: str,
        *,
        ttl_sec: int = 300,
    ) -> dict[str, Any] | None:
        """Refresh an active claim. Returns None if missing, expired, released, or owned by another agent."""
        now = utcnow()
        expires_at = to_iso(now + timedelta(seconds=ttl_sec))
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM claims WHERE claim_key = ?",
                (claim_key,),
            ).fetchone()
            if not existing:
                return None
            if existing["owner_agent_id"] != owner_agent_id:
                return None
            if existing["released_at"] is not None:
                return None
            if from_iso(existing["expires_at"]) < now:
                return None
            self._conn.execute(
                "UPDATE claims SET expires_at = ? WHERE claim_key = ?",
                (expires_at, claim_key),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM claims WHERE claim_key = ?",
                (claim_key,),
            ).fetchone()
        return self._claim_row_to_dict(row)

    def create_task(
        self,
        *,
        message_id: int,
        parent_task_id: int | None,
        channel: str,
        thread_id: str | None,
    ) -> dict[str, Any]:
        """Register a task-kind message in the tasks table."""
        now = to_iso(utcnow())
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO tasks(task_id, parent_task_id, channel, thread_id, status, created_at)
                VALUES (?, ?, ?, ?, 'open', ?)
                """,
                (message_id, parent_task_id, channel, thread_id, now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (message_id,)
            ).fetchone()
        return self._task_row_to_dict(row)

    def complete_task(self, task_id: int) -> dict[str, Any] | None:
        """Mark a task as done. Returns the updated task or None if not found."""
        now = to_iso(utcnow())
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row:
                return None
            if row["status"] == "done":
                return self._task_row_to_dict(row)
            self._conn.execute(
                "UPDATE tasks SET status = 'done', completed_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row_to_dict(row)

    def list_tasks(
        self,
        parent_id: int | None = None,
        status: str | None = None,
        channel: str | None = None,
        thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List tasks with optional filters."""
        with self._lock:
            conditions: list[str] = []
            params: list[Any] = []
            if parent_id is not None:
                conditions.append("parent_task_id = ?")
                params.append(parent_id)
            if status is not None:
                conditions.append("status = ?")
                params.append(status)
            if channel is not None:
                conditions.append("channel = ?")
                params.append(channel)
            if thread_id is not None:
                conditions.append("thread_id = ?")
                params.append(thread_id)
            where = " AND ".join(conditions) if conditions else "1=1"
            rows = self._conn.execute(
                f"SELECT * FROM tasks WHERE {where} ORDER BY task_id ASC",
                params,
            ).fetchall()
        return [self._task_row_to_dict(row) for row in rows]

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._task_row_to_dict(row) if row else None

    def check_parent_completion(self, task_id: int) -> bool | None:
        """Check if all subtasks of a task's parent are done.
        Returns True if all siblings done, False if not, None if no parent."""
        task = self.get_task(task_id)
        if not task or task["parent_task_id"] is None:
            return None
        siblings = self.list_tasks(parent_id=task["parent_task_id"])
        return all(s["status"] == "done" for s in siblings)

    def list_threads(self) -> list[dict[str, Any]]:
        thread_ids = self._list_thread_ids()
        summaries = [self.get_thread_summary(thread_id) for thread_id in thread_ids]
        present = [summary for summary in summaries if summary is not None]
        present.sort(
            key=lambda item: (
                item["latest_message_id"] if item["latest_message_id"] is not None else -1,
                item["thread_id"],
            ),
            reverse=True,
        )
        return present

    def get_thread_summary(self, thread_id: str) -> dict[str, Any] | None:
        messages = self.list_all_thread_messages(thread_id)
        tasks = self.list_tasks(thread_id=thread_id)
        claims = self.list_claims(thread_id=thread_id)
        locks = self.list_thread_locks(thread_id)
        if not messages and not tasks and not claims and not locks:
            return None
        return self._build_thread_summary(
            thread_id,
            messages=messages,
            tasks=tasks,
            claims=claims,
            locks=locks,
        )

    def get_thread_detail(self, thread_id: str) -> dict[str, Any] | None:
        messages = self.list_all_thread_messages(thread_id)
        tasks = self.list_tasks(thread_id=thread_id)
        claims = self.list_claims(thread_id=thread_id)
        locks = self.list_thread_locks(thread_id)
        summary = self._build_thread_summary(
            thread_id,
            messages=messages,
            tasks=tasks,
            claims=claims,
            locks=locks,
        )
        if summary is None:
            return None
        return {
            "thread": summary,
            "messages": messages,
            "tasks": tasks,
            "claims": claims,
            "locks": locks,
        }

    def list_thread_locks(
        self,
        thread_id: str,
        *,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        locks = self.list_locks(active_only=active_only)
        return [
            lock for lock in locks
            if isinstance(lock.get("metadata"), dict) and lock["metadata"].get("thread_id") == thread_id
        ]

    def _task_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "parent_task_id": row["parent_task_id"],
            "channel": row["channel"],
            "thread_id": row["thread_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        }

    def _list_thread_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT thread_id FROM messages WHERE thread_id IS NOT NULL
                UNION
                SELECT thread_id FROM tasks WHERE thread_id IS NOT NULL
                UNION
                SELECT thread_id FROM claims WHERE thread_id IS NOT NULL
                ORDER BY thread_id ASC
                """
            ).fetchall()
        return [str(row["thread_id"]) for row in rows]

    def _build_thread_summary(
        self,
        thread_id: str,
        *,
        messages: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        locks: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not messages and not tasks and not claims and not locks:
            return None

        latest_message = messages[-1] if messages else None
        artifact_ids = [message["id"] for message in messages if message.get("kind") == "artifact"]
        active_claims = [claim for claim in claims if claim["released_at"] is None and from_iso(claim["expires_at"]) >= utcnow()]
        active_locks = [lock for lock in locks if lock["released_at"] is None and from_iso(lock["expires_at"]) >= utcnow()]
        root_tasks = [task for task in tasks if task["parent_task_id"] is None]
        root_task_id = min((task["task_id"] for task in root_tasks), default=None)
        open_task_count = sum(1 for task in tasks if task["status"] == "open")
        total_task_count = len(tasks)

        if total_task_count > 0 and open_task_count == 0:
            status = "completed"
        elif active_claims or active_locks:
            status = "open"
        else:
            status = "waiting"

        channel = None
        if root_tasks:
            channel = sorted(root_tasks, key=lambda task: task["task_id"])[0]["channel"]
        elif messages:
            channel = messages[0]["channel"]
        elif tasks:
            channel = tasks[0]["channel"]

        return {
            "thread_id": thread_id,
            "channel": channel,
            "root_task_id": root_task_id,
            "latest_message_id": latest_message["id"] if latest_message else None,
            "latest_message_ts": latest_message["ts"] if latest_message else None,
            "latest_artifact_id": max(artifact_ids) if artifact_ids else None,
            "message_count": len(messages),
            "total_task_count": total_task_count,
            "open_task_count": open_task_count,
            "active_claim_count": len(active_claims),
            "active_lock_count": len(active_locks),
            "status": status,
        }

    def acquire_lock(
        self,
        *,
        file_path: str,
        agent_id: str,
        ttl_sec: int = 300,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Acquire a file lock. Returns (lock_dict, acquired).
        acquired=False means an active lock is held by a different agent."""
        now = utcnow()
        expires_at = now + timedelta(seconds=ttl_sec)
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM locks WHERE file_path = ?",
                (file_path,),
            ).fetchone()
            if existing:
                is_released = existing["released_at"] is not None
                is_expired = from_iso(existing["expires_at"]) < now
                if not is_released and not is_expired:
                    if existing["agent_id"] == agent_id:
                        self._conn.execute(
                            "UPDATE locks SET expires_at = ? WHERE file_path = ?",
                            (to_iso(expires_at), file_path),
                        )
                        self._conn.commit()
                        row = self._conn.execute(
                            "SELECT * FROM locks WHERE file_path = ?",
                            (file_path,),
                        ).fetchone()
                        return self._lock_row_to_dict(row), True
                    else:
                        return self._lock_row_to_dict(existing), False
                self._conn.execute(
                    """
                    UPDATE locks SET
                        agent_id = ?, locked_at = ?, expires_at = ?,
                        released_at = NULL, metadata_json = ?
                    WHERE file_path = ?
                    """,
                    (
                        agent_id, to_iso(now), to_iso(expires_at),
                        json.dumps(metadata or {}), file_path,
                    ),
                )
                self._conn.commit()
            else:
                self._conn.execute(
                    """
                    INSERT INTO locks(
                        file_path, agent_id, locked_at, expires_at,
                        released_at, metadata_json
                    )
                    VALUES (?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        file_path, agent_id, to_iso(now), to_iso(expires_at),
                        json.dumps(metadata or {}),
                    ),
                )
                self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM locks WHERE file_path = ?",
                (file_path,),
            ).fetchone()
        return self._lock_row_to_dict(row), True

    def release_lock(
        self,
        file_path: str,
        agent_id: str,
    ) -> dict[str, Any] | None:
        """Release a file lock. Returns the lock dict or None if not found / not owned."""
        now = to_iso(utcnow())
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM locks WHERE file_path = ?",
                (file_path,),
            ).fetchone()
            if not existing:
                return None
            if existing["agent_id"] != agent_id:
                return None
            if existing["released_at"] is not None:
                return self._lock_row_to_dict(existing)
            self._conn.execute(
                "UPDATE locks SET released_at = ? WHERE file_path = ?",
                (now, file_path),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM locks WHERE file_path = ?",
                (file_path,),
            ).fetchone()
        return self._lock_row_to_dict(row)

    def release_lock_force(self, file_path: str) -> dict[str, Any] | None:
        """Release a lock without holder checks. Used for recovery flows."""
        now = to_iso(utcnow())
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM locks WHERE file_path = ?",
                (file_path,),
            ).fetchone()
            if not existing:
                return None
            if existing["released_at"] is not None:
                return self._lock_row_to_dict(existing)
            self._conn.execute(
                "UPDATE locks SET released_at = ? WHERE file_path = ?",
                (now, file_path),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM locks WHERE file_path = ?",
                (file_path,),
            ).fetchone()
        return self._lock_row_to_dict(row)

    def list_locks(
        self,
        agent_id: str | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        now = to_iso(utcnow())
        with self._lock:
            conditions: list[str] = []
            params: list[Any] = []
            if agent_id is not None:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if active_only:
                conditions.append("released_at IS NULL")
                conditions.append("expires_at >= ?")
                params.append(now)
            where = " AND ".join(conditions) if conditions else "1=1"
            rows = self._conn.execute(
                f"SELECT * FROM locks WHERE {where} ORDER BY locked_at ASC",
                params,
            ).fetchall()
        return [self._lock_row_to_dict(row) for row in rows]

    def refresh_lock(
        self,
        file_path: str,
        agent_id: str,
        *,
        ttl_sec: int = 300,
    ) -> dict[str, Any] | None:
        """Refresh an active lock. Returns None if missing, expired, released, or held by another agent."""
        now = utcnow()
        expires_at = to_iso(now + timedelta(seconds=ttl_sec))
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM locks WHERE file_path = ?",
                (file_path,),
            ).fetchone()
            if not existing:
                return None
            if existing["agent_id"] != agent_id:
                return None
            if existing["released_at"] is not None:
                return None
            if from_iso(existing["expires_at"]) < now:
                return None
            self._conn.execute(
                "UPDATE locks SET expires_at = ? WHERE file_path = ?",
                (expires_at, file_path),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM locks WHERE file_path = ?",
                (file_path,),
            ).fetchone()
        return self._lock_row_to_dict(row)

    def _lock_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "file_path": row["file_path"],
            "agent_id": row["agent_id"],
            "locked_at": row["locked_at"],
            "expires_at": row["expires_at"],
            "released_at": row["released_at"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def _claim_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "claim_key": row["claim_key"],
            "thread_id": row["thread_id"],
            "task_message_id": row["task_message_id"],
            "owner_agent_id": row["owner_agent_id"],
            "claimed_at": row["claimed_at"],
            "expires_at": row["expires_at"],
            "released_at": row["released_at"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def _channel_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "name": row["name"],
            "created_at": row["created_at"],
            "created_by": row["created_by"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def _message_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "ts": row["ts"],
            "from_agent": row["from_agent"],
            "to_agent": row["to_agent"],
            "channel": row["channel"],
            "kind": row["kind"],
            "body": row["body"],
            "attachments": json.loads(row["attachments_json"] or "[]"),
            "reply_to": row["reply_to"],
            "thread_id": row["thread_id"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def _session_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "session_id": row["session_id"],
            "agent_id": row["agent_id"],
            "display_name": row["display_name"],
            "capabilities": json.loads(row["capabilities_json"] or "[]"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
            "last_seen": row["last_seen"],
            "active": bool(row["active"]),
        }
