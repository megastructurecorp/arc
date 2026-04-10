# Megahub Protocol Specification v1

**Status**: Stable draft — all endpoints implemented and tested  
**Transport**: HTTP/1.1 JSON  
**Storage**: SQLite 3 (WAL mode)  
**Dependencies**: Python 3.10+ standard library (reference implementation)

This document is the complete specification for the Megahub agent coordination protocol. Any implementation that conforms to the endpoint contracts, JSON shapes, and state machines described here is a compatible Megahub hub. An agent can build one from this spec alone.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Data Model](#2-data-model)
3. [SQLite Schema](#3-sqlite-schema)
4. [Bootstrapping](#4-bootstrapping)
5. [Endpoints](#5-endpoints)
6. [Error Shape](#6-error-shape)
7. [Session Lifecycle](#7-session-lifecycle)
8. [Claims State Machine](#8-claims-state-machine)
9. [Polling Convention](#9-polling-convention)
10. [Message Kinds & Conventions](#10-message-kinds--conventions)
11. [Attachment Types](#11-attachment-types)
12. [Task Workflow](#12-task-workflow)
13. [File Locks](#13-file-locks)
14. [Structured Subtasks](#14-structured-subtasks)
15. [Hub Discovery & PID File](#15-hub-discovery--pid-file)
16. [Live Dashboard](#16-live-dashboard)
17. [Orchestration CLI](#17-orchestration-cli)
18. [Interop Contract](#18-interop-contract)
19. [Build Your Own](#19-build-your-own)

---

## 1. Overview

Megahub is a local-first HTTP + SQLite coordination service for multi-agent systems. It provides:

- **Sessions**: Agent presence tracking with TTL-based expiry
- **Channels**: Named message streams (broadcast)
- **Messages**: Typed, threaded, with inline or reference attachments
- **Inbox**: Per-agent direct message delivery
- **Claims**: Atomic task ownership with TTL, refresh, and release
- **File Locks**: Per-file advisory locks to prevent edit conflicts
- **Structured Subtasks**: Parent-child task trees with completion rollup
- **PID File Discovery**: Automatic hub discovery via `.megahub.pid`
- **Live Dashboard**: Auto-refreshing HTML dashboard at `GET /`

Agents interact exclusively via HTTP JSON requests. There is no WebSocket, no long-polling, and no push — agents poll with `since_id` for new messages.

The hub binds to `127.0.0.1:6969` by default. All data persists in a single SQLite file (`megahub.sqlite3`).

---

## 2. Data Model

### Channel

| Field        | Type   | Notes                     |
|--------------|--------|---------------------------|
| `name`       | string | Primary key               |
| `created_at` | string | ISO 8601 UTC              |
| `created_by` | string | Nullable                  |
| `metadata`   | object | Arbitrary JSON, default `{}` |

### Session

| Field          | Type    | Notes                              |
|----------------|---------|-------------------------------------|
| `session_id`   | string  | UUID v4, primary key                |
| `agent_id`     | string  | One active session per agent        |
| `display_name` | string  | Defaults to `agent_id`              |
| `capabilities` | array   | List of strings                     |
| `metadata`     | object  | Arbitrary JSON                      |
| `created_at`   | string  | ISO 8601 UTC                        |
| `last_seen`    | string  | ISO 8601 UTC, updated on activity   |
| `active`       | boolean | `false` when closed/expired/replaced |

### Message

| Field         | Type    | Notes                              |
|---------------|---------|-------------------------------------|
| `id`          | integer | Auto-increment, primary key         |
| `ts`          | string  | ISO 8601 UTC                        |
| `from_agent`  | string  | Required                            |
| `to_agent`    | string  | Nullable — if set, message is direct |
| `channel`     | string  | Defaults to `"general"` or `"direct"` |
| `kind`        | string  | One of: `chat`, `notice`, `task`, `claim`, `release`, `artifact` |
| `body`        | string  | Text body (max 128,000 chars default, configurable) |
| `attachments` | array   | List of attachment objects (max 32 default, configurable) |
| `reply_to`    | integer | Nullable — references another `id`  |
| `thread_id`   | string  | Nullable — groups messages           |
| `metadata`    | object  | Arbitrary JSON                       |

### Claim

| Field             | Type    | Notes                              |
|-------------------|---------|-------------------------------------|
| `claim_key`       | string  | Primary key                         |
| `thread_id`       | string  | Nullable                            |
| `task_message_id` | integer | Nullable — links to a message `id`  |
| `owner_agent_id`  | string  | Current holder                      |
| `claimed_at`      | string  | ISO 8601 UTC                        |
| `expires_at`      | string  | ISO 8601 UTC                        |
| `released_at`     | string  | Nullable — set on release           |
| `metadata`        | object  | Arbitrary JSON                      |

### Lock

| Field         | Type    | Notes                              |
|---------------|---------|-------------------------------------|
| `file_path`   | string  | Primary key — path being locked     |
| `agent_id`    | string  | Current holder                      |
| `locked_at`   | string  | ISO 8601 UTC                        |
| `expires_at`  | string  | ISO 8601 UTC                        |
| `released_at` | string  | Nullable — set on release           |
| `metadata`    | object  | Arbitrary JSON                      |

### Task

| Field            | Type    | Notes                                       |
|------------------|---------|----------------------------------------------|
| `task_id`        | integer | Primary key — equals the message `id`        |
| `parent_task_id` | integer | Nullable — references parent task            |
| `channel`        | string  | Channel the task message was posted on       |
| `thread_id`      | string  | Nullable — thread grouping key               |
| `status`         | string  | `"open"` or `"done"`                         |
| `created_at`     | string  | ISO 8601 UTC                                 |
| `completed_at`   | string  | Nullable — set when marked done              |

---

## 3. SQLite Schema

Copy-paste these statements to create a compatible database:

```sql
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS channels (
    name         TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    created_by   TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    from_agent       TEXT NOT NULL,
    to_agent         TEXT,
    channel          TEXT NOT NULL,
    kind             TEXT NOT NULL,
    body             TEXT NOT NULL,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    reply_to         INTEGER,
    thread_id        TEXT,
    metadata_json    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_messages_channel_id   ON messages(channel, id);
CREATE INDEX IF NOT EXISTS idx_messages_to_agent_id  ON messages(to_agent, id);
CREATE INDEX IF NOT EXISTS idx_messages_thread_id    ON messages(thread_id, id);

CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    agent_id          TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    active            INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_sessions_agent        ON sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_active       ON sessions(active, last_seen);

CREATE TABLE IF NOT EXISTS claims (
    claim_key       TEXT PRIMARY KEY,
    thread_id       TEXT,
    task_message_id INTEGER,
    owner_agent_id  TEXT NOT NULL,
    claimed_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    released_at     TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_claims_thread_id ON claims(thread_id);

CREATE TABLE IF NOT EXISTS locks (
    file_path     TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    locked_at     TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    released_at   TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_locks_agent_id ON locks(agent_id);

CREATE TABLE IF NOT EXISTS tasks (
    task_id        INTEGER PRIMARY KEY,
    parent_task_id INTEGER,
    channel        TEXT NOT NULL,
    thread_id      TEXT,
    status         TEXT NOT NULL DEFAULT 'open',
    created_at     TEXT NOT NULL,
    completed_at   TEXT,
    FOREIGN KEY (parent_task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
```

**JSON storage**: Fields ending in `_json` store serialized JSON. The API always returns them parsed: `metadata_json` → `metadata`, `attachments_json` → `attachments`, `capabilities_json` → `capabilities`.

---

## 4. Bootstrapping

On startup, a conforming hub MUST:

1. Initialize the SQLite schema (create tables and indexes if not present)
2. Attempt to enable SQLite WAL mode
3. Ensure default channels exist: `"general"` and `"direct"` (INSERT OR IGNORE)

Stale sessions are filtered by TTL and background pruning. A hub MUST NOT globally deactivate all sessions on startup because that breaks shared-filesystem deployments where multiple hub processes point at the same database.

---

## 5. Endpoints

All JSON endpoints accept and return `Content-Type: application/json`.

Conforming hubs SHOULD include an `X-Megahub-Instance` response header on every response. The value is an opaque, stable identifier for the underlying hub database so clients can detect when they have silently started talking to a different Megahub instance.

### Shared POST Parsing Rules

All POST endpoints apply these guards before dispatching to the handler:

- `Content-Length` must be an integer >= 0
- Request bodies larger than `max_body_chars + (max_attachment_chars * max_attachments) + 65536` are rejected with `400`
- Malformed JSON returns `400`
- A valid JSON payload must decode to an object, not an array, string, number, or boolean

With default settings, the request-body hard cap is approximately 8.3 MB. These limits are configurable via `--max-body-chars`, `--max-attachment-chars`, and `--max-attachments` CLI arguments; query `GET /v1/hub-info` for active values.

### 5.1. `POST /v1/sessions` — Create Session

**Request body**:
```json
{
  "agent_id": "my-agent",
  "display_name": "My Agent",
  "capabilities": ["code", "review"],
  "metadata": {},
  "replace": false
}
```

| Field          | Required | Default        | Notes                                |
|----------------|----------|----------------|--------------------------------------|
| `agent_id`     | yes      | —              | Non-empty string                     |
| `display_name` | no       | `agent_id`     |                                      |
| `capabilities` | no       | `[]`           | Array of strings                     |
| `metadata`     | no       | `{}`           | JSON object                          |
| `replace`      | no       | `false`        | If true, deactivate existing session |

**Success response** (`201`):
```json
{
  "ok": true,
  "result": {
    "session_id": "uuid-v4",
    "agent_id": "my-agent",
    "display_name": "My Agent",
    "capabilities": ["code", "review"],
    "metadata": {},
    "created_at": "2025-01-01T00:00:00Z",
    "last_seen": "2025-01-01T00:00:00Z",
    "active": true
  }
}
```

**Error**: `409` if agent already has an active, non-expired session and `replace` is false. `400` for validation errors.

**Behavior**: Only one active session per `agent_id`. If `replace: true`, the existing session is deactivated first. If the existing session's `last_seen` is older than `presence_ttl_sec`, it is treated as expired and replaced regardless of the `replace` flag.

### 5.2. `DELETE /v1/sessions/{session_id}` — Close Session

**Success response** (`200`):
```json
{
  "ok": true,
  "result": { "session_id": "uuid-v4", "deleted": true }
}
```

**Error**: `404` if session not found or already inactive.

### 5.3. `GET /v1/agents` — List Active Agents

Returns all sessions with `active = 1` and `last_seen` within `presence_ttl_sec`. Triggers pruning of expired sessions.

**Response** (`200`):
```json
{
  "ok": true,
  "result": [
    {
      "session_id": "...", "agent_id": "...", "display_name": "...",
      "capabilities": [...], "metadata": {}, "created_at": "...",
      "last_seen": "...", "active": true
    }
  ]
}
```

### 5.4. `GET /v1/channels` — List Channels

**Response** (`200`):
```json
{
  "ok": true,
  "result": [
    { "name": "general", "created_at": "...", "created_by": "system", "metadata": {} },
    { "name": "direct", "created_at": "...", "created_by": "system", "metadata": {} }
  ]
}
```

### 5.5. `POST /v1/channels` — Create Channel

**Request body**:
```json
{ "name": "builds", "created_by": "agent-a", "metadata": {} }
```

**Responses**: `201` if newly created, `200` if already exists (returns existing channel). `400` for validation errors.

### 5.5a. `GET /v1/hub-info` — Hub Storage Metadata

**Response** (`200`):
```json
{
  "ok": true,
  "result": {
    "storage_path": "/abs/path/to/megahub.sqlite3",
    "instance_id": "mh1-0123456789abcdef0123",
    "journal_mode": "wal",
    "wal_mode": true
  }
}
```

**Notes**:
- `storage_path` is the resolved SQLite file path used by this hub process
- `instance_id` is the same opaque identifier surfaced via `X-Megahub-Instance`
- `journal_mode` reports SQLite's actual journal mode for this process
- `wal_mode` is a convenience boolean equivalent to `journal_mode == "wal"`

### 5.6. `POST /v1/messages` — Post Message

**Request body**:
```json
{
  "from_agent": "agent-a",
  "to_agent": null,
  "channel": "general",
  "kind": "chat",
  "body": "Hello world",
  "attachments": [],
  "reply_to": null,
  "thread_id": null,
  "metadata": {}
}
```

| Field        | Required | Default      | Notes                                          |
|--------------|----------|--------------|-------------------------------------------------|
| `from_agent` | yes      | —            | Non-empty string                                |
| `to_agent`   | no       | `null`       | If set, message is direct (inbox delivery)      |
| `channel`    | no       | `"general"`  | `"direct"` if `to_agent` is set                 |
| `kind`       | no       | `"chat"`     | One of: `chat`, `notice`, `task`, `claim`, `release`, `artifact` |
| `body`       | no*      | `""`         | *At least one of `body` or `attachments` required |
| `attachments`| no       | `[]`         | Array of attachment objects (max 16)             |
| `reply_to`   | no       | `null`       | Integer reference to another message `id`        |
| `thread_id`  | no       | `null`       | String grouping key                              |
| `metadata`   | no       | `{}`         | JSON object                                      |

**Success response** (`201`):
```json
{
  "ok": true,
  "result": {
    "id": 1, "ts": "...", "from_agent": "agent-a", "to_agent": null,
    "channel": "general", "kind": "chat", "body": "Hello world",
    "attachments": [], "reply_to": null, "thread_id": null, "metadata": {}
  }
}
```

**Side effect**: Touching the sender's session (`last_seen` refreshed).

**Validation**:
- If `to_agent` is null, the channel must exist
- `kind` must be one of the six valid kinds
- `body` max 128,000 characters (default, configurable via `--max-body-chars`)
- Max 32 attachments (default, configurable via `--max-attachments`), each inline attachment max 256,000 characters JSON-encoded (default, configurable via `--max-attachment-chars`)

### 5.7. `GET /v1/messages` — Query Messages

**Query parameters**:

| Parameter   | Required      | Default | Notes                               |
|-------------|---------------|---------|--------------------------------------|
| `channel`   | yes*          | —       | *At least one of `channel` or `thread_id` |
| `thread_id` | yes*          | —       |                                      |
| `since_id`  | no            | `0`     | Return messages with `id > since_id` |
| `limit`     | no            | `100`   | Capped at `max_query_limit` (500)    |

**Behavior**:
- **Channel query**: Returns messages where `channel = ?` and `to_agent IS NULL` and `id > since_id`, ordered by `id ASC`.
- **Thread query**: Returns messages where `thread_id = ?` and `to_agent IS NULL` and `id > since_id`, ordered by `id ASC`. If `channel` is also provided, it must match too.
- At least one of `channel` or `thread_id` must be provided.

**Response** (`200`):
```json
{ "ok": true, "result": [ { "id": 1, ... }, { "id": 2, ... } ] }
```

**Error**: `400` if neither parameter provided. `404` if channel not found.

### 5.8. `GET /v1/threads` — List Thread Summaries

Returns one summary per known `thread_id`. A thread is discovered from messages, tasks, and claims.

**Response** (`200`):
```json
{
  "ok": true,
  "result": [
    {
      "thread_id": "task-auth-001",
      "channel": "general",
      "root_task_id": 12,
      "latest_message_id": 18,
      "latest_message_ts": "2025-01-01T00:00:00Z",
      "latest_artifact_id": 17,
      "message_count": 5,
      "total_task_count": 2,
      "open_task_count": 1,
      "active_claim_count": 1,
      "active_lock_count": 1,
      "status": "open"
    }
  ]
}
```

**Status derivation**:
- `completed` when the thread has tasks and all of them are done
- `open` when at least one active claim or thread-scoped lock exists
- `waiting` otherwise

**Notes**:
- `root_task_id` is the smallest task id in the thread whose `parent_task_id` is null
- `latest_artifact_id` is the highest message id in the thread with `kind = "artifact"`
- `active_lock_count` counts locks whose `metadata.thread_id` matches the thread and whose lease is active

### 5.9. `GET /v1/threads/{thread_id}` — Get Thread Detail

Returns one thread summary plus the full set of related records for drill-down views.

**Response** (`200`):
```json
{
  "ok": true,
  "result": {
    "thread": {
      "thread_id": "task-auth-001",
      "channel": "general",
      "root_task_id": 12,
      "latest_message_id": 18,
      "latest_message_ts": "2025-01-01T00:00:00Z",
      "latest_artifact_id": 17,
      "message_count": 5,
      "total_task_count": 2,
      "open_task_count": 1,
      "active_claim_count": 1,
      "active_lock_count": 1,
      "status": "open"
    },
    "messages": [
      { "id": 12, "thread_id": "task-auth-001", "...": "..." }
    ],
    "tasks": [
      { "task_id": 12, "parent_task_id": null, "...": "..." }
    ],
    "claims": [
      { "claim_key": "task-12", "thread_id": "task-auth-001", "...": "..." }
    ],
    "locks": [
      { "file_path": "src/auth.py", "metadata": { "thread_id": "task-auth-001" }, "...": "..." }
    ]
  }
}
```

**Error**: `404` if the thread is unknown.

**Behavior**:
- Includes direct and channel messages that share the same `thread_id`
- Includes claims with matching `thread_id`
- Includes locks whose `metadata.thread_id` matches the thread

### 5.10. `GET /v1/events` — Unified Agent Event Feed

Returns the full message stream visible to one agent: broadcast messages plus direct messages addressed to that agent.

**Query parameters**:

| Parameter   | Required | Default | Notes |
|-------------|----------|---------|-------|
| `agent_id`  | yes      | —       | Agent visibility context |
| `channel`   | no       | —       | Optional channel filter |
| `thread_id` | no       | —       | Optional thread filter |
| `since_id`  | no       | `0`     | Return messages with `id > since_id` |
| `limit`     | no       | `100`   | Capped at `max_query_limit` (500) |

**Behavior**:
- Returns messages where `id > since_id` and `(to_agent IS NULL OR to_agent = agent_id)`
- If `channel` is provided, only messages in that channel are returned
- If `thread_id` is provided, only messages in that thread are returned
- Results are ordered by `id ASC`

**Response** (`200`):
```json
{ "ok": true, "result": [ { "id": 1, ... }, { "id": 2, ... } ] }
```

**Error**: `400` if `agent_id` is omitted. `404` if `channel` is provided and does not exist.

### 5.11. `GET /v1/inbox/{agent_id}` — Agent Inbox

Returns direct messages (`to_agent = ?`) for the specified agent.

**Query parameters**: `since_id` (default `0`), `limit` (default `100`).

**Response** (`200`): Same shape as messages query.

### 5.12. `POST /v1/claims` — Acquire or Refresh Claim

**Request body**:
```json
{
  "owner_agent_id": "agent-b",
  "claim_key": "task-42",
  "task_message_id": 42,
  "thread_id": "task-frob-001",
  "ttl_sec": 300,
  "metadata": {}
}
```

| Field             | Required | Default          | Notes                                     |
|-------------------|----------|------------------|--------------------------------------------|
| `owner_agent_id`  | yes      | —                | Non-empty string                           |
| `claim_key`       | no       | `task-{task_message_id}` | Derived if only `task_message_id` given |
| `task_message_id` | no       | —                | Integer                                    |
| `thread_id`       | no       | —                | String                                     |
| `ttl_sec`         | no       | `300`            | Minimum 5                                  |
| `metadata`        | no       | `{}`             | JSON object                                |

At least one of `claim_key` or `task_message_id` must be provided.

**Responses**:

Acquired (`201`):
```json
{ "ok": true, "acquired": true, "result": { "claim_key": "task-42", ... } }
```

Denied — held by another agent (`200`):
```json
{ "ok": true, "acquired": false, "result": { "claim_key": "task-42", "owner_agent_id": "agent-a", ... } }
```

**Side effect**: Touches the owner's session.

### 5.13. `POST /v1/claims/refresh` — Refresh Claim Lease

Extends the TTL on an existing active claim without changing ownership.

**Request body**:
```json
{ "claim_key": "task-42", "owner_agent_id": "agent-b", "ttl_sec": 300 }
```

All fields are required except `ttl_sec`, which defaults to `300` and must be at least `5`.

**Success** (`200`):
```json
{ "ok": true, "acquired": true, "result": { "claim_key": "task-42", ... } }
```

**Error**: `404` if the claim does not exist, is no longer active, or is owned by another agent.

**Side effect**: Touches the owner's session.

### 5.14. `POST /v1/claims/release` — Release Claim

**Request body**:
```json
{ "claim_key": "task-42", "agent_id": "agent-b" }
```

Both fields required. The `agent_id` must match the claim's `owner_agent_id`.

**Success** (`200`):
```json
{ "ok": true, "result": { "claim_key": "task-42", "released_at": "...", ... } }
```

**Error**: `404` if claim not found or not owned by `agent_id`.

**Side effect**: Touches the releaser's session.

### 5.15. `GET /v1/claims` — List Claims

**Query parameters**:

| Parameter     | Default | Notes                               |
|---------------|---------|--------------------------------------|
| `thread_id`   | —       | Filter by thread                     |
| `active_only` | `false` | `true`/`1`/`yes` to exclude released and expired |

**Response** (`200`):
```json
{ "ok": true, "result": [ { "claim_key": "...", ... } ] }
```

Active = `released_at IS NULL AND expires_at >= now`.

### 5.16. `POST /v1/locks` — Acquire or Refresh File Lock

**Request body**:
```json
{
  "agent_id": "agent-b",
  "file_path": "src/main.py",
  "ttl_sec": 300,
  "metadata": {}
}
```

| Field       | Required | Default | Notes                   |
|-------------|----------|---------|--------------------------|
| `agent_id`  | yes      | —       | Non-empty string         |
| `file_path` | yes      | —       | Non-empty string         |
| `ttl_sec`   | no       | `300`   | Minimum 5                |
| `metadata`  | no       | `{}`    | JSON object              |

**Responses**:

Acquired (`201`):
```json
{ "ok": true, "acquired": true, "result": { "file_path": "src/main.py", "agent_id": "agent-b", ... } }
```

Denied — held by another agent (`200`):
```json
{ "ok": true, "acquired": false, "result": { "file_path": "src/main.py", "agent_id": "agent-a", ... } }
```

**Behavior**: Same semantics as claims — same-owner re-acquire refreshes `expires_at`, expired/released locks can be overwritten.

### 5.17. `POST /v1/locks/refresh` — Refresh File Lock Lease

Extends the TTL on an existing active file lock without changing ownership.

**Request body**:
```json
{ "file_path": "src/main.py", "agent_id": "agent-b", "ttl_sec": 300 }
```

All fields are required except `ttl_sec`, which defaults to `300` and must be at least `5`.

**Success** (`200`):
```json
{ "ok": true, "acquired": true, "result": { "file_path": "src/main.py", ... } }
```

**Error**: `404` if the lock does not exist, is no longer active, or is held by another agent.

**Side effect**: Touches the holder's session.

### 5.18. `POST /v1/locks/release` — Release File Lock

**Request body**:
```json
{ "file_path": "src/main.py", "agent_id": "agent-b" }
```

Both fields required. `agent_id` must match the lock holder.

**Success** (`200`):
```json
{ "ok": true, "result": { "file_path": "src/main.py", "released_at": "...", ... } }
```

**Error**: `404` if lock not found or not held by `agent_id`.

### 5.19. `GET /v1/locks` — List File Locks

**Query parameters**:

| Parameter     | Default | Notes                               |
|---------------|---------|--------------------------------------|
| `agent_id`    | —       | Filter by holder                     |
| `active_only` | `false` | Exclude released and expired locks   |

### 5.20. `GET /v1/tasks` — List Tasks

**Query parameters**:

| Parameter   | Default | Notes                             |
|-------------|---------|-----------------------------------|
| `parent_id` | —       | Filter by parent task             |
| `status`    | —       | `"open"` or `"done"`              |
| `channel`   | —       | Filter by channel                 |
| `thread_id` | —       | Filter by thread                  |

**Response** (`200`):
```json
{ "ok": true, "result": [ { "task_id": 1, "parent_task_id": null, "status": "open", ... } ] }
```

### 5.21. `POST /v1/tasks/{task_id}/complete` — Complete a Task

Marks a task as `"done"` and sets `completed_at`. If all sibling subtasks of the same parent are now done, the parent is auto-completed and a `kind: "notice"` message is posted announcing the rollup.

**Success** (`200`):
```json
{ "ok": true, "result": { "task_id": 5, "status": "done", ... } }
```

If parent auto-completed:
```json
{ "ok": true, "result": { ... }, "parent_completed": true }
```

**Error**: `404` if task not found.

### 5.22. `GET /` — Live Dashboard

Serves an auto-refreshing HTML page showing agents, claims, locks, channels, and messages. Zero dependencies — embedded HTML/CSS/JS that fetches from the API endpoints. Refreshes every 5 seconds.

### 5.23. `POST /v1/messages` — Task Auto-Registration

When a message with `kind: "task"` is posted, it is automatically registered in the `tasks` table. An optional `parent_task_id` field in the request body links it to a parent task for subtask trees.

---

## 6. Error Shape

All errors return:
```json
{ "ok": false, "error": "human-readable message" }
```

Common status codes: `400` (validation), `404` (not found), `409` (conflict).

---

## 7. Session Lifecycle

```
        ┌──────────┐
        │  create   │  POST /v1/sessions
        └────┬─────┘
             │
             ▼
     ┌───────────────┐
     │    active      │◄──── touch (on message post, claim acquire/release)
     │  last_seen     │
     └───┬───────┬───┘
         │       │
    close│       │ TTL expired (last_seen + ttl < now)
         │       │
         ▼       ▼
     ┌───────────────┐
     │   inactive     │
     │  active = 0    │
     └───────────────┘
```

- **Create**: Generates UUID v4 session. One active session per `agent_id`.
- **Replace**: `replace: true` deactivates any existing session for the same agent.
- **Touch**: `last_seen` is updated whenever the agent posts a message, acquires or refreshes a claim/lock, or releases a claim/lock.
- **Expiry**: A background timer (every `ttl / 3` seconds) prunes sessions where `last_seen < now - presence_ttl_sec`.
- **Recovery**: After pruning an expired session, the hub releases any active claims and locks still owned by that agent and posts `kind: "notice"` recovery messages so the stranded work becomes visible and reclaimable.
- **Close**: `DELETE /v1/sessions/{id}` sets `active = 0`.
- **Restart**: On server start, all sessions are deactivated (`active = 0`).

---

## 8. Claims State Machine

```
                              ┌────────────────┐
                              │   unclaimed     │
                              │ (no row exists) │
                              └───────┬────────┘
                                      │ POST /v1/claims
                                      │ (first acquire)
                                      ▼
                              ┌────────────────┐
            ┌────────────────►│    active       │◄────── refresh (same owner re-acquires)
            │                 │ released_at=NULL│        → extends expires_at
            │                 │ expires_at > now│
            │                 └──┬──────────┬──┘
            │                    │          │
            │       release      │          │ expires_at < now
            │  POST claims/release           │ (owner abandoned)
            │                    │          │
            │                    ▼          ▼
            │            ┌──────────┐  ┌──────────┐
            │            │ released  │  │ expired   │
            │            │released_at│  │released_at│
            │            │ != NULL   │  │ = NULL    │
            │            └──────────┘  │expires_at  │
            │                          │ < now      │
            │                          └──────────┘
            │                    │          │
            └────────────────────┴──────────┘
                    new acquire (any agent)
                    → overwrites row, resets to active
```

**State transitions**:

| From | Event | To | Condition |
|------|-------|----|-----------|
| (none) | acquire | active | No existing claim row |
| active | acquire (same owner) | active | Refreshes `expires_at` |
| active | refresh (same owner) | active | Refreshes `expires_at` via `POST /v1/claims/refresh` |
| active | acquire (different owner) | active (denied) | Returns existing claim, `acquired: false` |
| active | release (owner) | released | Sets `released_at` |
| active | session expiry | released | Recovery path releases stale work and posts a notice |
| active | release (wrong agent) | (error 404) | Only owner can release |
| active | time passes | expired | `expires_at < now` |
| released | acquire (any) | active | Overwrites row |
| expired | acquire (any) | active | Overwrites row |

---

## 9. Polling Convention

Agents consume messages by polling with `since_id`. This is the standard pattern:

```
Agent                                    Hub
  │                                       │
  │  GET /v1/messages?channel=general     │
  │       &since_id=0                     │
  │──────────────────────────────────────►│
  │  ◄── [msg id=1, msg id=2, msg id=3]  │
  │                                       │
  │  (process messages, remember max id)  │
  │                                       │
  │  GET /v1/messages?channel=general     │
  │       &since_id=3                     │
  │──────────────────────────────────────►│
  │  ◄── [msg id=4]                       │
  │                                       │
  │  (sleep poll_interval, repeat)        │
```

**Rules**:
1. Start with `since_id=0` to get all messages
2. Track the highest `id` seen
3. On each poll, pass `since_id={highest_seen_id}`
4. The server returns messages with `id > since_id`
5. Poll interval is agent-configurable (1 second is typical)

This pattern works for channels (`channel=...`), threads (`thread_id=...`), and inbox (`/v1/inbox/{agent_id}`).

---

## 10. Message Kinds & Conventions

| Kind       | Purpose                                    | Typical sender |
|------------|---------------------------------------------|----------------|
| `chat`     | Free-form conversation                      | Any agent      |
| `notice`   | Operational status, progress, summary       | Any agent      |
| `task`     | Defines a unit of work                      | Requester      |
| `claim`    | Announces intent to work (informational)    | Worker         |
| `artifact` | Delivers completed work product             | Worker         |
| `release`  | Announces relinquishing ownership           | Worker         |

The server stores all kinds identically. Semantic enforcement is the responsibility of participating agents. The `claim` and `release` message kinds are informational — actual locking is done via the claims API.

---

## 11. Attachment Types

### Inline (content stored in message)

| Type   | Required fields | Optional      | Size limit                       |
|--------|-----------------|---------------|----------------------------------|
| `text` | `content`       | —             | 256,000 chars default (JSON-encoded, configurable) |
| `json` | `content`       | —             | 256,000 chars default (JSON-encoded, configurable) |
| `code` | `content`       | `language`    | 256,000 chars default (JSON-encoded, configurable) |

### Reference (pointer to external resource)

| Type       | Required | Optional                                      |
|------------|----------|------------------------------------------------|
| `file_ref` | `path`   | `description`, `start_line`, `end_line`        |
| `diff_ref` | `path`   | `description`, `base`, `head`, `start_line`, `end_line` |

---

## 12. Task Workflow

The canonical five-step lifecycle:

```
1. TASK       Agent A posts kind:"task" with a thread_id
2. CLAIM      Agent B acquires a claim via POST /v1/claims
3. ARTIFACT   Agent B posts kind:"artifact" with attachments
4. RELEASE    Agent B releases the claim via POST /v1/claims/release
5. SUMMARY    Agent A posts kind:"notice" closing the thread
```

**Conventions**:
1. Every task should include a `thread_id` to group all related messages and claims.
2. Use `reply_to` to reference the original task message.
3. Post the artifact before releasing the claim.
4. Set `ttl_sec` proportional to expected task duration.
5. Use `notice` kind for progress updates and summaries.

---

## 13. File Locks

File locks let agents declare intent to edit a specific file, preventing conflicts. They follow the same state machine as claims (acquire → active → release/expire) but keyed by file path instead of claim key.

### State Machine

| From | Event | To | Condition |
|------|-------|----|-----------|
| (none) | acquire | active | No existing lock row |
| active | acquire (same agent) | active | Refreshes `expires_at` |
| active | refresh (same agent) | active | Refreshes `expires_at` via `POST /v1/locks/refresh` |
| active | acquire (different agent) | active (denied) | Returns existing lock, `acquired: false` |
| active | release (holder) | released | Sets `released_at` |
| active | session expiry | released | Recovery path releases stale work and posts a notice |
| active | time passes | expired | `expires_at < now` |
| released/expired | acquire (any) | active | Overwrites row |

### Usage Pattern

```
Agent A: POST /v1/locks  { "agent_id": "a", "file_path": "src/main.py" }
         → acquired: true
Agent B: POST /v1/locks  { "agent_id": "b", "file_path": "src/main.py" }
         → acquired: false, result shows agent "a" holds it
Agent A: POST /v1/locks/release  { "agent_id": "a", "file_path": "src/main.py" }
         → released
```

---

## 14. Structured Subtasks

When a message with `kind: "task"` is posted, it is automatically registered in the `tasks` table. Tasks support parent-child relationships via `parent_task_id`.

### Auto-Registration

When `POST /v1/messages` receives a `kind: "task"` message, a row is inserted into `tasks` with `task_id = message.id`. If the request body includes `parent_task_id`, the task is linked as a subtask.

### Completion Rollup

When `POST /v1/tasks/{id}/complete` marks a subtask as done:

1. If all sibling subtasks (same `parent_task_id`) are now done:
2. The parent task is auto-completed
3. A `kind: "notice"` message is posted to the parent's channel/thread announcing the rollup

This enables hierarchical task decomposition — post a parent task, break it into subtasks, and the parent auto-completes when all children finish.

### Example

```
POST /v1/messages  { "kind": "task", "body": "Build the app", ... }
  → message.id = 1, task_id = 1

POST /v1/messages  { "kind": "task", "body": "Build frontend", "parent_task_id": 1, ... }
  → message.id = 2, task_id = 2

POST /v1/messages  { "kind": "task", "body": "Build backend", "parent_task_id": 1, ... }
  → message.id = 3, task_id = 3

POST /v1/tasks/2/complete  → done
POST /v1/tasks/3/complete  → done, parent_completed: true (task 1 auto-completed)
```

---

## 15. Hub Discovery & PID File

### PID File

When the hub starts, it writes a `.megahub.pid` file in the same directory as the SQLite database:

```json
{ "pid": 12345, "port": 6969, "url": "http://127.0.0.1:6969" }
```

On clean shutdown, the PID file is removed (only if the PID and port match, to avoid removing another instance's file).

### Discovery Algorithm

`ensure_hub()` uses PID file discovery before falling back to direct probing:

```
1. Search for .megahub.pid upward from CWD and the storage directory
2. If found, read the URL and probe it
3. If the probe succeeds → hub is running at that URL
4. Fall back to probing http://{host}:{port}/v1/channels
5. If no hub found → start one in the background
6. Poll until the hub responds or timeout
```

This allows agents in different working directories to find the same hub instance.

### Bootstrap Algorithm

```
1. Discover:  Search for .megahub.pid, then probe http://{host}:{port}/v1/channels
2. If 200 OK → hub is running → proceed to register session
3. If connection refused →
     a. Start the hub as a background process
     b. Poll the probe endpoint every 150ms, up to timeout (default 5s)
     c. If probe succeeds → proceed to register session
     d. If timeout → report error
4. Register: POST /v1/sessions with replace: true
```

**Port binding is the mutex.** Only one process can bind to a port. If two agents race to start the hub, one gets `EADDRINUSE` and the other succeeds. No coordinator needed.

### Reference: `ensure_hub()` API

```
megahub ensure [--host HOST] [--port PORT] [--storage PATH] [--timeout SEC]
python megahub_single.py ensure [--host HOST] [--port PORT] [--storage PATH] [--timeout SEC]
```

Returns JSON:
```json
{ "running": true, "started": true, "url": "http://127.0.0.1:6969" }
```

---

## 16. Live Dashboard

`GET /` serves a zero-dependency, auto-refreshing HTML dashboard. It fetches data from the API endpoints (`/v1/agents`, `/v1/claims`, `/v1/locks`, `/v1/channels`) and renders:

- **Agents** — active agents with display name and last-seen timestamp
- **Active Claims** — held claims with owner and expiry
- **Active Locks** — held file locks with agent and expiry
- **Channels** — all channels with creator

The page refreshes every 5 seconds. No JavaScript framework — just inline `fetch()` calls.

---

## 17. Orchestration CLI

The `megahub orchestrate` command automates the common pattern of seeding a task and waiting for agents to complete:

```bash
megahub orchestrate --task "Build the parser" --agents "alpha,beta,gamma" \
    [--channel NAME] [--thread-id TID] [--timeout 300] [--poll-interval-sec 1]
```

**What it does**:

1. Ensures the hub is running (via `ensure_hub()`)
2. Creates a channel (auto-named from the task slug if not specified)
3. Posts the task as `kind: "task"` on the channel
4. Sends a kickoff DM to each agent with the task, channel, thread, and dashboard URL
5. Polls the thread for completion signals (artifacts or messages containing "complete"/"done")
6. Returns a JSON summary with `completed_agents` and `pending_agents`

**Completion detection**: An agent is considered done when it posts a message with `kind: "artifact"`, or a message containing "complete"/"done"/"finished", or a message with `metadata.complete: true`.

---

## 18. Interop Contract

If you implement these 23 endpoints with these exact JSON request/response shapes, any Megahub client will work with your server:

| # | Method | Path                         | Function              |
|---|--------|------------------------------|-----------------------|
| 1 | GET    | `/`                          | Live dashboard (HTML) |
| 2 | POST   | `/v1/sessions`               | Create session        |
| 3 | DELETE | `/v1/sessions/{session_id}`  | Close session         |
| 4 | GET    | `/v1/agents`                 | List active agents    |
| 5 | GET    | `/v1/channels`               | List channels         |
| 6 | POST   | `/v1/channels`               | Create channel        |
| 7 | GET    | `/v1/hub-info`               | Hub storage metadata  |
| 8 | POST   | `/v1/messages`               | Post message          |
| 9 | GET    | `/v1/messages`               | Query messages        |
| 10| GET    | `/v1/threads`                | List thread summaries |
| 11| GET    | `/v1/threads/{thread_id}`    | Get thread detail     |
| 12| GET    | `/v1/events`                 | Unified event feed    |
| 13| GET    | `/v1/inbox/{agent_id}`       | Agent inbox           |
| 14| POST   | `/v1/claims`                 | Acquire/refresh claim |
| 15| POST   | `/v1/claims/refresh`         | Refresh claim lease   |
| 16| POST   | `/v1/claims/release`         | Release claim         |
| 17| GET    | `/v1/claims`                 | List claims           |
| 18| POST   | `/v1/locks`                  | Acquire/refresh lock  |
| 19| POST   | `/v1/locks/refresh`          | Refresh lock lease    |
| 20| POST   | `/v1/locks/release`          | Release lock          |
| 21| GET    | `/v1/locks`                  | List locks            |
| 22| GET    | `/v1/tasks`                  | List tasks            |
| 23| POST   | `/v1/tasks/{id}/complete`    | Complete task         |

**Success envelope**: `{ "ok": true, "result": ... }`  
**Error envelope**: `{ "ok": false, "error": "..." }`  
**Claims/Locks acquire or refresh**: Adds `"acquired": true/false` to the envelope.

**Timestamp format**: ISO 8601 UTC with seconds precision, `Z` suffix. Example: `2025-01-15T14:30:00Z`.

---

## 19. Build Your Own

Minimal checklist for building a compatible Megahub hub in any language:

### Storage
- [ ] Create the six tables: `channels`, `messages`, `sessions`, `claims`, `locks`, `tasks`
- [ ] Create the indexes for efficient queries
- [ ] Store JSON fields as serialized strings; parse on read
- [ ] Use a mutex/lock for concurrent access (SQLite is single-writer)

### Bootstrapping
- [ ] On startup: attempt WAL mode, ensure `general` and `direct` channels
- [ ] Write `.megahub.pid` with `pid`, `port`, and `url`
- [ ] On shutdown: remove `.megahub.pid` if it matches current process

### Sessions
- [ ] `POST /v1/sessions`: Generate UUID, enforce one-active-per-agent, support `replace`
- [ ] `DELETE /v1/sessions/{id}`: Set `active = 0`
- [ ] `GET /v1/agents`: Return active sessions, prune expired ones first
- [ ] Background timer: prune sessions where `last_seen < now - ttl`

### Messages
- [ ] `POST /v1/messages`: Validate kind, normalize channel, validate attachments
- [ ] Auto-register `kind: "task"` messages in the `tasks` table
- [ ] Support `parent_task_id` for subtask linking
- [ ] Touch sender's session on message post
- [ ] `GET /v1/messages`: Support `channel`, `thread_id`, `since_id`, `limit`
- [ ] `GET /v1/threads`: Return thread summaries with derived status
- [ ] `GET /v1/threads/{thread_id}`: Return summary plus related messages/tasks/claims/locks
- [ ] `GET /v1/events`: Return broadcast plus direct messages visible to one agent
- [ ] `GET /v1/inbox/{agent_id}`: Filter `to_agent = ?`

### Claims
- [ ] `POST /v1/claims`: Atomic acquire with three outcomes: new, refresh, denied
- [ ] `POST /v1/claims/refresh`: Extend an active lease only when the same owner still holds it
- [ ] `POST /v1/claims/release`: Only owner can release
- [ ] `GET /v1/claims`: Support `thread_id` and `active_only` filters
- [ ] On expired session prune: release stale claims and emit recovery notices

### File Locks
- [ ] `POST /v1/locks`: Same acquire semantics as claims, keyed by `file_path`
- [ ] `POST /v1/locks/refresh`: Extend an active lease only when the same holder still holds it
- [ ] `POST /v1/locks/release`: Only holder can release
- [ ] `GET /v1/locks`: Support `agent_id` and `active_only` filters
- [ ] On expired session prune: release stale locks and emit recovery notices

### Tasks
- [ ] `GET /v1/tasks`: Filter by `parent_id`, `status`, `channel`, `thread_id`
- [ ] `POST /v1/tasks/{id}/complete`: Mark done, auto-rollup parent if all siblings done

### Dashboard
- [ ] `GET /`: Serve HTML dashboard (optional but recommended)

### Hub Introspection
- [ ] `GET /v1/hub-info`: Return resolved storage path, instance id, and journal/WAL mode
- [ ] Include `X-Megahub-Instance` on responses

### Validation
- [ ] `from_agent` is required and non-empty on messages
- [ ] `owner_agent_id` is required on claims
- [ ] `agent_id` and `file_path` are required on locks
- [ ] At least one of `claim_key` or `task_message_id` on acquire
- [ ] `kind` must be one of: `chat`, `notice`, `task`, `claim`, `release`, `artifact`
- [ ] Attachment `type` must be one of: `text`, `json`, `code`, `file_ref`, `diff_ref`
- [ ] `ttl_sec` minimum is 5

### Testing
Run the reference test suite against your implementation. The reference test suite is in `tests/` and uses only Python stdlib.

---

## 20. Deployment Modes

Megahub supports two first-class deployment modes.

### Single-Hub Mode

One hub process owns the SQLite database and all agents talk to that one HTTP endpoint.

- Best default for local development
- Simplest mental model: one `localhost`, one process, one pidfile
- Recommended when agents share a network namespace

### Shared-Filesystem Mode

Multiple hub processes point at the same SQLite file on a shared or mounted filesystem.

- Best when agents run in isolated sandboxes, containers, or harnesses where `localhost` is not shared
- Each hub serves its own local agents on its own HTTP port
- State is shared through the SQLite database, not through cross-agent network access
- Every hub MUST point at the same SQLite file, not just the same directory

### Shared-Filesystem Requirements

- SQLite WAL mode SHOULD be enabled; hubs SHOULD report the actual `journal_mode`
- The underlying filesystem must preserve SQLite locking semantics well enough for WAL mode to function correctly
- Clients SHOULD compare `storage_path` and `instance_id` from `GET /v1/hub-info` when verifying they are on the same shared coordination state
- Hubs MUST NOT reset every session on startup, because another live hub process may already be using the shared database

### Process Identity vs. Hub Identity

In shared-filesystem mode, different hub processes may expose different local URLs and pidfiles while still representing the same coordination state.

- **Process identity**: local URL, PID, pidfile
- **Hub identity**: SQLite file plus the stable `instance_id`

`X-Megahub-Instance` exists so agents can distinguish these cases cleanly.
