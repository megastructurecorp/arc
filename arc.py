#!/usr/bin/env python3
"""Arc — single-file local-first agent coordination hub.

    python arc.py [--port 6969] [--storage arc.sqlite3]

Zero dependencies beyond Python 3.10+. HTTP + SQLite coordination for
multi-agent systems. All responses are JSON: {"ok": true, "result": ...}
or {"ok": false, "error": "..."}.

REST API — every endpoint at a glance:

  Sessions & Presence
    POST   /v1/sessions              Register agent (agent_id required)
    DELETE /v1/sessions/{id}         Close session
    GET    /v1/agents                List live agents

  Channels & Messages
    GET    /v1/channels              List channels
    POST   /v1/channels              Create channel (name required)
    POST   /v1/messages              Post message (from_agent, body required)
    GET    /v1/messages              Query by ?channel= and/or ?thread_id=
    GET    /v1/events                Unified feed (?agent_id= required, ?since_id=)
    GET    /v1/inbox/{agent_id}      Direct messages for one agent

  Threads
    GET    /v1/threads               List thread summaries
    GET    /v1/threads/{id}          Thread detail with messages

  Claims (atomic task ownership)
    POST   /v1/claims                Acquire (owner_agent_id, claim_key required)
    POST   /v1/claims/refresh        Refresh TTL
    POST   /v1/claims/release        Release
    GET    /v1/claims                List (?active_only=true)

  File Locks (advisory per-file locks)
    POST   /v1/locks                 Acquire (agent_id, file_path required)
    POST   /v1/locks/refresh         Refresh TTL
    POST   /v1/locks/release         Release
    GET    /v1/locks                 List (?active_only=true)

  Tasks (structured subtasks with rollup)
    GET    /v1/tasks                 List (?status=open, ?parent_id=)
    POST   /v1/tasks/{id}/complete   Mark done (auto-rolls-up parent)

  Admin
    GET    /v1/hub-info              Hub config and instance info
    POST   /v1/shutdown              Graceful shutdown (?delay_sec=60)
    POST   /v1/shutdown/cancel       Cancel pending shutdown
    GET    /                         Live HTML dashboard

Default channel: "general" — all agents should join #general first.
This is the shared meeting point. Use /v1/hub-info to confirm (returns
default_channel). Create or join other channels only when needed.

Polling convention: use ?since_id=<last_seen_id>&timeout=<sec> for
long-poll. Messages, events, and inbox all support since_id + limit.

Quick-start curl examples:
  curl localhost:6969/v1/channels
  curl -X POST localhost:6969/v1/sessions -d '{"agent_id":"my-agent"}'
  curl -X POST localhost:6969/v1/messages \\
       -d '{"from_agent":"my-agent","channel":"general","body":"hello"}'
  curl 'localhost:6969/v1/messages?channel=general&since_id=0'
  curl 'localhost:6969/v1/events?agent_id=my-agent&since_id=0&timeout=5'

Python API (import arc):
  ensure_hub(host, port, storage, timeout) -> dict   # start if needed
  create_server(config) -> _Srv                      # create server obj
  run_server(config)                                 # blocking serve
  stop_hub(storage, host, port) -> dict              # graceful stop
  reset_hub(storage, host, port) -> dict             # stop + delete DB

Deployment modes:
  1. Single hub  — one process serves all agents (default).
  2. Shared-filesystem — multiple hubs, same --storage SQLite file.
  3. Sandbox relay — file-spooled forwarding for sandboxed agents.

File layout (search for '# ──' section markers to navigate):
  ~90-115   Imports and constants
  ~170      HubConfig dataclass
  ~200-650  HubStore — SQLite CRUD (sessions, messages, claims, locks, tasks)
  ~655-760  Route patterns (_P dict) + request parsing helpers
  ~760-1040 _H request handler — do_GET / do_POST / do_DELETE implementations
  ~1044-1290 _Srv server class (lifecycle, relay, shutdown timer)
  ~1293-1380 Public API functions (ensure_hub, create/run/stop/reset)
  ~1520-1650 File relay system (FileRelayClient, FileRelayServer)
  ~1654-1875 Smoke test agent
  ~1880-2175 DASHBOARD_HTML (web UI — not needed for API usage)
  ~2178+     CLI main()

Full protocol specification: docs/PROTOCOL.md
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, select, sqlite3, sys, tempfile, threading, time, uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import parse_qs, urlparse

# ── Arc Architecture ────────────────────────────────────────────────
# HubConfig -> HubStore (SQLite) -> _H (HTTP handler) -> _Srv (server)
# Public API: create_server / run_server / ensure_hub / stop_hub / reset_hub
# Route table: see _P dict (~line 655)  |  Full spec: docs/PROTOCOL.md

__version__ = "0.1.0"
LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_SPOOL_DIR = ".arc-relay"
DEFAULT_BASE_URL = "http://127.0.0.1:6969"
DEFAULT_CHANNEL = "smoke-room"
DEFAULT_THREAD_ID = "smoke-relay-001"
DEFAULT_CLAIM_KEY = "smoke-claim-001"

_utcnow = lambda: datetime.now(timezone.utc)
_to_iso = lambda dt: dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
def _from_iso(v):
    return datetime.fromisoformat(v[:-1] + "+00:00" if v.endswith("Z") else v)


def utcnow():
    return _utcnow()


def to_iso(dt):
    return _to_iso(dt)


def from_iso(value):
    return _from_iso(value)

PIDFILE_NAME = ".arc.pid"
def _storage_dir(storage_path):
    p = Path(storage_path)
    if not p.is_absolute(): p = Path.cwd() / p
    return p.resolve().parent
def _pidfile_path(storage_path): return _storage_dir(storage_path) / PIDFILE_NAME
def _storage_path(storage_path):
    p = Path(storage_path).expanduser()
    if not p.is_absolute(): p = Path.cwd() / p
    return p.resolve()
def _pidfile_url(host, port):
    host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://[{host}]:{port}" if ":" in host and not host.startswith("[") else f"http://{host}:{port}"
def _instance_fingerprint(storage_path, birth_marker):
    material = f"{Path(storage_path).resolve()}|{birth_marker}".encode("utf-8")
    return f"mh1-{hashlib.sha256(material).hexdigest()[:20]}"
def _read_pidfile(path):
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return None
    if not isinstance(payload, dict): return None
    try: return {"pid": int(payload["pid"]), "port": int(payload["port"]), "url": str(payload["url"]), "path": str(path)}
    except (KeyError, TypeError, ValueError): return None
def _pidfile_candidates(storage_path):
    out, seen = [], set()
    for root in (Path.cwd().resolve(), _storage_dir(storage_path)):
        for base in (root, *root.parents):
            candidate = base / PIDFILE_NAME
            key = str(candidate)
            if key in seen: continue
            seen.add(key); out.append(candidate)
    return out
def _discover_pidfile(storage_path):
    for candidate in _pidfile_candidates(storage_path):
        info = _read_pidfile(candidate)
        if info is not None: return info
    return None


_candidate_pidfiles = _pidfile_candidates

class _Conflict(ValueError):
    """Mutating validation error that must map to HTTP 409 instead of 400."""

class _NotFound(ValueError):
    """Lookup miss that must map to HTTP 404 instead of 400."""

_ALREADY_SENT = object()  # sentinel returned by handlers that wrote their own response

@dataclass(slots=True)
class HubConfig:
    listen_host: str = "127.0.0.1"; port: int = 6969; allow_remote: bool = False
    storage_path: str = "arc.sqlite3"; log_events: bool = True
    presence_ttl_sec: int = 120; max_body_chars: int = 128_000
    max_attachment_chars: int = 256_000; max_attachments: int = 32; max_query_limit: int = 500
    def validate(self):
        if self.port < 0 or self.port > 65535: raise ValueError("port out of range")
        if self.presence_ttl_sec < 5: raise ValueError("presence_ttl_sec must be >= 5")
        if self.max_body_chars < 1: raise ValueError("max_body_chars must be at least 1")
        if self.max_attachment_chars < 1: raise ValueError("max_attachment_chars must be at least 1")
        if self.max_attachments < 0: raise ValueError("max_attachments must be non-negative")
        if self.max_query_limit < 1: raise ValueError("max_query_limit must be at least 1")
        if not self.allow_remote and self.listen_host not in LOCAL_BIND_HOSTS:
            raise ValueError("Remote bind requires allow_remote=true")
        storage_path = _storage_path(self.storage_path)
        if storage_path.exists() and storage_path.is_dir():
            raise ValueError("storage_path must point to a file, not a directory")
        try:
            storage_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ValueError(f"unable to create storage directory: {storage_path.parent}") from e
        try:
            if storage_path.exists():
                with storage_path.open("ab"): pass
            else:
                with tempfile.NamedTemporaryFile(dir=storage_path.parent, prefix=".arc-write-check-", delete=True): pass
        except OSError as e:
            raise ValueError(f"storage_path is not writable: {storage_path}") from e

# ── Storage ───────────────────────────────────────────────────────────
class HubStore:
    _SCHEMA = """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS channels(name TEXT PRIMARY KEY,created_at TEXT NOT NULL,
        created_by TEXT,metadata_json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,
        from_agent TEXT NOT NULL,to_agent TEXT,channel TEXT NOT NULL,kind TEXT NOT NULL,
        body TEXT NOT NULL,attachments_json TEXT NOT NULL DEFAULT '[]',
        reply_to INTEGER,thread_id TEXT,metadata_json TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS ix_m_ch ON messages(channel,id);
    CREATE INDEX IF NOT EXISTS ix_m_to ON messages(to_agent,id);
    CREATE INDEX IF NOT EXISTS ix_m_th ON messages(thread_id,id);
    CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,
        display_name TEXT NOT NULL,capabilities_json TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,
        last_seen TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
    CREATE INDEX IF NOT EXISTS ix_s_ag ON sessions(agent_id);
    CREATE INDEX IF NOT EXISTS ix_s_ac ON sessions(active,last_seen);
    CREATE TABLE IF NOT EXISTS claims(claim_key TEXT PRIMARY KEY,thread_id TEXT,
        task_message_id INTEGER,owner_agent_id TEXT NOT NULL,claimed_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,released_at TEXT,metadata_json TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS ix_c_th ON claims(thread_id);
    CREATE TABLE IF NOT EXISTS locks(file_path TEXT PRIMARY KEY,agent_id TEXT NOT NULL,
        locked_at TEXT NOT NULL,expires_at TEXT NOT NULL,released_at TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS ix_l_ag ON locks(agent_id);
    CREATE TABLE IF NOT EXISTS tasks(task_id INTEGER PRIMARY KEY,parent_task_id INTEGER,
        channel TEXT NOT NULL,thread_id TEXT,status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL,completed_at TEXT,
        FOREIGN KEY(parent_task_id) REFERENCES tasks(task_id));
    CREATE INDEX IF NOT EXISTS ix_t_par ON tasks(parent_task_id);
    CREATE INDEX IF NOT EXISTS ix_t_st ON tasks(status);"""

    def __init__(self, db_path: str):
        self.db_path = _storage_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lk = threading.RLock()
        self._db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._conn = self._db
        self._lock = self._lk
        self.journal_mode = "unknown"
        with self._lk:
            self._db.executescript(self._SCHEMA)
            row = self._db.execute("PRAGMA journal_mode").fetchone()
            if row is not None: self.journal_mode = str(row[0]).lower()
            self._db.commit()
        for ch in ("general", "direct"):
            now = _to_iso(_utcnow())
            with self._lk:
                self._db.execute("INSERT OR IGNORE INTO channels VALUES(?,?,?,?)", (ch, now, "system", "{}"))
                self._db.commit()

    def close(self):
        with self._lk: self._db.close()

    @property
    def wal_enabled(self):
        return self.journal_mode == "wal"

    def get_storage_info(self):
        return {
            "storage_path": str(self.db_path),
            "journal_mode": self.journal_mode,
            "wal_mode": self.wal_enabled,
        }

    def get_channel(self, name):
        with self._lk:
            r = self._db.execute("SELECT * FROM channels WHERE name=?", (name,)).fetchone()
        return self._ch(r) if r else None

    def list_channels(self):
        with self._lk:
            return [self._ch(r) for r in self._db.execute("SELECT * FROM channels ORDER BY name").fetchall()]

    def create_channel(self, name, created_by, metadata):
        e = self.get_channel(name)
        if e: return e, False
        now = _to_iso(_utcnow())
        with self._lk:
            self._db.execute("INSERT OR IGNORE INTO channels VALUES(?,?,?,?)",
                             (name, now, created_by, json.dumps(metadata or {})))
            self._db.commit()
        return self.get_channel(name), True

    def create_session(self, agent_id, display_name, capabilities, metadata, replace, ttl_sec):
        now, deact = _utcnow(), []
        with self._lk:
            act = self._db.execute("SELECT * FROM sessions WHERE agent_id=? AND active=1 ORDER BY created_at DESC LIMIT 1", (agent_id,)).fetchone()
            if act:
                if _from_iso(act["last_seen"]) >= (now - timedelta(seconds=ttl_sec)) and not replace:
                    raise _Conflict("agent_id already has an active session")
                self._db.execute("UPDATE sessions SET active=0 WHERE session_id=?", (act["session_id"],))
                deact.append(self._ss(act))
            sid, iso = str(uuid.uuid4()), _to_iso(now)
            self._db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,1)",
                (sid, agent_id, display_name or agent_id, json.dumps(list(capabilities or [])),
                 json.dumps(dict(metadata or {})), iso, iso))
            self._db.commit()
        s = {"session_id": sid, "agent_id": agent_id, "display_name": display_name or agent_id,
             "capabilities": list(capabilities or []), "metadata": dict(metadata or {}),
             "created_at": iso, "last_seen": iso, "active": True}
        return s, deact

    def rename_session(self, agent_id, display_name):
        with self._lk:
            cur = self._db.execute(
                "UPDATE sessions SET display_name=?, last_seen=? WHERE agent_id=? AND active=1",
                (display_name, _to_iso(_utcnow()), agent_id))
            self._db.commit()
            if cur.rowcount == 0: return None
            r = self._db.execute(
                "SELECT * FROM sessions WHERE agent_id=? AND active=1 ORDER BY created_at DESC LIMIT 1",
                (agent_id,)).fetchone()
        return self._ss(r) if r else None

    def get_session(self, sid):
        with self._lk: r = self._db.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()
        return self._ss(r) if r else None

    def delete_session(self, sid):
        with self._lk:
            r = self._db.execute("SELECT * FROM sessions WHERE session_id=? AND active=1", (sid,)).fetchone()
            if not r: return None
            self._db.execute("UPDATE sessions SET active=0 WHERE session_id=?", (sid,)); self._db.commit()
        return self._ss(r)

    def touch_agent_session(self, agent_id):
        with self._lk:
            self._db.execute("UPDATE sessions SET last_seen=? WHERE agent_id=? AND active=1", (_to_iso(_utcnow()), agent_id))
            self._db.commit()

    def prune_expired(self, ttl_sec):
        c = _to_iso(_utcnow() - timedelta(seconds=ttl_sec))
        with self._lk:
            rows = self._db.execute("SELECT * FROM sessions WHERE active=1 AND last_seen<?", (c,)).fetchall()
            if rows: self._db.execute("UPDATE sessions SET active=0 WHERE active=1 AND last_seen<?", (c,)); self._db.commit()
        return [self._ss(r) for r in rows]

    def prune_expired_sessions(self, ttl_sec):
        return self.prune_expired(ttl_sec)

    def list_live_agents(self, ttl_sec, capability=None):
        self.prune_expired(ttl_sec)
        with self._lk:
            agents = [self._ss(r) for r in self._db.execute("SELECT * FROM sessions WHERE active=1 ORDER BY agent_id").fetchall()]
        if capability:
            agents = [a for a in agents if capability in a.get("capabilities", [])]
        return agents

    def bootstrap(self, agent_id, ttl_sec):
        """Single-round-trip rehydrate: session, latest visible id, live agents, default channel."""
        self.prune_expired(ttl_sec)
        with self._lk:
            sr = self._db.execute("SELECT * FROM sessions WHERE agent_id=? AND active=1 ORDER BY created_at DESC LIMIT 1", (agent_id,)).fetchone()
            row = self._db.execute("SELECT COALESCE(MAX(id),0) AS mx FROM messages WHERE to_agent IS NULL OR to_agent=?", (agent_id,)).fetchone()
            live = [self._ss(r) for r in self._db.execute("SELECT * FROM sessions WHERE active=1 ORDER BY agent_id").fetchall()]
        return {"agent_id": agent_id, "session": self._ss(sr) if sr else None,
                "latest_visible_id": int(row["mx"] if row else 0),
                "live_agents": live, "default_channel": "general"}

    def create_message(self, **kw):
        now = _to_iso(_utcnow())
        with self._lk:
            cur = self._db.execute("INSERT INTO messages(ts,from_agent,to_agent,channel,kind,body,"
                "attachments_json,reply_to,thread_id,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (now, kw["from_agent"], kw["to_agent"], kw["channel"], kw["kind"], kw["body"],
                 json.dumps(kw["attachments"]), kw["reply_to"], kw["thread_id"], json.dumps(kw["metadata"] or {})))
            self._db.commit()
            r = self._db.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()
        return self._mg(r)

    def get_message(self, msg_id):
        with self._lk:
            r = self._db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        return self._mg(r) if r else None

    def list_channel_messages(self, ch, since_id=0, limit=100):
        with self._lk:
            return [self._mg(r) for r in self._db.execute(
                "SELECT * FROM messages WHERE channel=? AND to_agent IS NULL AND id>? ORDER BY id LIMIT ?", (ch, since_id, limit)).fetchall()]

    def list_inbox_messages(self, agent_id, since_id=0, limit=100):
        with self._lk:
            return [self._mg(r) for r in self._db.execute(
                "SELECT * FROM messages WHERE to_agent=? AND id>? ORDER BY id LIMIT ?", (agent_id, since_id, limit)).fetchall()]

    def list_visible_messages_for_agent(self, agent_id, since_id=0, limit=500, *, channel=None, thread_id=None, exclude_self=False):
        conds = ["id>?", "(to_agent IS NULL OR to_agent=?)"]
        params: list[Any] = [since_id, agent_id]
        if channel is not None:
            conds.append("channel=?")
            params.append(channel)
        if thread_id is not None:
            conds.append("thread_id=?")
            params.append(thread_id)
        if exclude_self:
            conds.append("from_agent!=?")
            params.append(agent_id)
        params.append(limit)
        with self._lk:
            rows = self._db.execute(
                f"SELECT * FROM messages WHERE {' AND '.join(conds)} ORDER BY id LIMIT ?",
                params,
            ).fetchall()
        return [self._mg(r) for r in rows]

    def list_thread_messages(self, tid, channel=None, since_id=0, limit=100, *, include_direct=False):
        with self._lk:
            if channel:
                vis = "" if include_direct else "AND to_agent IS NULL"
                rows = self._db.execute(
                    f"SELECT * FROM messages WHERE thread_id=? AND channel=? {vis} AND id>? ORDER BY id LIMIT ?",
                    (tid, channel, since_id, limit),
                ).fetchall()
            else:
                vis = "" if include_direct else "AND to_agent IS NULL"
                rows = self._db.execute(
                    f"SELECT * FROM messages WHERE thread_id=? {vis} AND id>? ORDER BY id LIMIT ?",
                    (tid, since_id, limit),
                ).fetchall()
        return [self._mg(r) for r in rows]

    def list_all_thread_messages(self, tid):
        with self._lk:
            rows = self._db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY id", (tid,)).fetchall()
        return [self._mg(r) for r in rows]

    def acquire_claim(self, *, claim_key, thread_id, task_message_id, owner_agent_id, ttl_sec=300, metadata=None):
        now, exp = _utcnow(), _to_iso(_utcnow() + timedelta(seconds=ttl_sec))
        with self._lk:
            ex = self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()
            if ex:
                alive = ex["released_at"] is None and _from_iso(ex["expires_at"]) >= now
                if alive:
                    if ex["owner_agent_id"] == owner_agent_id:
                        self._db.execute("UPDATE claims SET expires_at=? WHERE claim_key=?", (exp, claim_key)); self._db.commit()
                        return self._cl(self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()), True
                    return self._cl(ex), False
                self._db.execute("UPDATE claims SET thread_id=?,task_message_id=?,owner_agent_id=?,claimed_at=?,expires_at=?,released_at=NULL,metadata_json=? WHERE claim_key=?",
                    (thread_id, task_message_id, owner_agent_id, _to_iso(now), exp, json.dumps(metadata or {}), claim_key))
            else:
                self._db.execute("INSERT INTO claims VALUES(?,?,?,?,?,?,NULL,?)",
                    (claim_key, thread_id, task_message_id, owner_agent_id, _to_iso(now), exp, json.dumps(metadata or {})))
            self._db.commit()
            return self._cl(self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()), True

    def release_claim(self, claim_key, agent_id):
        with self._lk:
            ex = self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()
            if not ex or ex["owner_agent_id"] != agent_id: return None
            if ex["released_at"] is not None: return self._cl(ex)
            self._db.execute("UPDATE claims SET released_at=? WHERE claim_key=?", (_to_iso(_utcnow()), claim_key)); self._db.commit()
            return self._cl(self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone())

    def release_claim_force(self, claim_key):
        with self._lk:
            ex = self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()
            if not ex: return None
            if ex["released_at"] is not None: return self._cl(ex)
            self._db.execute("UPDATE claims SET released_at=? WHERE claim_key=?", (_to_iso(_utcnow()), claim_key)); self._db.commit()
            return self._cl(self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone())

    def list_claims(self, thread_id=None, active_only=False, owner_agent_id=None):
        conds, params = [], []
        if thread_id is not None: conds.append("thread_id=?"); params.append(thread_id)
        if owner_agent_id is not None: conds.append("owner_agent_id=?"); params.append(owner_agent_id)
        if active_only: conds += ["released_at IS NULL", "expires_at>=?"]; params.append(_to_iso(_utcnow()))
        w = " AND ".join(conds) if conds else "1=1"
        with self._lk:
            return [self._cl(r) for r in self._db.execute(f"SELECT * FROM claims WHERE {w} ORDER BY claimed_at", params).fetchall()]

    def refresh_claim(self, claim_key, owner_agent_id, *, ttl_sec=300):
        now = _utcnow()
        exp = _to_iso(now + timedelta(seconds=ttl_sec))
        with self._lk:
            ex = self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone()
            if not ex or ex["owner_agent_id"] != owner_agent_id: return None
            if ex["released_at"] is not None or _from_iso(ex["expires_at"]) < now: return None
            self._db.execute("UPDATE claims SET expires_at=? WHERE claim_key=?", (exp, claim_key))
            self._db.commit()
            return self._cl(self._db.execute("SELECT * FROM claims WHERE claim_key=?", (claim_key,)).fetchone())

    def create_task(self, *, message_id, parent_task_id, channel, thread_id):
        now = _to_iso(_utcnow())
        with self._lk:
            self._db.execute("INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,'open',?,NULL)",
                (message_id, parent_task_id, channel, thread_id, now))
            self._db.commit()
            r = self._db.execute("SELECT * FROM tasks WHERE task_id=?", (message_id,)).fetchone()
        return self._tk(r)

    def complete_task(self, task_id):
        now = _to_iso(_utcnow())
        with self._lk:
            r = self._db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not r: return None
            if r["status"] == "done": return self._tk(r)
            self._db.execute("UPDATE tasks SET status='done',completed_at=? WHERE task_id=?", (now, task_id))
            self._db.commit()
            r = self._db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._tk(r)

    def list_tasks(self, parent_id=None, status=None, channel=None, thread_id=None):
        conds, params = [], []
        if parent_id is not None: conds.append("parent_task_id=?"); params.append(parent_id)
        if status is not None: conds.append("status=?"); params.append(status)
        if channel is not None: conds.append("channel=?"); params.append(channel)
        if thread_id is not None: conds.append("thread_id=?"); params.append(thread_id)
        w = " AND ".join(conds) if conds else "1=1"
        with self._lk:
            return [self._tk(r) for r in self._db.execute(f"SELECT * FROM tasks WHERE {w} ORDER BY task_id", params).fetchall()]

    def get_task(self, task_id):
        with self._lk:
            r = self._db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._tk(r) if r else None

    def check_parent_completion(self, task_id):
        t = self.get_task(task_id)
        if not t or t["parent_task_id"] is None: return None
        siblings = self.list_tasks(parent_id=t["parent_task_id"])
        return all(s["status"] == "done" for s in siblings)

    def list_threads(self):
        tids = self._list_thread_ids()
        summaries = [self.get_thread_summary(tid) for tid in tids]
        present = [item for item in summaries if item is not None]
        present.sort(
            key=lambda item: (
                item["latest_message_id"] if item["latest_message_id"] is not None else -1,
                item["thread_id"],
            ),
            reverse=True,
        )
        return present

    def get_thread_summary(self, thread_id):
        messages = self.list_all_thread_messages(thread_id)
        tasks = self.list_tasks(thread_id=thread_id)
        claims = self.list_claims(thread_id=thread_id)
        locks = self.list_thread_locks(thread_id)
        if not messages and not tasks and not claims and not locks:
            return None
        return self._build_thread_summary(thread_id, messages=messages, tasks=tasks, claims=claims, locks=locks)

    def get_thread_detail(self, thread_id):
        messages = self.list_all_thread_messages(thread_id)
        tasks = self.list_tasks(thread_id=thread_id)
        claims = self.list_claims(thread_id=thread_id)
        locks = self.list_thread_locks(thread_id)
        summary = self._build_thread_summary(thread_id, messages=messages, tasks=tasks, claims=claims, locks=locks)
        if summary is None:
            return None
        return {
            "thread": summary,
            "messages": messages,
            "tasks": tasks,
            "claims": claims,
            "locks": locks,
        }

    def list_thread_locks(self, thread_id, *, active_only=False):
        return [
            lock for lock in self.list_locks(active_only=active_only)
            if isinstance(lock.get("metadata"), dict) and lock["metadata"].get("thread_id") == thread_id
        ]

    def _list_thread_ids(self):
        with self._lk:
            rows = self._db.execute(
                """
                SELECT thread_id FROM messages WHERE thread_id IS NOT NULL
                UNION
                SELECT thread_id FROM tasks WHERE thread_id IS NOT NULL
                UNION
                SELECT thread_id FROM claims WHERE thread_id IS NOT NULL
                ORDER BY thread_id
                """
            ).fetchall()
        return [str(r["thread_id"]) for r in rows]

    def _build_thread_summary(self, thread_id, *, messages, tasks, claims, locks):
        if not messages and not tasks and not claims and not locks:
            return None
        latest = messages[-1] if messages else None
        artifact_ids = [m["id"] for m in messages if m.get("kind") == "artifact"]
        now = _utcnow()
        active_claims = [c for c in claims if c["released_at"] is None and _from_iso(c["expires_at"]) >= now]
        active_locks = [l for l in locks if l["released_at"] is None and _from_iso(l["expires_at"]) >= now]
        root_tasks = [t for t in tasks if t["parent_task_id"] is None]
        root_task_id = min((t["task_id"] for t in root_tasks), default=None)
        open_task_count = sum(1 for t in tasks if t["status"] == "open")
        total_task_count = len(tasks)
        if total_task_count > 0 and open_task_count == 0:
            status = "completed"
        elif active_claims or active_locks:
            status = "open"
        else:
            status = "waiting"
        channel = None
        if root_tasks:
            channel = sorted(root_tasks, key=lambda item: item["task_id"])[0]["channel"]
        elif messages:
            channel = messages[0]["channel"]
        elif tasks:
            channel = tasks[0]["channel"]
        return {
            "thread_id": thread_id,
            "channel": channel,
            "root_task_id": root_task_id,
            "latest_message_id": latest["id"] if latest else None,
            "latest_message_ts": latest["ts"] if latest else None,
            "latest_artifact_id": max(artifact_ids) if artifact_ids else None,
            "message_count": len(messages),
            "total_task_count": total_task_count,
            "open_task_count": open_task_count,
            "active_claim_count": len(active_claims),
            "active_lock_count": len(active_locks),
            "status": status,
        }

    def _tk(self, r): return {"task_id":r["task_id"],"parent_task_id":r["parent_task_id"],"channel":r["channel"],"thread_id":r["thread_id"],"status":r["status"],"created_at":r["created_at"],"completed_at":r["completed_at"]}

    def acquire_lock(self, *, file_path, agent_id, ttl_sec=300, metadata=None):
        now, exp = _utcnow(), _to_iso(_utcnow() + timedelta(seconds=ttl_sec))
        with self._lk:
            ex = self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()
            if ex:
                alive = ex["released_at"] is None and _from_iso(ex["expires_at"]) >= now
                if alive:
                    if ex["agent_id"] == agent_id:
                        self._db.execute("UPDATE locks SET expires_at=? WHERE file_path=?", (exp, file_path)); self._db.commit()
                        return self._lk_row(self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()), True
                    return self._lk_row(ex), False
                self._db.execute("UPDATE locks SET agent_id=?,locked_at=?,expires_at=?,released_at=NULL,metadata_json=? WHERE file_path=?",
                    (agent_id, _to_iso(now), exp, json.dumps(metadata or {}), file_path))
            else:
                self._db.execute("INSERT INTO locks VALUES(?,?,?,?,NULL,?)",
                    (file_path, agent_id, _to_iso(now), exp, json.dumps(metadata or {})))
            self._db.commit()
            return self._lk_row(self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()), True

    def release_lock(self, file_path, agent_id):
        with self._lk:
            ex = self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()
            if not ex or ex["agent_id"] != agent_id: return None
            if ex["released_at"] is not None: return self._lk_row(ex)
            self._db.execute("UPDATE locks SET released_at=? WHERE file_path=?", (_to_iso(_utcnow()), file_path)); self._db.commit()
            return self._lk_row(self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone())

    def release_lock_force(self, file_path):
        with self._lk:
            ex = self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()
            if not ex: return None
            if ex["released_at"] is not None: return self._lk_row(ex)
            self._db.execute("UPDATE locks SET released_at=? WHERE file_path=?", (_to_iso(_utcnow()), file_path)); self._db.commit()
            return self._lk_row(self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone())

    def list_locks(self, agent_id=None, active_only=False):
        conds, params = [], []
        if agent_id is not None: conds.append("agent_id=?"); params.append(agent_id)
        if active_only: conds += ["released_at IS NULL", "expires_at>=?"]; params.append(_to_iso(_utcnow()))
        w = " AND ".join(conds) if conds else "1=1"
        with self._lk:
            return [self._lk_row(r) for r in self._db.execute(f"SELECT * FROM locks WHERE {w} ORDER BY locked_at", params).fetchall()]

    def refresh_lock(self, file_path, agent_id, *, ttl_sec=300):
        now = _utcnow()
        exp = _to_iso(now + timedelta(seconds=ttl_sec))
        with self._lk:
            ex = self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone()
            if not ex or ex["agent_id"] != agent_id: return None
            if ex["released_at"] is not None or _from_iso(ex["expires_at"]) < now: return None
            self._db.execute("UPDATE locks SET expires_at=? WHERE file_path=?", (exp, file_path))
            self._db.commit()
            return self._lk_row(self._db.execute("SELECT * FROM locks WHERE file_path=?", (file_path,)).fetchone())

    def _lk_row(self, r): return {"file_path":r["file_path"],"agent_id":r["agent_id"],"locked_at":r["locked_at"],"expires_at":r["expires_at"],"released_at":r["released_at"],"metadata":json.loads(r["metadata_json"] or "{}")}

    def _ch(self, r): return {"name":r["name"],"created_at":r["created_at"],"created_by":r["created_by"],"metadata":json.loads(r["metadata_json"] or "{}")}
    def _mg(self, r): return {"id":r["id"],"ts":r["ts"],"from_agent":r["from_agent"],"to_agent":r["to_agent"],"channel":r["channel"],"kind":r["kind"],"body":r["body"],"attachments":json.loads(r["attachments_json"] or "[]"),"reply_to":r["reply_to"],"thread_id":r["thread_id"],"metadata":json.loads(r["metadata_json"] or "{}")}
    def _ss(self, r): return {"session_id":r["session_id"],"agent_id":r["agent_id"],"display_name":r["display_name"],"capabilities":json.loads(r["capabilities_json"] or "[]"),"metadata":json.loads(r["metadata_json"] or "{}"),"created_at":r["created_at"],"last_seen":r["last_seen"],"active":bool(r["active"])}
    def _cl(self, r): return {"claim_key":r["claim_key"],"thread_id":r["thread_id"],"task_message_id":r["task_message_id"],"owner_agent_id":r["owner_agent_id"],"claimed_at":r["claimed_at"],"expires_at":r["expires_at"],"released_at":r["released_at"],"metadata":json.loads(r["metadata_json"] or "{}")}

# ── Server ────────────────────────────────────────────────────────────
MSG_KINDS = {"chat","notice","task","claim","release","artifact","task_request","task_result"}

# Normative feature tokens advertised in GET /v1/hub-info.
# See PROTOCOL.md §6.3 for the vocabulary. Clients SHOULD use membership tests,
# MUST tolerate unknown tokens (forward-compatible), and SHOULD NOT depend on
# ordering. The reference hub implements all v1 tokens.
HUB_FEATURES = [
    "sse",
    "relay",
    "long_poll_keepalive",
    "subtask_rollup",
    "rpc_kinds",
    "capability_filter",
    "shutdown_control",
    "session_rename",
]
ATT_TYPES = {"text","json","code","file_ref","diff_ref"}
_P = {n: re.compile(p) for n, p in [
    ("sessions", r"^/v1/sessions$"), ("session", r"^/v1/sessions/(?P<id>[^/]+)$"),
    ("session_rename", r"^/v1/sessions/(?P<id>[^/]+)/rename$"),
    ("agents", r"^/v1/agents$"), ("channels", r"^/v1/channels$"), ("hub_info", r"^/v1/hub-info$"),
    ("events", r"^/v1/events$"), ("messages", r"^/v1/messages$"), ("bootstrap", r"^/v1/bootstrap$"),
    ("threads", r"^/v1/threads$"), ("thread", r"^/v1/threads/(?P<id>[^/]+)$"),
    ("inbox", r"^/v1/inbox/(?P<id>[^/]+)$"),
    ("claims", r"^/v1/claims$"), ("claims_refresh", r"^/v1/claims/refresh$"), ("claims_rel", r"^/v1/claims/release$"),
    ("locks", r"^/v1/locks$"), ("locks_refresh", r"^/v1/locks/refresh$"), ("locks_rel", r"^/v1/locks/release$"),
    ("tasks", r"^/v1/tasks$"), ("task_complete", r"^/v1/tasks/(?P<id>\d+)/complete$"),
    ("shutdown", r"^/v1/shutdown$"), ("shutdown_cancel", r"^/v1/shutdown/cancel$"),
    ("stream", r"^/v1/stream$"), ("network", r"^/v1/network$"), ("root", r"^/$")]}

def _norm_msg(p, cfg):
    fa = str(p.get("from_agent","")).strip()
    if not fa: raise ValueError("from_agent is required")
    ta = p.get("to_agent"); ta = (str(ta).strip() or None) if ta is not None else None
    ch = p.get("channel") or ("direct" if ta else "general"); ch = str(ch).strip()
    if not ch: raise ValueError("channel must be non-empty")
    kind = str(p.get("kind","chat")).strip().lower()
    if kind not in MSG_KINDS: raise ValueError(f"unsupported kind: {kind}")
    body = str(p.get("body",""))
    raw = p.get("attachments") or []
    if not isinstance(raw, list): raise ValueError("attachments must be a list")
    if len(raw) > cfg.max_attachments: raise ValueError("too many attachments")
    atts = [_norm_att(a, cfg.max_attachment_chars) for a in raw]
    if not body and not atts: raise ValueError("body or attachments is required")
    if len(body) > cfg.max_body_chars: raise ValueError("body exceeds max size")
    rt = p.get("reply_to"); rt = _coerce_int(rt, name="reply_to") if rt is not None else None
    tid = p.get("thread_id"); tid = str(tid) if tid is not None else None
    meta = p.get("metadata") or {}
    if not isinstance(meta, dict): raise ValueError("metadata must be a JSON object")
    return {"from_agent":fa,"to_agent":ta,"channel":ch,"kind":kind,"body":body,"attachments":atts,"reply_to":rt,"thread_id":tid,"metadata":meta}

def _norm_att(a, mx):
    if not isinstance(a, dict): raise ValueError("attachments must contain JSON objects")
    t = str(a.get("type","")).strip()
    if t not in ATT_TYPES: raise ValueError(f"unsupported attachment type: {t}")
    n: dict[str,Any] = {"type": t}
    if t in {"text","json","code"}:
        if "content" not in a: raise ValueError(f"attachment type {t} requires content")
        if len(json.dumps(a["content"])) > mx: raise ValueError(f"attachment type {t} exceeds max size")
        n["content"] = a["content"]
        if t == "code" and a.get("language") is not None: n["language"] = str(a["language"])
    if t in {"file_ref","diff_ref"}:
        pt = str(a.get("path","")).strip()
        if not pt: raise ValueError(f"attachment type {t} requires path")
        n["path"] = pt
        for k in ("description","base","head"):
            if k in a and a[k] is not None: n[k] = a[k]
        for k in ("start_line","end_line"):
            if k in a and a[k] is not None: n[k] = _coerce_int(a[k], name=k)
    return n


def _parse_limit(q, max_limit):
    raw = q.get("limit", ["100"])[0]
    try:
        limit = int(raw)
    except ValueError as e:
        raise ValueError("limit must be an integer") from e
    if limit < 1:
        raise ValueError("limit must be at least 1")
    return min(limit, max_limit)


def _parse_timeout(q):
    raw = q.get("timeout", ["0"])[0]
    try:
        t = float(raw)
    except ValueError as e:
        raise ValueError("timeout must be a number") from e
    return max(0.0, min(t, 60.0))

def _poll_until(fetch_fn, timeout, interval=0.25, on_wait=None):
    """Call fetch_fn() repeatedly until it returns non-empty or timeout expires."""
    if timeout <= 0:
        return fetch_fn()
    deadline = time.monotonic() + timeout
    while True:
        rows = fetch_fn()
        if rows:
            return rows
        now = time.monotonic()
        if now >= deadline:
            return rows
        if on_wait is not None:
            on_wait(now)
        time.sleep(interval)

def _parse_since_id(q):
    raw = q.get("since_id", ["0"])[0]
    try:
        since_id = int(raw)
    except ValueError as e:
        raise ValueError("since_id must be an integer") from e
    if since_id < 0:
        raise ValueError("since_id must be >= 0")
    return since_id

def _coerce_int(v, *, name):
    try: return int(v)
    except (TypeError, ValueError) as e: raise ValueError(f"{name} must be an integer") from e

def _max_req(cfg):
    return cfg.max_body_chars + cfg.max_attachment_chars * cfg.max_attachments + 65_536

# ── Route dispatch framework ─────────────────────────────────────────
# Named validators used by both body-spec validation and per-handler query parsing.
def _v_ttl(v):
    if v < 5: raise ValueError("ttl_sec must be at least 5")
    return v
def _v_delay(v):
    if v < 0: raise ValueError("delay_sec must be >= 0")
    if v > 3600: raise ValueError("delay_sec must be <= 3600")
    return v
def _v_status(v):
    if v not in ("open", "done"): raise ValueError("status must be 'open' or 'done'")
    return v

def _validate(body, spec):
    """Coerce body against (name, type, required, validator?) tuples; ValueError on bad input."""
    out = {}
    for entry in spec:
        name, tp, req = entry[0], entry[1], entry[2]
        val = entry[3] if len(entry) > 3 else None
        raw = body.get(name)
        if raw is None:
            if req: raise ValueError(f"{name} is required")
            continue
        if tp is str:
            v = str(raw).strip()
            if req and not v: raise ValueError(f"{name} is required")
            out[name] = v
        elif tp is int:
            v = _coerce_int(raw, name=name)
            out[name] = val(v) if val else v
        elif tp is dict:
            if not isinstance(raw, dict): raise ValueError(f"{name} must be a JSON object")
            out[name] = raw
        elif tp is list:
            if not isinstance(raw, list): raise ValueError(f"{name} must be a list")
            out[name] = raw
        elif tp is bool:
            out[name] = bool(raw)
    return out

# Route table: (method, _P key) -> (handler_name, body_spec_or_None, touch_field_or_None).
# body_spec is a tuple of (name, type, required, validator?) tuples consumed by _validate.
# None means the handler owns its own parsing (GET queries, orchestration-heavy POSTs).
_S_SESSIONS   = (("agent_id",str,True),("display_name",str,False),("capabilities",list,False),("metadata",dict,False),("replace",bool,False))
_S_SESSION_RENAME = (("display_name",str,True),)
_S_CHANNELS   = (("name",str,True),("created_by",str,False),("metadata",dict,False))
_S_CLAIMS     = (("owner_agent_id",str,True),("claim_key",str,False),("task_message_id",int,False),("thread_id",str,False),("ttl_sec",int,False,_v_ttl),("metadata",dict,False))
_S_CLAIMS_RFR = (("claim_key",str,True),("owner_agent_id",str,True),("ttl_sec",int,False,_v_ttl))
_S_CLAIMS_REL = (("claim_key",str,True),("agent_id",str,True))
_S_LOCKS      = (("agent_id",str,True),("file_path",str,True),("ttl_sec",int,False,_v_ttl),("metadata",dict,False))
_S_LOCKS_RFR  = (("agent_id",str,True),("file_path",str,True),("ttl_sec",int,False,_v_ttl))
_S_LOCKS_REL  = (("agent_id",str,True),("file_path",str,True))
_S_SHUTDOWN   = (("delay_sec",int,False,_v_delay),)

_ROUTES = {
    ("GET","root"):("_h_root",None,None),             ("GET","agents"):("_h_agents",None,None),
    ("GET","channels"):("_h_channels",None,None),     ("GET","hub_info"):("_h_hub_info",None,None),
    ("GET","events"):("_h_events",None,None),         ("GET","messages"):("_h_messages",None,None),
    ("GET","threads"):("_h_threads",None,None),       ("GET","thread"):("_h_thread",None,None),
    ("GET","inbox"):("_h_inbox",None,None),           ("GET","claims"):("_h_list_claims",None,None),
    ("GET","locks"):("_h_list_locks",None,None),      ("GET","tasks"):("_h_list_tasks",None,None),
    ("GET","shutdown"):("_h_shutdown_status",None,None),
    ("GET","bootstrap"):("_h_bootstrap",None,None),  ("GET","stream"):("_h_stream",None,None),
    ("POST","sessions"):("_h_create_session",_S_SESSIONS,None),
    ("POST","session_rename"):("_h_rename_session",_S_SESSION_RENAME,None),
    ("POST","channels"):("_h_create_channel",_S_CHANNELS,None),
    ("POST","messages"):("_h_post_message",None,None),
    ("POST","claims"):("_h_acquire_claim",_S_CLAIMS,"owner_agent_id"),
    ("POST","claims_refresh"):("_h_refresh_claim",_S_CLAIMS_RFR,"owner_agent_id"),
    ("POST","claims_rel"):("_h_release_claim",_S_CLAIMS_REL,"agent_id"),
    ("POST","locks"):("_h_acquire_lock",_S_LOCKS,"agent_id"),
    ("POST","locks_refresh"):("_h_refresh_lock",_S_LOCKS_RFR,"agent_id"),
    ("POST","locks_rel"):("_h_release_lock",_S_LOCKS_REL,"agent_id"),
    ("POST","task_complete"):("_h_task_complete",None,None),
    ("POST","shutdown"):("_h_shutdown_initiate",_S_SHUTDOWN,None),
    ("POST","shutdown_cancel"):("_h_shutdown_cancel",None,None),
    ("POST","network"):("_h_network_toggle",None,None),
}

class _H(BaseHTTPRequestHandler):
    server: _Srv
    def log_message(self, *a): pass
    def _discard_body(self, n, *, limit=None):
        rem = n if limit is None else min(n, limit)
        while rem > 0:
            chunk = self.rfile.read(min(65_536, rem))
            if not chunk: break
            rem -= len(chunk)
    def _j(self):
        raw = self.headers.get("Content-Length", "0")
        try: n = int(raw)
        except (ValueError, TypeError) as e: raise ValueError("Content-Length must be an integer") from e
        if n < 0: raise ValueError("Content-Length must be >= 0")
        mx = _max_req(self.server.cfg)
        if n > mx:
            self._discard_body(n, limit=mx + 65_536)
            self.close_connection = True
            raise ValueError("request body exceeds max size")
        body = self.rfile.read(n) if n else b""
        try: dec = body.decode("utf-8")
        except UnicodeDecodeError as e: raise ValueError("request body must be valid UTF-8") from e
        try: d = json.loads(dec) if body else {}
        except json.JSONDecodeError as e: raise ValueError("malformed JSON") from e
        if not isinstance(d, dict): raise ValueError("request body must be a JSON object")
        return d
    def _ok(self, d, s=200):
        b = json.dumps(d).encode()
        self.send_response(s); self.send_header("Content-Type","application/json")
        self.send_header("X-Arc-Instance", self.server.instance_id)
        self.send_header("Connection","close")
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
        self.close_connection = True
    def _err(self, m, s=400): self._ok({"ok":False,"error":m}, s)
    def _u(self):
        p = urlparse(self.path); return p.path, parse_qs(p.query)

    def _html(self, html):
        b = html.encode("utf-8")
        self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("X-Arc-Instance", self.server.instance_id)
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)

    # ── Dispatch ──────────────────────────────────────────────────────
    def _dispatch(self, method):
        path, q = self._u()
        for (mth, key), (fn, spec, touch) in _ROUTES.items():
            if mth != method: continue
            mt = _P[key].match(path)
            if not mt: continue
            try:
                body, params = None, {}
                if method == "POST":
                    body = self._j()
                    if spec is not None: params = _validate(body, spec)
                result = getattr(self, fn)(params, mt, q, body)
            except _Conflict as e:  return self._err(str(e), 409)
            except _NotFound as e:  return self._err(str(e), 404)
            except ValueError as e: return self._err(str(e), 400)
            if result is _ALREADY_SENT: return
            payload, status = result if isinstance(result, tuple) else (result, 200)
            if touch and body and body.get(touch):
                self.server.store.touch_agent_session(str(body[touch]).strip())
            return self._ok(payload, status)
        if method == "POST":  # drain body so a bad path doesn't hang the keep-alive
            try: n = int(self.headers.get("Content-Length", "0"))
            except (TypeError, ValueError): n = 0
            if n > 0: self._discard_body(n, limit=n)
        self._err("not found", 404)

    def do_GET(self):    self._dispatch("GET")
    def do_POST(self):   self._dispatch("POST")
    def do_DELETE(self):
        mt = _P["session"].match(self._u()[0])
        if not mt: return self._err("not found", 404)
        sess = self.server.store.delete_session(mt.group("id"))
        if not sess: return self._err("session not found", 404)
        return self._ok({"ok":True,"result":{"session_id":mt.group("id"),"deleted":True}})

    # ── GET handlers ──────────────────────────────────────────────────
    def _h_root(self, p, m, q, b):     self._html(DASHBOARD_HTML); return _ALREADY_SENT
    def _h_agents(self, p, m, q, b):
        cap = q.get("capability",[None])[0]
        aid = q.get("as",[None])[0]
        if aid: self.server.store.touch_agent_session(aid)
        return {"ok":True,"result":self.server.store.list_live_agents(self.server.cfg.presence_ttl_sec, capability=cap)}
    def _h_channels(self, p, m, q, b): return {"ok":True,"result":self.server.store.list_channels()}
    def _h_threads(self, p, m, q, b):  return {"ok":True,"result":self.server.store.list_threads()}
    def _h_shutdown_status(self, p, m, q, b): return {"ok":True,"result":self.server.get_shutdown_status()}
    def _h_bootstrap(self, p, m, q, b):
        aid = q.get("agent_id",[None])[0]
        if not aid: raise ValueError("agent_id query parameter is required")
        return {"ok":True,"result":self.server.store.bootstrap(aid, self.server.cfg.presence_ttl_sec)}
    def _h_hub_info(self, p, m, q, b):
        cfg = self.server.cfg; s = self.server.store
        return {"ok":True,"result":{"storage_path":str(s.db_path),"instance_id":self.server.instance_id,
            "journal_mode":s.journal_mode,"wal_mode":s.wal_enabled,"default_channel":"general",
            "max_body_chars":cfg.max_body_chars,"max_attachment_chars":cfg.max_attachment_chars,
            "max_attachments":cfg.max_attachments,"allow_remote":cfg.allow_remote,
            "protocol_version":"1",
            "implementation":"megastructure-arc",
            "implementation_version":__version__,
            "features":HUB_FEATURES,
            "message_kinds":sorted(MSG_KINDS)}}
    def _h_network_toggle(self, p, m, q, b):
        if b and "allow_remote" in b: self.server.cfg.allow_remote = bool(b["allow_remote"])
        return {"ok":True,"result":{"allow_remote":self.server.cfg.allow_remote}}
    def _h_events(self, p, m, q, b):
        s = self.server.store; cfg = self.server.cfg
        aid = q.get("agent_id",[None])[0]; ch = q.get("channel",[None])[0]; tid = q.get("thread_id",[None])[0]
        if not aid: raise ValueError("agent_id query parameter is required")
        if ch and s.get_channel(ch) is None: raise _NotFound("channel not found")
        ex = q.get("exclude_self",[""])[0].lower() in ("true","1","yes")
        si = _parse_since_id(q); li = _parse_limit(q, cfg.max_query_limit); to = _parse_timeout(q)
        s.touch_agent_session(aid)
        touch_every = max(1.0, min(30.0, cfg.presence_ttl_sec / 3.0))
        last_touch = time.monotonic()
        def _keepalive(now):
            nonlocal last_touch
            if now - last_touch >= touch_every:
                s.touch_agent_session(aid)
                last_touch = now
        rows = _poll_until(
            lambda: s.list_visible_messages_for_agent(aid, since_id=si, limit=li, channel=ch, thread_id=tid, exclude_self=ex),
            to,
            on_wait=_keepalive,
        )
        s.touch_agent_session(aid)
        return {"ok":True,"result":rows}
    def _h_stream(self, p, m, q, b):
        """SSE streaming endpoint — keeps connection open and pushes new messages as server-sent events."""
        s = self.server.store; cfg = self.server.cfg
        aid = q.get("agent_id",[None])[0]
        if not aid: raise ValueError("agent_id query parameter is required")
        channels = q.get("channels",[None])[0]  # comma-separated
        ch = None  # filter per-channel in the query if single channel requested
        if channels and "," not in channels: ch = channels
        ex = q.get("exclude_self",[""])[0].lower() in ("true","1","yes")
        si = _parse_since_id(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Arc-Instance", self.server.instance_id)
        self.end_headers()
        ch_set = set(channels.split(",")) if channels and "," in channels else None
        last_touch = time.monotonic()
        try:
            while True:
                msgs = s.list_visible_messages_for_agent(aid, since_id=si, limit=50, channel=ch, exclude_self=ex)
                if ch_set:
                    msgs = [m for m in msgs if m.get("channel") in ch_set]
                for msg in msgs:
                    self.wfile.write(f"data: {json.dumps(msg)}\n\n".encode())
                    si = max(si, msg["id"])
                if msgs:
                    self.wfile.flush()
                now = time.monotonic()
                if now - last_touch >= 30:
                    s.touch_agent_session(aid); last_touch = now
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        return _ALREADY_SENT
    def _h_messages(self, p, m, q, b):
        s = self.server.store; cfg = self.server.cfg
        ch = q.get("channel",[None])[0]; tid = q.get("thread_id",[None])[0]
        if not ch and not tid: raise ValueError("channel or thread_id query parameter is required")
        if ch and s.get_channel(ch) is None: raise _NotFound("channel not found")
        si = _parse_since_id(q); li = _parse_limit(q, cfg.max_query_limit); to = _parse_timeout(q)
        return {"ok":True,"result":_poll_until(lambda: s.list_thread_messages(tid,channel=ch,since_id=si,limit=li) if tid else s.list_channel_messages(ch,since_id=si,limit=li), to)}
    def _h_thread(self, p, m, q, b):
        d = self.server.store.get_thread_detail(m.group("id"))
        if d is None: raise _NotFound("thread not found")
        return {"ok":True,"result":d}
    def _h_inbox(self, p, m, q, b):
        si = _parse_since_id(q); li = _parse_limit(q, self.server.cfg.max_query_limit)
        return {"ok":True,"result":self.server.store.list_inbox_messages(m.group("id"),since_id=si,limit=li)}
    def _h_list_claims(self, p, m, q, b):
        ao = q.get("active_only",[""])[0].lower() in ("true","1","yes")
        return {"ok":True,"result":self.server.store.list_claims(thread_id=q.get("thread_id",[None])[0],active_only=ao)}
    def _h_list_locks(self, p, m, q, b):
        ao = q.get("active_only",[""])[0].lower() in ("true","1","yes")
        return {"ok":True,"result":self.server.store.list_locks(agent_id=q.get("agent_id",[None])[0],active_only=ao)}
    def _h_list_tasks(self, p, m, q, b):
        pid_raw = q.get("parent_id",[None])[0]; st = q.get("status",[None])[0]
        pid = _coerce_int(pid_raw, name="parent_id") if pid_raw is not None else None
        if st is not None: _v_status(st)
        return {"ok":True,"result":self.server.store.list_tasks(parent_id=pid,status=st,channel=q.get("channel",[None])[0],thread_id=q.get("thread_id",[None])[0])}

    # ── POST handlers ─────────────────────────────────────────────────
    def _h_create_session(self, p, m, q, b):
        s = self.server.store
        sess, _ = s.create_session(p["agent_id"], p.get("display_name"),
            [str(c) for c in p.get("capabilities",[])], p.get("metadata",{}),
            bool(p.get("replace",False)), self.server.cfg.presence_ttl_sec)
        return ({"ok":True,"result":sess}, 201)
    def _h_rename_session(self, p, m, q, b):
        dn = p["display_name"].strip()
        if not dn: raise ValueError("display_name must not be empty")
        if len(dn) > 64: raise ValueError("display_name must be 64 characters or fewer")
        sess = self.server.store.rename_session(m.group("id"), dn)
        if sess is None: return ({"ok":False,"error":"no active session for agent_id"}, 404)
        return ({"ok":True,"result":sess}, 200)
    def _h_create_channel(self, p, m, q, b):
        ch, created = self.server.store.create_channel(p["name"], p.get("created_by"), p.get("metadata",{}))
        return ({"ok":True,"result":ch}, 201 if created else 200)
    def _h_post_message(self, p, m, q, b):
        s = self.server.store; raw = dict(b)
        ptid = raw.pop("parent_task_id", None)
        if ptid is not None:
            ptid = _coerce_int(ptid, name="parent_task_id")
            if s.get_task(ptid) is None: raise ValueError("parent_task_id references unknown task")
        n = _norm_msg(raw, self.server.cfg)
        if n["to_agent"] is None and s.get_channel(n["channel"]) is None:
            raise ValueError(f"channel does not exist: {n['channel']}")
        msg = s.create_message(**n)
        if msg["kind"] in ("task", "task_request"):
            s.create_task(message_id=msg["id"], parent_task_id=ptid, channel=msg["channel"], thread_id=msg["thread_id"])
        elif msg["kind"] == "task_result" and msg["reply_to"] is not None:
            orig = s.get_message(msg["reply_to"])
            if orig and orig["kind"] == "task_request":
                t = s.get_task(orig["id"])
                if t and t["status"] == "open":
                    s.complete_task(orig["id"])
                    msg["metadata"]["task_completed"] = orig["id"]
        s.touch_agent_session(msg["from_agent"])
        return ({"ok":True,"result":msg}, 201)
    def _h_acquire_claim(self, p, m, q, b):
        ck = p.get("claim_key") or None; tmid = p.get("task_message_id")
        if not ck:
            if tmid is None: raise ValueError("claim_key or task_message_id is required")
            ck = f"task-{tmid}"
        cl, acq = self.server.store.acquire_claim(claim_key=ck, thread_id=p.get("thread_id") or None,
            task_message_id=tmid, owner_agent_id=p["owner_agent_id"],
            ttl_sec=p.get("ttl_sec",300), metadata=p.get("metadata",{}))
        return ({"ok":True,"acquired":acq,"result":cl}, 201 if acq else 200)
    def _h_refresh_claim(self, p, m, q, b):
        cl = self.server.store.refresh_claim(p["claim_key"], p["owner_agent_id"], ttl_sec=p.get("ttl_sec",300))
        if cl is None: raise _NotFound("claim not found or not owned by owner_agent_id")
        return {"ok":True,"acquired":True,"result":cl}
    def _h_release_claim(self, p, m, q, b):
        cl = self.server.store.release_claim(p["claim_key"], p["agent_id"])
        if cl is None: raise _NotFound("claim not found or not owned by agent_id")
        return {"ok":True,"result":cl}
    def _h_acquire_lock(self, p, m, q, b):
        lk, acq = self.server.store.acquire_lock(file_path=p["file_path"], agent_id=p["agent_id"],
            ttl_sec=p.get("ttl_sec",300), metadata=p.get("metadata",{}))
        return ({"ok":True,"acquired":acq,"result":lk}, 201 if acq else 200)
    def _h_refresh_lock(self, p, m, q, b):
        lk = self.server.store.refresh_lock(p["file_path"], p["agent_id"], ttl_sec=p.get("ttl_sec",300))
        if lk is None: raise _NotFound("lock not found or not owned by agent_id")
        return {"ok":True,"acquired":True,"result":lk}
    def _h_release_lock(self, p, m, q, b):
        lk = self.server.store.release_lock(p["file_path"], p["agent_id"])
        if lk is None: raise _NotFound("lock not found or not owned by agent_id")
        return {"ok":True,"result":lk}
    def _h_task_complete(self, p, m, q, b):
        s = self.server.store; tid = int(m.group("id"))
        t = s.complete_task(tid)
        if t is None: raise _NotFound("task not found")
        result = {"ok":True,"result":t}
        if s.check_parent_completion(tid) is True and t["parent_task_id"] is not None:
            parent = s.get_task(t["parent_task_id"])
            if parent and parent["status"] == "open":
                s.complete_task(t["parent_task_id"])
                subs = s.list_tasks(parent_id=t["parent_task_id"])
                s.create_message(from_agent="system", to_agent=None, channel=parent["channel"],
                    kind="notice", body=f"All {len(subs)} subtasks of task {t['parent_task_id']} are complete.",
                    attachments=[], reply_to=t["parent_task_id"], thread_id=parent["thread_id"],
                    metadata={"auto_rollup":True,"parent_task_id":t["parent_task_id"]})
                result["parent_completed"] = True
        return result
    def _h_shutdown_initiate(self, p, m, q, b):
        self.server.initiate_shutdown(p.get("delay_sec", 60))
        return {"ok":True,"result":{"status":"shutdown_initiated",**(self.server.get_shutdown_status() or {})}}
    def _h_shutdown_cancel(self, p, m, q, b):
        self.server.cancel_shutdown()
        return {"ok":True,"result":{"status":"shutdown_cancelled"}}

class _Srv(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    def __init__(self, cfg, *, spool_dir=DEFAULT_SPOOL_DIR):
        self.cfg, self.store = cfg, HubStore(cfg.storage_path)
        self._timer: threading.Timer | None = None
        self._relay: FileRelayServer | None = None
        self._relay_thread: threading.Thread | None = None
        self._shutdown_timer: threading.Timer | None = None
        self._shutdown_deadline: float | None = None
        self._shutdown_delay: int | None = None
        self._spool_dir = spool_dir
        super().__init__((cfg.listen_host, cfg.port), _H)
        self.bound_port = self.server_address[1]
        self.pidfile_path = _pidfile_path(cfg.storage_path)
        general = self.store.get_channel("general")
        birth_marker = str(general["created_at"]) if general and general.get("created_at") else "unknown"
        self.instance_id = _instance_fingerprint(self.store.db_path, birth_marker)
        self.config = cfg
        self.runtime = self

    def log(self, message):
        if not self.cfg.log_events:
            return
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        print(f"[arc {stamp}] {message}")

    def _write_pidfile(self):
        payload = {"pid": os.getpid(), "port": self.bound_port, "url": _pidfile_url(self.cfg.listen_host, self.bound_port)}
        try:
            self.pidfile_path.parent.mkdir(parents=True, exist_ok=True)
            self.pidfile_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as e:
            self.log(f"pidfile write error: {e}")
    def _cleanup_pidfile(self):
        info = _read_pidfile(self.pidfile_path)
        if not info or info["pid"] != os.getpid() or info["port"] != self.bound_port: return
        try: self.pidfile_path.unlink()
        except FileNotFoundError: return
        except OSError as e:
            self.log(f"pidfile cleanup error: {e}")

    def _schedule_prune(self):
        interval = max(1, self.cfg.presence_ttl_sec // 3)
        self._timer = threading.Timer(interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def get_hub_info(self):
        info = self.store.get_storage_info()
        return {
            "storage_path": info["storage_path"],
            "instance_id": self.instance_id,
            "journal_mode": info["journal_mode"],
            "wal_mode": info["wal_mode"],
        }

    def start(self):
        self._write_pidfile()
        info = self.get_hub_info()
        self.log(
            f"listening on {self.cfg.listen_host}:{self.bound_port} "
            f"(storage={info['storage_path']}, journal_mode={info['journal_mode']}, "
            f"instance={self.instance_id}, allow_remote={self.cfg.allow_remote})"
        )
        if not info["wal_mode"]:
            self.log(
                "warning: SQLite is not in WAL mode; shared-filesystem coordination may not behave "
                "correctly on this storage backend"
            )
        self._schedule_prune()
        self._start_relay()

    def _start_relay(self):
        base_url = _pidfile_url(self.cfg.listen_host, self.bound_port)
        relay_cfg = FileRelayConfig(
            base_url=base_url,
            spool_dir=self._spool_dir,
        )
        ensure_spool_dirs(self._spool_dir)
        self._relay = FileRelayServer(relay_cfg)
        self._relay_thread = threading.Thread(target=self._relay.run, name="arc-relay", daemon=True)
        self._relay_thread.start()
        self.log(f"relay started (spool={self._spool_dir})")

    def start_prune(self):
        self.start()

    def _tick(self):
        try:
            expired = self.store.prune_expired_sessions(self.cfg.presence_ttl_sec)
            for session in expired:
                self.log(f"session expired: agent={session['agent_id']} session={session['session_id']}")
            if expired:
                self._recover_expired_work(expired)
        except Exception as e:
            self.log(f"prune error: {e}")
        self._schedule_prune()

    def _recover_expired_work(self, expired_sessions):
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

    def _post_recovery_notice_for_claim(self, claim, *, stale_agent_id):
        thread_id = claim.get("thread_id")
        channel = "general"
        if thread_id:
            summary = self.store.get_thread_summary(thread_id)
            if summary and summary.get("channel"):
                channel = str(summary["channel"])
        task_message_id = claim.get("task_message_id")
        self.store.create_message(
            from_agent="system",
            to_agent=None,
            channel=channel,
            kind="notice",
            body=(
                f"Recovered stale claim {claim['claim_key']} from {stale_agent_id}. "
                "Work is available for pickup."
            ),
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

    def _post_recovery_notice_for_lock(self, lock, *, stale_agent_id):
        metadata = lock.get("metadata") or {}
        thread_id = metadata.get("thread_id")
        channel = str(metadata.get("channel") or "general")
        if thread_id:
            summary = self.store.get_thread_summary(str(thread_id))
            if summary and summary.get("channel"):
                channel = str(summary["channel"])
        self.store.create_message(
            from_agent="system",
            to_agent=None,
            channel=channel,
            kind="notice",
            body=(
                f"Recovered stale lock on {lock['file_path']} from {stale_agent_id}. "
                "The file is available for pickup."
            ),
            attachments=[],
            reply_to=None,
            thread_id=None if thread_id is None else str(thread_id),
            metadata={
                "recovery": True,
                "stale_agent_id": stale_agent_id,
                "file_path": lock["file_path"],
            },
        )

    def stop(self):
        if self._shutdown_timer:
            self._shutdown_timer.cancel()
            self._shutdown_timer = None
        if self._relay:
            self._relay.request_stop()
        if self._relay_thread and self._relay_thread.is_alive():
            self._relay_thread.join(timeout=2.0)
        if self._timer: self._timer.cancel()
        self._cleanup_pidfile()
        self.store.close()

    def _post_system_notice(self, body, *, metadata=None):
        self.store.create_message(
            from_agent="system", to_agent=None, channel="general",
            kind="notice", body=body, attachments=[],
            reply_to=None, thread_id=None, metadata=metadata or {},
        )

    def initiate_shutdown(self, delay_sec=60):
        if self._shutdown_timer is not None or self._shutdown_deadline is not None:
            raise ValueError("shutdown already pending")
        if delay_sec == 0:
            self._post_system_notice(
                "Hub is shutting down now.",
                metadata={"shutdown": True, "delay_sec": 0},
            )
            self.log("shutdown initiated (immediate)")
            threading.Thread(target=self._execute_shutdown, daemon=True).start()
            return
        self._shutdown_delay = delay_sec
        self._shutdown_deadline = time.monotonic() + delay_sec
        self._post_system_notice(
            f"Hub shutdown initiated. Shutting down in {delay_sec} seconds.",
            metadata={"shutdown": True, "delay_sec": delay_sec},
        )
        self.log(f"shutdown initiated (delay={delay_sec}s)")
        self._shutdown_timer = threading.Timer(delay_sec, self._execute_shutdown)
        self._shutdown_timer.daemon = True
        self._shutdown_timer.start()

    def cancel_shutdown(self):
        if self._shutdown_timer is None and self._shutdown_deadline is None:
            raise ValueError("no shutdown pending")
        if self._shutdown_timer:
            self._shutdown_timer.cancel()
        self._shutdown_timer = None
        self._shutdown_deadline = None
        self._shutdown_delay = None
        self._post_system_notice(
            "Hub shutdown cancelled.",
            metadata={"shutdown_cancelled": True},
        )
        self.log("shutdown cancelled")

    def get_shutdown_status(self):
        if self._shutdown_deadline is None:
            return None
        remaining = max(0, int(self._shutdown_deadline - time.monotonic()))
        return {"remaining_sec": remaining, "delay_sec": self._shutdown_delay}

    def _execute_shutdown(self):
        self._shutdown_timer = None
        self._post_system_notice(
            "Hub is shutting down now.",
            metadata={"shutdown": True, "final": True},
        )
        self.log("executing shutdown")
        self.shutdown()


def create_server(config=None, *, spool_dir=DEFAULT_SPOOL_DIR):
    cfg = config or HubConfig()
    cfg.validate()
    return _Srv(cfg, spool_dir=spool_dir)


def run_server(config=None, *, spool_dir=DEFAULT_SPOOL_DIR):
    cfg = config or HubConfig()
    cfg.validate()
    if cfg.allow_remote:
        print(
            "[arc] Warning: allow_remote=true exposes this daemon to non-local clients. "
            "There is no built-in auth in v1."
        )
    srv = create_server(cfg, spool_dir=spool_dir)
    srv.start()
    try:
        srv.serve_forever()
    finally:
        srv.stop()
        srv.server_close()

def _probe_hub(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{url}/v1/channels", method="GET"), timeout=2): return True
    except (urllib.error.URLError, OSError, TimeoutError): return False


def _find_running_hub(storage="arc.sqlite3", host="127.0.0.1", port=6969):
    """Return (url, pid_info) for a running hub, or (None, None)."""
    pid_info = _discover_pidfile(storage)
    if pid_info and _probe_hub(pid_info["url"]):
        return pid_info["url"], pid_info
    base = _pidfile_url(host, port)
    if _probe_hub(base):
        return base, pid_info
    return None, pid_info


def stop_hub(storage="arc.sqlite3", host="127.0.0.1", port=6969):
    """Stop a running hub. Returns dict with stopped (bool) and details."""
    import signal
    url, pid_info = _find_running_hub(storage, host, port)
    if url is None:
        return {"stopped": False, "error": "no running hub found"}
    if pid_info and pid_info.get("pid"):
        pid = pid_info["pid"]
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError) as e:
            return {"stopped": False, "error": f"failed to stop pid {pid}: {e}"}
        pidfile = Path(pid_info.get("path", ""))
        if pidfile.exists():
            try:
                pidfile.unlink()
            except OSError:
                pass
        return {"stopped": True, "pid": pid, "url": url}
    return {"stopped": False, "error": "hub is responding but no pidfile found to identify process"}


def reset_hub(storage="arc.sqlite3", host="127.0.0.1", port=6969):
    """Stop the hub if running, then delete the SQLite database. Returns dict."""
    url, _ = _find_running_hub(storage, host, port)
    if url is not None:
        result = stop_hub(storage, host, port)
        if not result.get("stopped"):
            return {"reset": False, "error": f"could not stop running hub: {result.get('error')}"}
        time.sleep(0.3)
    db = _storage_path(storage)
    removed = []
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            try:
                p.unlink()
                removed.append(p.name)
            except OSError as e:
                return {"reset": False, "error": f"failed to remove {p}: {e}"}
    if not removed:
        return {"reset": True, "note": "database did not exist"}
    return {"reset": True, "removed": removed}


def ensure_hub(host="127.0.0.1", port=6969, storage="arc.sqlite3", timeout=5.0, spool_dir=DEFAULT_SPOOL_DIR,
               max_body_chars=128_000, max_attachment_chars=256_000, max_attachments=32, allow_remote=False):
    """Check if a hub is running; if not, start one in the background.
    Returns dict with: running (bool), started (bool), url (str).
    The port binding itself is the mutex — only one process can bind."""
    import subprocess, sys
    base = _pidfile_url(host, port)
    pid_info = _discover_pidfile(storage)
    if pid_info and _probe_hub(pid_info["url"]): return {"running": True, "started": False, "url": pid_info["url"]}
    if _probe_hub(base): return {"running": True, "started": False, "url": base}
    try:
        subprocess.Popen([sys.executable, __file__, "--host", host, "--port", str(port),
            "--storage", storage, "--spool-dir", spool_dir,
            "--max-body-chars", str(max_body_chars),
            "--max-attachment-chars", str(max_attachment_chars),
            "--max-attachments", str(max_attachments)] + (["--allow-remote"] if allow_remote else []) + [
            "--quiet"], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError: return {"running": False, "started": False, "url": base, "error": "spawn failed"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.15)
        if _probe_hub(base): return {"running": True, "started": True, "url": base}
    return {"running": False, "started": True, "url": base, "error": "timeout"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_agent_id(agent_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(agent_id or "").strip())
    return cleaned or "agent"


def _spool_root(spool_dir: str | Path) -> Path:
    # Keep spool paths lexical instead of calling resolve(). Some sandbox
    # harnesses expose the workspace through odd mount paths, and resolve()
    # can turn a simple relative path into an unusable host-looking string.
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
    base_url: str = DEFAULT_BASE_URL
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
            return _error_response(request_id, 597, f"relay spool write failed: {exc}")

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if response_path.exists():
                payload = _load_json_file(response_path)
                if not isinstance(payload, dict):
                    return _error_response(request_id, 500, "relay produced invalid response payload", body=payload)
                return payload
            time.sleep(self.poll_interval_sec)
        return _error_response(request_id, 598, "relay timed out waiting for response")


class ArcError(Exception):
    """Raised by ArcClient when the hub returns an !ok envelope."""
    def __init__(self, error: str, status: int = 400):
        super().__init__(error)
        self.error = error
        self.status = status

class _HTTPTransport:
    """Direct HTTP transport for ArcClient (urllib.request under the hood)."""
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
    def call(self, method: str, path: str, payload: dict[str, Any] | None = None,
             timeout_override: float | None = None) -> dict[str, Any]:
        timeout = self.timeout if timeout_override is None else timeout_override
        return _http_json(self.base_url, method, path, payload, timeout=timeout)

class _RelayTransport:
    """File-spooled transport for sandboxed agents that cannot reach host localhost.
    Wraps FileRelayClient and unwraps its envelope so callers see the hub envelope directly.

    The relay envelope nests the hub's response under `body`. A hub 4xx is still a
    successful transport — we return the hub envelope as-is and let ArcClient._call
    raise ArcError on !ok. A transport-level failure (timeout, spool error, malformed
    envelope) has no `body` — we synthesize an error envelope from the top-level fields.
    """
    def __init__(self, relay_client: "FileRelayClient"):
        self._rc = relay_client
    def call(self, method: str, path: str, payload: dict[str, Any] | None = None,
             timeout_override: float | None = None) -> dict[str, Any]:
        r = self._rc.call(method, path, payload)
        body = r.get("body")
        if isinstance(body, dict): return body
        return {"ok": False, "error": r.get("error") or "relay transport error"}

class ArcClient:
    """Client for the Arc hub. Tracks since_id internally so poll() is idempotent.

    Two ways to construct:
      ArcClient(agent_id, base_url=...)            # direct HTTP (default)
      ArcClient.over_relay(agent_id, spool_dir=...) # file-spooled relay (sandboxed)

    Construct once per agent process. `exclude_self=True` is the default on poll() —
    callers that want their own posts echoed back must pass exclude_self=False.
    """
    def __init__(self, agent_id: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 15.0,
                 *, transport: Any = None):
        self.agent_id = agent_id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport if transport is not None else _HTTPTransport(self.base_url, timeout)
        self._since_id = 0

    @classmethod
    def over_relay(cls, agent_id: str, spool_dir: str = DEFAULT_SPOOL_DIR, *, timeout: float = 30.0) -> "ArcClient":
        """Construct a ArcClient that talks to the hub via the file-spool relay.
        Use this in sandboxes that cannot reach 127.0.0.1 and cannot safely use SQLite
        on the shared mount. The host must already be running `python arc.py ensure`."""
        rc = FileRelayClient(agent_id=agent_id, spool_dir=spool_dir, timeout=timeout)
        return cls(agent_id, timeout=timeout, transport=_RelayTransport(rc))

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None,
              *, timeout_override: float | None = None) -> dict[str, Any]:
        resp = self._transport.call(method, path, payload, timeout_override=timeout_override)
        if not resp.get("ok"): raise ArcError(resp.get("error", "unknown error"))
        return resp

    def register(self, *, display_name=None, replace=True, capabilities=None, metadata=None) -> dict:
        body: dict[str, Any] = {"agent_id": self.agent_id, "replace": bool(replace)}
        if display_name is not None: body["display_name"] = display_name
        if capabilities is not None: body["capabilities"] = list(capabilities)
        if metadata is not None: body["metadata"] = dict(metadata)
        return self._call("POST", "/v1/sessions", body)["result"]

    def post(self, channel: str, body: str, *, kind: str = "chat", thread_id: str | None = None,
             to_agent: str | None = None, attachments: list | None = None,
             metadata: dict | None = None, reply_to: int | None = None,
             parent_task_id: int | None = None) -> dict:
        p: dict[str, Any] = {"from_agent": self.agent_id, "channel": channel, "kind": kind, "body": body}
        if thread_id is not None:    p["thread_id"] = thread_id
        if to_agent is not None:     p["to_agent"] = to_agent
        if attachments is not None:  p["attachments"] = attachments
        if metadata is not None:     p["metadata"] = metadata
        if reply_to is not None:     p["reply_to"] = reply_to
        if parent_task_id is not None: p["parent_task_id"] = parent_task_id
        return self._call("POST", "/v1/messages", p)["result"]

    def dm(self, to_agent: str, body: str, **kw) -> dict:
        kw.setdefault("channel", "direct")
        return self.post(body=body, to_agent=to_agent, **kw)

    def poll(self, *, exclude_self: bool = True, timeout: float = 30.0,
             channel: str | None = None, thread_id: str | None = None, limit: int = 100) -> list[dict]:
        """Long-poll /v1/events; advances internal since_id on return. Returns new messages only."""
        q: dict[str, Any] = {"agent_id": self.agent_id, "since_id": self._since_id,
                             "timeout": timeout, "limit": limit}
        if exclude_self: q["exclude_self"] = 1
        if channel is not None:   q["channel"] = channel
        if thread_id is not None: q["thread_id"] = thread_id
        path = "/v1/events?" + urllib.parse.urlencode(q)
        transport_timeout = max(self.timeout, float(timeout) + 5.0)
        msgs = self._call("GET", path, timeout_override=transport_timeout)["result"]
        if msgs: self._since_id = max(m["id"] for m in msgs)
        return msgs

    def bootstrap(self) -> dict:
        r = self._call("GET", f"/v1/bootstrap?agent_id={urllib.parse.quote(self.agent_id)}")["result"]
        self._since_id = max(self._since_id, int(r.get("latest_visible_id", 0)))
        return r

    def whoami(self) -> dict: return self.bootstrap()

    def claim(self, claim_key: str, *, thread_id: str | None = None,
              task_message_id: int | None = None, ttl_sec: int = 300, metadata: dict | None = None) -> dict:
        p: dict[str, Any] = {"owner_agent_id": self.agent_id, "claim_key": claim_key, "ttl_sec": ttl_sec}
        if thread_id is not None:       p["thread_id"] = thread_id
        if task_message_id is not None: p["task_message_id"] = task_message_id
        if metadata is not None:        p["metadata"] = metadata
        return self._call("POST", "/v1/claims", p)["result"]

    def refresh_claim(self, claim_key: str, ttl_sec: int = 300) -> dict:
        return self._call("POST", "/v1/claims/refresh",
            {"claim_key": claim_key, "owner_agent_id": self.agent_id, "ttl_sec": ttl_sec})["result"]

    def release(self, claim_key: str) -> dict:
        return self._call("POST", "/v1/claims/release",
            {"claim_key": claim_key, "agent_id": self.agent_id})["result"]

    def lock(self, file_path: str, ttl_sec: int = 300, metadata: dict | None = None) -> dict:
        p: dict[str, Any] = {"agent_id": self.agent_id, "file_path": file_path, "ttl_sec": ttl_sec}
        if metadata is not None: p["metadata"] = metadata
        return self._call("POST", "/v1/locks", p)["result"]

    def unlock(self, file_path: str) -> dict:
        return self._call("POST", "/v1/locks/release",
            {"agent_id": self.agent_id, "file_path": file_path})["result"]

    def complete_task(self, task_id: int) -> dict:
        return self._call("POST", f"/v1/tasks/{int(task_id)}/complete", {})["result"]

    @classmethod
    def quickstart(cls, agent_id: str, base_url: str = DEFAULT_BASE_URL, *,
                   display_name: str | None = None, capabilities: list[str] | None = None,
                   timeout: float = 15.0) -> "ArcClient":
        """Construct, register, and return a ready-to-use client in one call."""
        c = cls(agent_id, base_url=base_url, timeout=timeout)
        c.register(display_name=display_name or agent_id, replace=True, capabilities=capabilities)
        return c

    def call(self, to_agent: str, body: str, *, channel: str = "direct",
             timeout: float = 30.0, poll_interval: float = 1.0,
             metadata: dict | None = None) -> dict:
        """Synchronous agent-to-agent RPC. Posts a task_request, polls for the
        matching task_result (via reply_to), returns the result message or raises on timeout."""
        req = self.post(channel, body, kind="task_request", to_agent=to_agent, metadata=metadata)
        req_id = req["id"]
        scan_since = max(0, req_id - 1)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            path = f"/v1/messages?channel={urllib.parse.quote(channel)}&since_id={scan_since}&limit=200"
            msgs = self._call("GET", path)["result"]
            for m in msgs:
                if m.get("kind") == "task_result" and m.get("reply_to") == req_id:
                    return m
            time.sleep(poll_interval)
        raise ArcError(f"RPC to {to_agent} timed out after {timeout}s", status=408)


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
    claims_resp = _require_ok(
        transport.request(
            "GET",
            f"/v1/claims?{urllib.parse.urlencode({'thread_id': thread_id, 'active_only': 'true'})}",
        ),
        "list claims",
    )
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

# ── Dashboard HTML ────────────────────────────────────────────────────
# Served by GET / — the live web dashboard. Moved to bottom of file
# so scanning agents see API code in the first 1000 lines.
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arc</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,monospace;
  background:#0f172a;color:#e2e8f0;display:flex;flex-direction:column;height:100vh}
#header{flex-shrink:0;display:flex;align-items:center;gap:.75rem;padding:.6rem 1rem;
  background:#1e293b;border-bottom:1px solid #334155}
#header h1{font-size:1.1rem;font-weight:700;color:#38bdf8;white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex-shrink:0}
.dot-green{background:#34d399}.dot-red{background:#f87171}
#status-text{font-size:.75rem;color:#64748b;white-space:nowrap}
#agents-bar{display:flex;gap:.4rem;flex-wrap:wrap;margin-left:auto}
.agent-pill{font-size:.7rem;padding:.15rem .5rem;border-radius:9999px;
  background:#334155;color:#e2e8f0;white-space:nowrap;display:flex;align-items:center;gap:.3rem}
.agent-pill .dot{width:6px;height:6px}
#feed{flex:1;overflow-y:auto;padding:.75rem 1rem;display:flex;flex-direction:column;gap:.15rem}
.msg{font-size:.82rem;line-height:1.5;word-wrap:break-word}
.msg-time{color:#475569;margin-right:.4rem;font-size:.75rem}
.msg-from{font-weight:600;margin-right:.3rem}
.msg-self .msg-from{color:#38bdf8}
.msg-system{color:#64748b;font-style:italic}
.badge{display:inline-block;padding:.05rem .35rem;border-radius:.2rem;font-size:.65rem;
  font-weight:600;margin-right:.3rem;vertical-align:middle}
.badge-notice{background:#713f12;color:#fbbf24}
.badge-task{background:#3b0764;color:#c084fc}
.badge-artifact{background:#064e3b;color:#34d399}
.badge-claim{background:#713f12;color:#fbbf24}
.badge-release{background:#7f1d1d;color:#f87171}
#input-bar{flex-shrink:0;display:flex;gap:.5rem;padding:.5rem 1rem;
  background:#1e293b;border-top:1px solid #334155}
#msg-input{flex:1;background:#0f172a;border:1px solid #334155;border-radius:.375rem;
  color:#e2e8f0;padding:.4rem .6rem;font-size:.85rem;font-family:inherit;outline:none}
#msg-input:focus{border-color:#38bdf8}
#msg-input::placeholder{color:#475569}
#send-btn{background:#1e3a5f;color:#60a5fa;border:1px solid #334155;border-radius:.375rem;
  padding:.4rem .8rem;font-size:.82rem;cursor:pointer;font-family:inherit;white-space:nowrap}
#send-btn:hover{background:#2d4a6f}
#footer{flex-shrink:0;padding:.3rem 1rem;font-size:.7rem;color:#475569;
  background:#1e293b;border-top:1px solid #334155;display:flex;gap:1rem;align-items:center}
#footer code{color:#64748b}
.empty{color:#475569;font-style:italic;padding:2rem;text-align:center}
</style>
</head>
<body>
<div id="header">
  <h1>Arc</h1>
  <span class="dot dot-green" id="status-dot"></span>
  <span id="status-text">Connecting...</span>
  <span id="me-name" style="font-size:.75rem;color:#64748b;margin-left:.5rem">operator</span>
  <span id="channel-indicator" style="margin-left:.7rem;color:#94a3b8;font-size:.85rem;font-weight:400">#general</span>
  <select id="thread-picker" style="margin-left:.7rem;background:#1e293b;color:#cbd5e1;border:1px solid #334155;border-radius:.25rem;padding:.15rem .3rem;font-size:.78rem">
    <option value="">(all channel messages)</option>
  </select>
  <div id="agents-bar"></div>
</div>
<div id="shutdown-bar" style="display:none;padding:.4rem 1rem;background:#7f1d1d;
  color:#fbbf24;font-size:.82rem;text-align:center;flex-shrink:0">
  <span id="shutdown-msg"></span>
</div>
<div id="feed"><div class="empty">Loading messages...</div></div>
<details id="inbox-panel" style="background:#0f1729;border-top:1px solid #334155;padding:.3rem .8rem;font-size:.82rem">
  <summary style="cursor:pointer;color:#94a3b8">Operator inbox (<span id="inbox-count">0</span>)</summary>
  <div id="inbox-feed" style="max-height:10rem;overflow-y:auto;margin-top:.3rem"></div>
</details>
<div id="input-bar">
  <input id="msg-input" type="text" placeholder="Send a message to agents..." autocomplete="off">
  <button id="send-btn">Send</button>
</div>
<div id="footer">
  <span id="hub-info"></span>
  <span style="margin-left:auto"><code>/channels</code> &middot; <code>/channel &lt;name&gt;</code> &middot; <code>/create-channel &lt;name&gt;</code> &middot; <code>/dm &lt;agent&gt; &lt;msg&gt;</code> &middot; <code>/nick &lt;name&gt;</code> &middot; <code>/network on|off</code> &middot; <code>/quit [sec]</code></span>
</div>
<script>
const NICK_COLORS=['#38bdf8','#34d399','#fbbf24','#f87171','#c084fc','#fb923c','#2dd4bf','#e879f9'];
let lastId=0,seeded=false,currentChannel='general',currentThread=null,inboxLastId=0;
let currentAgent='operator';
let currentDisplay='operator';
let agentNameMap={};

async function tryRegister(agentId,display,replace){
  try{
    const r=await fetch('/v1/sessions',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({agent_id:agentId,display_name:display,replace:replace})});
    return {status:r.status,body:await r.json().catch(()=>({}))};
  }catch(e){return {status:0,body:{}};}
}

async function bootstrapIdentity(){
  let stored=null,storedDisplay=null;
  try{stored=localStorage.getItem('arcAgentId');}catch(e){}
  try{storedDisplay=localStorage.getItem('arcDisplayName');}catch(e){}
  const display=storedDisplay||'operator';
  if(stored){
    const r=await tryRegister(stored,display,true);
    if(r.body&&r.body.ok){setIdentity(r.body.result);return;}
  }
  let r=await tryRegister('operator',display,false);
  if(r.status===409){
    const id='operator-'+Math.random().toString(36).slice(2,6);
    r=await tryRegister(id,display,false);
  }
  if(r.body&&r.body.ok)setIdentity(r.body.result);
}

function setIdentity(sess){
  currentAgent=sess.agent_id;
  currentDisplay=sess.display_name||sess.agent_id;
  try{localStorage.setItem('arcAgentId',currentAgent);}catch(e){}
  try{localStorage.setItem('arcDisplayName',currentDisplay);}catch(e){}
  const el=$('me-name');if(el)el.textContent=currentDisplay;
}

function displayNameFor(agentId){return agentNameMap[agentId]||agentId;}

async function renameMe(newName){
  newName=(newName||'').trim();
  if(!newName||newName===currentDisplay)return;
  try{
    const r=await fetch('/v1/sessions/'+encodeURIComponent(currentAgent)+'/rename',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({display_name:newName})});
    const j=await r.json();
    if(j.ok){
      currentDisplay=j.result.display_name;
      try{localStorage.setItem('arcDisplayName',currentDisplay);}catch(e){}
      const el=$('me-name');if(el)el.textContent=currentDisplay;
      await pollAgents();
      showLocalNotice('You are now known as '+currentDisplay);
    }else{
      showLocalNotice('Rename failed: '+(j.error||'unknown'));
    }
  }catch(e){showLocalNotice('Rename failed.');}
}

function $(id){return document.getElementById(id)}
function esc(s){if(s==null)return'';const d=document.createElement('div');d.textContent=String(s);return d.innerHTML}
function timeFmt(iso){if(!iso)return'';try{return new Date(iso).toLocaleTimeString()}catch(e){return iso}}
function nickColor(name){
  let h=0;for(let i=0;i<name.length;i++)h=((h<<5)-h+name.charCodeAt(i))|0;
  return NICK_COLORS[Math.abs(h)%NICK_COLORS.length];
}

function shouldAutoScroll(){
  const f=$('feed');return f.scrollTop+f.clientHeight>=f.scrollHeight-60;
}

function appendMessages(msgs){
  const f=$('feed');
  const wasEmpty=f.querySelector('.empty');
  if(wasEmpty)f.innerHTML='';
  const doScroll=shouldAutoScroll();
  for(const m of msgs){
    const div=document.createElement('div');
    div.className='msg';
    const isMe=m.from_agent===currentAgent;
    const isSys=m.from_agent==='system';
    if(isMe)div.classList.add('msg-self');
    if(isSys)div.classList.add('msg-system');
    let kindBadge='';
    if(m.kind&&m.kind!=='chat'){
      kindBadge='<span class="badge badge-'+esc(m.kind)+'">'+esc(m.kind)+'</span>';
    }
    const color=isMe?'#38bdf8':isSys?'#64748b':nickColor(m.from_agent);
    div.innerHTML='<span class="msg-time">'+timeFmt(m.ts)+'</span>'
      +'<span class="msg-from" style="color:'+color+'">'+esc(displayNameFor(m.from_agent))+'</span>'
      +kindBadge+esc(m.body);
    f.appendChild(div);
    if(m.id>lastId)lastId=m.id;
  }
  if(doScroll)f.scrollTop=f.scrollHeight;
}

function renderAgents(agents){
  const bar=$('agents-bar');
  if(!agents.length){bar.innerHTML='';return}
  bar.innerHTML=agents.map(a=>
    '<span class="agent-pill"><span class="dot dot-green"></span>'+esc(a.display_name||a.agent_id)+'</span>'
  ).join('');
}

function pollUrl(){
  // Thread takes precedence over channel when set — single source of truth.
  const base=currentThread
    ?'/v1/messages?thread_id='+encodeURIComponent(currentThread)
    :'/v1/messages?channel='+encodeURIComponent(currentChannel);
  return seeded ? base+'&since_id='+lastId : base+'&limit=50';
}

async function pollMessages(){
  try{
    const r=await fetch(pollUrl());
    const j=await r.json();
    if(j.ok){
      if(j.result.length)appendMessages(j.result);
      seeded=true;
    }
    $('status-dot').className='dot dot-green';
    $('status-text').textContent='Connected';
  }catch(e){
    $('status-dot').className='dot dot-red';
    $('status-text').textContent='Disconnected';
  }
}

async function pollAgents(){
  try{
    const r=await fetch('/v1/agents?as='+encodeURIComponent(currentAgent));
    const j=await r.json();
    if(j.ok){
      agentNameMap={};
      for(const a of j.result) agentNameMap[a.agent_id]=a.display_name||a.agent_id;
      renderAgents(j.result);
    }
  }catch(e){}
}

async function loadHubInfo(){
  try{
    const r=await fetch('/v1/hub-info');
    const j=await r.json();
    if(j.ok&&j.result){
      const i=j.result;
      $('hub-info').textContent='Instance: '+(i.instance_id||'?')+' | '+(i.wal_mode?'WAL':'no WAL');
    }
  }catch(e){}
}

let shutdownRemaining=null;

function showShutdownBar(sec){
  const bar=$('shutdown-bar');
  bar.style.display='';
  $('shutdown-msg').textContent='Hub shutting down in '+sec+'s\u2026 (type anything to cancel)';
}
function hideShutdownBar(){
  $('shutdown-bar').style.display='none';
  shutdownRemaining=null;
}
function showLocalNotice(text){
  appendMessages([{from_agent:'system',kind:'notice',body:text,ts:new Date().toISOString(),id:0}]);
}

async function pollShutdownStatus(){
  try{
    const r=await fetch('/v1/shutdown');
    const j=await r.json();
    if(j.ok&&j.result){
      shutdownRemaining=j.result.remaining_sec;
      showShutdownBar(shutdownRemaining);
    }else{
      hideShutdownBar();
    }
  }catch(e){}
}

setInterval(()=>{
  if(shutdownRemaining!==null&&shutdownRemaining>0){
    shutdownRemaining--;
    showShutdownBar(shutdownRemaining);
  }
},1000);

async function sendMessage(){
  const input=$('msg-input');
  const body=input.value.trim();
  if(!body)return;
  input.value='';

  const nickMatch=body.match(/^\/nick\s+(.+)$/i);
  if(nickMatch){
    await renameMe(nickMatch[1]);
    return;
  }
  if(/^\/channels$/i.test(body)){
    try{const r=await fetch('/v1/channels');const j=await r.json();if(j.ok)showLocalNotice('Channels: '+j.result.map(c=>'#'+c.name).join(', '));else showLocalNotice('Failed: '+(j.error||'unknown'));}catch(e){showLocalNotice('Failed to list channels.');}
    return;
  }
  const chMatch=body.match(/^\/channel\s+(\S+)$/i);
  if(chMatch){
    const name=chMatch[1].replace(/^#/,'');currentChannel=name;lastId=0;seeded=false;
    $('feed').innerHTML='<div class="empty">Loading messages...</div>';$('channel-indicator').textContent='#'+name;
    showLocalNotice('Switched to #'+name);await pollMessages();return;
  }
  const dmMatch=body.match(/^\/dm\s+(\S+)\s+([\s\S]+)$/i);
  if(dmMatch){
    const to=dmMatch[1],dmBody=dmMatch[2];
    try{const r=await fetch('/v1/messages',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({from_agent:currentAgent,to_agent:to,channel:'direct',kind:'chat',body:dmBody})});const j=await r.json();if(j.ok)showLocalNotice('DM \u2192 '+to+': '+dmBody);else showLocalNotice('DM failed: '+(j.error||'unknown'));}catch(e){showLocalNotice('DM failed.');}
    return;
  }
  const createMatch=body.match(/^\/create-channel\s+(\S+)$/i);
  if(createMatch){
    const name=createMatch[1].replace(/^#/,'');
    try{const r=await fetch('/v1/channels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,created_by:currentAgent})});const j=await r.json();
      if(j.ok){currentChannel=name;lastId=0;seeded=false;$('feed').innerHTML='<div class="empty">Loading messages...</div>';$('channel-indicator').textContent='#'+name;showLocalNotice('Created and switched to #'+name);await pollMessages();}
      else showLocalNotice('Failed: '+(j.error||'unknown'));}catch(e){showLocalNotice('Failed to create channel.');}
    return;
  }

  const netMatch=body.match(/^\/network\s+(on|off)$/i);
  if(netMatch){
    try{const r=await fetch('/v1/network',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({allow_remote:netMatch[1].toLowerCase()==='on'})});const j=await r.json();if(j.ok)showLocalNotice('Network: remote access '+(j.result.allow_remote?'ON':'OFF'));else showLocalNotice('Failed: '+(j.error||'unknown'));}catch(e){showLocalNotice('Network toggle failed.');}
    return;
  }

  const quitMatch=body.match(/^\/(quit|exit)(?:\s+(\d+))?$/i);
  if(quitMatch){
    const delay=quitMatch[2]!==undefined?parseInt(quitMatch[2],10):60;
    try{
      const r=await fetch('/v1/shutdown',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({delay_sec:delay})
      });
      const j=await r.json();
      if(!j.ok) showLocalNotice('Shutdown failed: '+j.error);
      else await pollShutdownStatus();
    }catch(e){showLocalNotice('Shutdown request failed.');}
    return;
  }

  if(shutdownRemaining!==null){
    try{
      await fetch('/v1/shutdown/cancel',{
        method:'POST',headers:{'Content-Type':'application/json'},body:'{}'
      });
      hideShutdownBar();
    }catch(e){}
  }

  try{
    await fetch('/v1/messages',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({from_agent:currentAgent,channel:currentChannel,kind:'chat',body:body})
    });
    await pollMessages();
  }catch(e){
    input.style.borderColor='#f87171';
    setTimeout(()=>input.style.borderColor='',1500);
  }
}

$('msg-input').addEventListener('keydown',e=>{if(e.key==='Enter')sendMessage()});
$('send-btn').addEventListener('click',sendMessage);
$('inbox-feed').addEventListener('click',e=>{
  const row=e.target.closest('[data-from]');
  if(!row)return;
  const input=$('msg-input');
  input.value='/dm '+row.dataset.from+' ';
  input.focus();
});

async function loadThreads(){
  try{
    const r=await fetch('/v1/threads');
    const j=await r.json();
    if(!j.ok)return;
    const sel=$('thread-picker');
    const keep=currentThread||'';
    sel.innerHTML='<option value="">(all channel messages)</option>'
      +j.result.map(t=>'<option value="'+esc(t.thread_id)+'">'+esc(t.thread_id)+' ('+t.message_count+')</option>').join('');
    sel.value=keep;
  }catch(e){}
}
$('thread-picker').addEventListener('change',e=>{
  currentThread=e.target.value||null;
  lastId=0;seeded=false;
  $('feed').innerHTML='<div class="empty">Loading messages...</div>';
  $('channel-indicator').textContent=currentThread?('thread '+currentThread):'#'+currentChannel;
  pollMessages();
});

function renderInbox(msgs){
  const box=$('inbox-feed');
  if(box.querySelector('.empty'))box.innerHTML='';
  for(const m of msgs){
    const div=document.createElement('div');
    div.className='msg msg-self';
    div.style.cursor='pointer';
    div.title='Click to reply with /dm';
    div.dataset.from=m.from_agent;
    const color=nickColor(m.from_agent);
    div.innerHTML='<span class="msg-time">'+timeFmt(m.ts)+'</span>'
      +'<span class="msg-from" style="color:'+color+'">'+esc(displayNameFor(m.from_agent))+' &rarr; you</span>'
      +esc(m.body);
    box.appendChild(div);
    if(m.id>inboxLastId)inboxLastId=m.id;
  }
  $('inbox-count').textContent=String(box.children.length);
}
async function pollInbox(){
  try{
    const r=await fetch('/v1/inbox/'+encodeURIComponent(currentAgent)+'?since_id='+inboxLastId);
    const j=await r.json();
    if(j.ok&&j.result.length)renderInbox(j.result);
  }catch(e){}
}

bootstrapIdentity().then(async()=>{
  await pollAgents();
  pollMessages();
  loadHubInfo();
  loadThreads();
  pollInbox();
  pollShutdownStatus();
});
setInterval(pollMessages,3000);
setInterval(pollAgents,5000);
setInterval(pollShutdownStatus,5000);
setInterval(loadThreads,10000);
setInterval(pollInbox,3000);
</script>
</body>
</html>"""

# ── MCP Server Adapter ───────────────────────────────────────────────
# Exposes Arc as an MCP (Model Context Protocol) server over stdio.
# JSON-RPC 2.0 over stdin/stdout. Implements initialize, tools/list, tools/call.

_MCP_TOOLS = [
    {"name":"arc_post_message","description":"Post a message to an Arc channel",
     "inputSchema":{"type":"object","properties":{
         "channel":{"type":"string"},"body":{"type":"string"},
         "kind":{"type":"string","default":"chat"},"thread_id":{"type":"string"},
         "to_agent":{"type":"string"}},"required":["channel","body"]}},
    {"name":"arc_poll_messages","description":"Poll for new messages from Arc hub",
     "inputSchema":{"type":"object","properties":{
         "channel":{"type":"string"},"timeout":{"type":"number","default":5},
         "thread_id":{"type":"string"}},"required":[]}},
    {"name":"arc_dm","description":"Send a direct message to another agent",
     "inputSchema":{"type":"object","properties":{
         "to_agent":{"type":"string"},"body":{"type":"string"}},
         "required":["to_agent","body"]}},
    {"name":"arc_list_agents","description":"List live agents on the Arc hub",
     "inputSchema":{"type":"object","properties":{},"required":[]}},
    {"name":"arc_create_channel","description":"Create an Arc channel",
     "inputSchema":{"type":"object","properties":{
         "name":{"type":"string"}},"required":["name"]}},
    {"name":"arc_rpc_call","description":"Send an RPC task_request to another agent and wait for the result",
     "inputSchema":{"type":"object","properties":{
         "to_agent":{"type":"string"},"body":{"type":"string"},
         "timeout":{"type":"number","default":30}},"required":["to_agent","body"]}},
]

def _mcp_handle_tool(client: ArcClient, name: str, args: dict) -> str:
    if name == "arc_post_message":
        r = client.post(args["channel"], args["body"], kind=args.get("kind","chat"),
                        thread_id=args.get("thread_id"), to_agent=args.get("to_agent"))
        return json.dumps(r)
    if name == "arc_poll_messages":
        msgs = client.poll(timeout=float(args.get("timeout",5)),
                           channel=args.get("channel"), thread_id=args.get("thread_id"))
        return json.dumps(msgs)
    if name == "arc_dm":
        return json.dumps(client.dm(args["to_agent"], args["body"]))
    if name == "arc_list_agents":
        return json.dumps(client._call("GET", "/v1/agents")["result"])
    if name == "arc_create_channel":
        return json.dumps(client._call("POST", "/v1/channels", {"name": args["name"]})["result"])
    if name == "arc_rpc_call":
        return json.dumps(client.call(args["to_agent"], args["body"], timeout=float(args.get("timeout",30))))
    raise ValueError(f"unknown tool: {name}")

def _mcp_split_header(buf: str):
    """Split MCP Content-Length header from body, handling both \\r\\n and \\n line endings."""
    for sep in ("\r\n\r\n", "\n\n"):
        if sep in buf:
            return buf.split(sep, 1)
    return None

def run_mcp_server(agent_id: str, base_url: str = DEFAULT_BASE_URL):
    """Run Arc as an MCP server over stdio (JSON-RPC 2.0)."""
    client = ArcClient.quickstart(agent_id, base_url)
    def _write(obj):
        raw = json.dumps(obj)
        frame = f"Content-Length: {len(raw)}\r\n\r\n{raw}".encode("utf-8")
        sys.stdout.buffer.write(frame)
        sys.stdout.buffer.flush()
    def _respond(req_id, result): _write({"jsonrpc":"2.0","id":req_id,"result":result})
    def _error(req_id, code, msg): _write({"jsonrpc":"2.0","id":req_id,"error":{"code":code,"message":msg}})
    buf = ""
    while True:
        try:
            line = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            break
        if not line: break
        buf += line
        # Parse Content-Length header framing (handle both \r\n and \n endings)
        parts = _mcp_split_header(buf)
        if parts is None: continue
        header, rest = parts
        m = re.search(r"Content-Length:\s*(\d+)", header, re.IGNORECASE)
        if not m: buf = rest; continue
        clen = int(m.group(1))
        while len(rest) < clen:
            chunk = sys.stdin.read(clen - len(rest))
            if not chunk: break
            rest += chunk
        body_str, buf = rest[:clen], rest[clen:]
        try: req = json.loads(body_str)
        except json.JSONDecodeError: continue
        rid = req.get("id")
        method = req.get("method","")
        if method == "initialize":
            _respond(rid, {"protocolVersion":"2024-11-05","capabilities":{"tools":{}},
                           "serverInfo":{"name":"arc-mcp","version":__version__}})
        elif method == "notifications/initialized":
            pass  # no response needed for notifications
        elif method == "tools/list":
            _respond(rid, {"tools": _MCP_TOOLS})
        elif method == "tools/call":
            params = req.get("params",{})
            try:
                text = _mcp_handle_tool(client, params.get("name",""), params.get("arguments",{}))
                _respond(rid, {"content":[{"type":"text","text":text}]})
            except Exception as exc:
                _respond(rid, {"content":[{"type":"text","text":str(exc)}],"isError":True})
        elif rid is not None:
            _error(rid, -32601, f"method not found: {method}")


def main():
    ap = argparse.ArgumentParser(prog="arc", description="Arc - single-file agent coordination hub")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6969)
    ap.add_argument("--storage", default="arc.sqlite3")
    ap.add_argument("--allow-remote", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--spool-dir", default=DEFAULT_SPOOL_DIR)
    ap.add_argument("--max-body-chars", type=int, default=128_000, help="Maximum characters in a message body")
    ap.add_argument("--max-attachment-chars", type=int, default=256_000, help="Maximum characters per attachment (JSON-encoded)")
    ap.add_argument("--max-attachments", type=int, default=32, help="Maximum attachments per message")

    ens = sub.add_parser("ensure", help="Start hub if not already running, then exit")
    ens.add_argument("--host", default="127.0.0.1")
    ens.add_argument("--port", type=int, default=6969)
    ens.add_argument("--storage", default="arc.sqlite3")
    ens.add_argument("--spool-dir", default=DEFAULT_SPOOL_DIR)
    ens.add_argument("--timeout", type=float, default=5.0)
    ens.add_argument("--allow-remote", action="store_true")
    ens.add_argument("--max-body-chars", type=int, default=128_000, help="Maximum characters in a message body")
    ens.add_argument("--max-attachment-chars", type=int, default=256_000, help="Maximum characters per attachment (JSON-encoded)")
    ens.add_argument("--max-attachments", type=int, default=32, help="Maximum attachments per message")

    stp = sub.add_parser("stop", help="Stop a running hub")
    stp.add_argument("--host", default="127.0.0.1")
    stp.add_argument("--port", type=int, default=6969)
    stp.add_argument("--storage", default="arc.sqlite3")

    rst = sub.add_parser("reset", help="Stop the hub and delete the database")
    rst.add_argument("--host", default="127.0.0.1")
    rst.add_argument("--port", type=int, default=6969)
    rst.add_argument("--storage", default="arc.sqlite3")

    relay = sub.add_parser("relay", help="Forward file-spooled relay requests to the local hub")
    relay.add_argument("--base-url", default=DEFAULT_BASE_URL)
    relay.add_argument("--spool-dir", default=DEFAULT_SPOOL_DIR)
    relay.add_argument("--poll-interval-sec", type=float, default=0.25)
    relay.add_argument("--request-timeout-sec", type=float, default=30.0)

    mcp = sub.add_parser("mcp", help="Run Arc as an MCP server over stdio")
    mcp.add_argument("--agent", default="mcp-client", help="Agent ID to register as")
    mcp.add_argument("--base-url", default=DEFAULT_BASE_URL)

    smoke = sub.add_parser("smoke-agent", help="Run a deterministic smoke-test role")
    smoke.add_argument("--role", required=True, choices=("smoke-a", "smoke-b", "smoke-c"))
    smoke.add_argument("--transport", required=True, choices=("http", "relay"))
    smoke.add_argument("--base-url", default=DEFAULT_BASE_URL)
    smoke.add_argument("--relay-dir", default=DEFAULT_SPOOL_DIR)
    smoke.add_argument("--channel", default=DEFAULT_CHANNEL)
    smoke.add_argument("--thread-id", default=DEFAULT_THREAD_ID)
    smoke.add_argument("--claim-key", default=DEFAULT_CLAIM_KEY)
    smoke.add_argument("--timeout-sec", type=float, default=120.0)
    smoke.add_argument("--poll-interval-sec", type=float, default=1.0)

    # ── Agent/human convenience CLI built on ArcClient ────────────
    ps = sub.add_parser("post", help="Post a message (or DM with --to)")
    ps.add_argument("--agent", required=True, help="Your agent_id (registers with replace=True)")
    ps.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ps.add_argument("--channel", default="general")
    ps.add_argument("--to", default=None, help="If set, sends as direct message to this agent")
    ps.add_argument("--kind", default="chat", choices=sorted(MSG_KINDS))
    ps.add_argument("--thread-id", default=None)
    ps.add_argument("body")

    pl = sub.add_parser("poll", help="Long-poll /v1/events as an agent (exclude_self by default)")
    pl.add_argument("--agent", required=True)
    pl.add_argument("--base-url", default=DEFAULT_BASE_URL)
    pl.add_argument("--channel", default=None)
    pl.add_argument("--thread-id", default=None)
    pl.add_argument("--timeout", type=float, default=30.0)
    pl.add_argument("--since-id", type=int, default=0)
    pl.add_argument("--include-self", action="store_true", help="Do not set exclude_self (default excludes own posts)")

    wm = sub.add_parser("whoami", help="Print GET /v1/bootstrap for this agent")
    wm.add_argument("--agent", required=True)
    wm.add_argument("--base-url", default=DEFAULT_BASE_URL)

    a = ap.parse_args()
    if a.command == "ensure":
        r = ensure_hub(host=a.host, port=a.port, storage=a.storage, timeout=a.timeout, spool_dir=a.spool_dir,
                      max_body_chars=a.max_body_chars, max_attachment_chars=a.max_attachment_chars,
                      max_attachments=a.max_attachments, allow_remote=a.allow_remote)
        print(json.dumps(r, indent=2))
        raise SystemExit(0 if r.get("running") else 1)
    if a.command == "stop":
        r = stop_hub(storage=a.storage, host=a.host, port=a.port)
        print(json.dumps(r, indent=2))
        raise SystemExit(0 if r.get("stopped") else 1)
    if a.command == "reset":
        r = reset_hub(storage=a.storage, host=a.host, port=a.port)
        print(json.dumps(r, indent=2))
        raise SystemExit(0 if r.get("reset") else 1)
    if a.command == "mcp":
        try:
            run_mcp_server(agent_id=a.agent, base_url=a.base_url)
        except KeyboardInterrupt:
            pass
        return
    if a.command == "relay":
        cfg = FileRelayConfig(
            base_url=a.base_url,
            spool_dir=a.spool_dir,
            poll_interval_sec=a.poll_interval_sec,
            request_timeout_sec=a.request_timeout_sec,
        )
        try:
            run_file_relay(cfg)
        except KeyboardInterrupt:
            pass
        return
    if a.command == "smoke-agent":
        try:
            raise SystemExit(run_smoke_agent(
                role=a.role,
                transport_name=a.transport,
                base_url=a.base_url,
                relay_dir=a.relay_dir,
                channel=a.channel,
                thread_id=a.thread_id,
                claim_key=a.claim_key,
                timeout_sec=a.timeout_sec,
                poll_interval_sec=a.poll_interval_sec,
            ))
        except SmokeError as exc:
            print(f"Smoke test failed: {exc}")
            raise SystemExit(1)
    if a.command in ("post", "poll", "whoami"):
        try:
            client = ArcClient(a.agent, base_url=a.base_url)
            client.register(replace=True)
            if a.command == "post":
                if a.to:
                    r = client.dm(a.to, a.body, kind=a.kind, thread_id=a.thread_id)
                else:
                    r = client.post(a.channel, a.body, kind=a.kind, thread_id=a.thread_id)
                print(json.dumps(r, indent=2))
            elif a.command == "poll":
                client._since_id = a.since_id
                msgs = client.poll(exclude_self=not a.include_self, timeout=a.timeout,
                                   channel=a.channel, thread_id=a.thread_id)
                for m in msgs: print(json.dumps(m))
            else:  # whoami
                print(json.dumps(client.bootstrap(), indent=2))
        except ArcError as exc:
            print(f"arc error: {exc.error}")
            raise SystemExit(1)
        return

    cfg = HubConfig(
        listen_host=a.host,
        port=a.port,
        storage_path=a.storage,
        allow_remote=a.allow_remote,
        log_events=not a.quiet,
        max_body_chars=a.max_body_chars,
        max_attachment_chars=a.max_attachment_chars,
        max_attachments=a.max_attachments,
    )
    try:
        run_server(cfg, spool_dir=a.spool_dir)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
