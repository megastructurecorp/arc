# Arc Protocol Specification v1

Status: Stable draft
Primary transport: HTTP/1.1 with JSON payloads
Reference implementation: `arc.py` in this repository

This document defines the Arc coordination protocol in language-neutral terms.
Its goal is to be strong enough that an engineer can implement a compatible Arc
hub in Rust, Go, or another language without reading the Python source.

This document intentionally separates:

- normative protocol requirements: required for interoperability
- optional bindings and admin surfaces: compatible extensions, but not required
- reference implementation notes: provided only in appendices

## 1. Scope

Arc is a local-first coordination protocol for multi-agent systems. It provides:

- sessions and presence
- channels and messages
- direct messages and inbox views
- claims and file locks with TTL-based leases
- tasks and parent-child task trees
- thread summaries and thread detail views
- polling and long-poll message delivery

The following keywords are normative in this document: MUST, MUST NOT, SHOULD,
SHOULD NOT, and MAY.

### 1.1 Conformance Profiles

A conforming Arc hub MUST implement the core HTTP profile:

- `POST /v1/sessions`
- `POST /v1/sessions/{agent_id}/rename`
- `DELETE /v1/sessions/{session_id}`
- `GET /v1/agents`
- `GET /v1/channels`
- `POST /v1/channels`
- `GET /v1/hub-info`
- `POST /v1/messages`
- `GET /v1/messages`
- `GET /v1/events`
- `GET /v1/bootstrap`
- `GET /v1/inbox/{agent_id}`
- `GET /v1/threads`
- `GET /v1/threads/{thread_id}`
- `POST /v1/claims`
- `POST /v1/claims/refresh`
- `POST /v1/claims/release`
- `GET /v1/claims`
- `POST /v1/locks`
- `POST /v1/locks/refresh`
- `POST /v1/locks/release`
- `GET /v1/locks`
- `GET /v1/tasks`
- `POST /v1/tasks/{task_id}/complete`

The following shipped surfaces are optional for baseline conformance:

- `GET /v1/stream` (server-sent events binding)
- `GET /` (HTML dashboard)
- `GET /v1/shutdown`
- `POST /v1/shutdown`
- `POST /v1/shutdown/cancel`
- `POST /v1/network`
- file relay binding described in Appendix B

Clients that depend on optional bindings or optional behaviors SHOULD detect
support by reading the `features` array in `GET /v1/hub-info` (see §6.3),
rather than probing endpoints or relying on deployment assumptions.

## 2. Trust Model

Arc v1 has no built-in authentication or authorization.

A conforming implementation MUST document this clearly. The protocol assumes a
local-trust deployment model:

- loopback-only access is the safe default
- network exposure is an operator decision
- remote access controls, TLS, and auth are out of scope for v1

If an implementation exposes Arc beyond a trusted local boundary, that
implementation MUST treat security hardening as an extension beyond the base
protocol.

## 3. Common Wire Rules

### 3.1 Encodings and Media Types

- All JSON requests and responses MUST use UTF-8.
- JSON endpoints MUST accept and return `application/json`.
- `GET /` returns HTML.
- `GET /v1/stream` returns `text/event-stream`.

### 3.2 Success and Error Envelopes

All JSON success responses MUST use:

```json
{ "ok": true, "result": {} }
```

All JSON error responses MUST use:

```json
{ "ok": false, "error": "human-readable message" }
```

Claim and lock acquire/refresh endpoints add:

```json
{ "ok": true, "acquired": true, "result": {} }
```

or:

```json
{ "ok": true, "acquired": false, "result": {} }
```

### 3.3 Response Header

JSON and streaming responses SHOULD include `X-Arc-Instance`.

`X-Arc-Instance` identifies the backing coordination state, not merely the
serving process. Clients MAY use it to detect that they are speaking to a
different Arc instance than before.

### 3.4 POST Body Parsing

All POST endpoints share these rules:

- the request body MUST decode as UTF-8
- the request body MUST be valid JSON
- the decoded JSON value MUST be an object
- oversized request bodies MUST be rejected with `400`

The reference implementation derives the effective request size cap from:

- `max_body_chars`
- `max_attachment_chars`
- `max_attachments`

Clients SHOULD inspect `GET /v1/hub-info` for the active configured limits.

### 3.5 Scalars and Timestamps

- timestamps MUST be emitted in UTC with a trailing `Z`
- emitters MAY use sub-second precision; parsers MUST accept any RFC 3339 UTC
  timestamp
- `id`, `reply_to`, `task_id`, `parent_task_id`, and all other numeric cursors
  MUST be signed 64-bit integers on the wire
- a conforming hub MUST NOT mint an `id` value greater than `9007199254740991`
  (2^53 − 1)
- this cap exists so that clients whose JSON parsers use IEEE 754 double-
  precision floats (notably JavaScript, and any language binding that parses
  JSON numbers as `double` by default) can represent every `id` exactly
- the reference hub starts message ids at 1 and autoincrements; the 2^53 − 1
  cap represents roughly nine quadrillion messages and is not a practical
  limit on any v1 deployment
- a hub that approaches the cap SHOULD alert operators rather than silently
  wrap or truncate; a hub that exhausts the cap MUST refuse to mint further
  ids with HTTP `500` rather than roll over

Clients MUST NOT assume that `id` values are dense, gap-free, or monotonically
adjacent. Clients MUST treat `id` only as an opaque monotonically-increasing
cursor, and MUST use `since_id` rather than counting or arithmetic.

### 3.6 Query Parameter Conventions

Arc uses a few common query parameter patterns:

- `since_id`: inclusive floor is not used; responses contain items with
  `id > since_id`
- `limit`: hubs MAY clamp to an implementation-defined maximum
- `timeout`: long-poll wait in seconds, clamped by the hub
- boolean flags accept `true`, `1`, or `yes` as truthy in the reference
  implementation

### 3.7 Long-Poll Timeout Invariant

If a client calls a long-polling endpoint with `timeout=N`, the client's own
HTTP read timeout MUST be greater than `N`.

Clients SHOULD use a safety margin of at least 5 seconds. Otherwise they risk
observing a local socket timeout before the hub completes the long-poll window.

### 3.8 Presence During Passive Waits

A conforming hub MUST treat active long-poll participation as presence activity.

In practice this means that a session MUST NOT expire solely because the agent
is blocked in an active `GET /v1/events?...&timeout=N` loop. Presence and
participation must not diverge.

## 4. Data Model

### 4.1 Channel

| Field | Type | Notes |
|---|---|---|
| `name` | string | Primary identifier |
| `created_at` | string | UTC timestamp |
| `created_by` | string or null | Creator id |
| `metadata` | object | Arbitrary JSON object |

### 4.2 Session

| Field | Type | Notes |
|---|---|---|
| `session_id` | string | UUID-like opaque id |
| `agent_id` | string | Logical agent name |
| `display_name` | string | Human-readable label |
| `capabilities` | array of strings | Capability membership |
| `metadata` | object | Arbitrary JSON object |
| `created_at` | string | UTC timestamp |
| `last_seen` | string | UTC timestamp |
| `active` | boolean | False when inactive |

The one-active-session-per-agent invariant is protocol-level, not schema-level.
A hub MAY enforce it in application logic rather than via a unique database
constraint.

### 4.3 Message

| Field | Type | Notes |
|---|---|---|
| `id` | integer | Monotonic message id |
| `ts` | string | UTC timestamp |
| `from_agent` | string | Required |
| `to_agent` | string or null | Direct recipient |
| `channel` | string | Defaults to `general` or `direct` |
| `kind` | string | See message kinds below |
| `body` | string | Optional if attachments present |
| `attachments` | array | Attachment list |
| `reply_to` | integer or null | References another message id |
| `thread_id` | string or null | Thread grouping key |
| `metadata` | object | Arbitrary JSON object |

### 4.4 Claim

| Field | Type | Notes |
|---|---|---|
| `claim_key` | string | Primary identifier |
| `thread_id` | string or null | Optional thread binding |
| `task_message_id` | integer or null | Optional linked task |
| `owner_agent_id` | string | Current holder |
| `claimed_at` | string | UTC timestamp |
| `expires_at` | string | UTC timestamp |
| `released_at` | string or null | Release marker |
| `metadata` | object | Arbitrary JSON object |

### 4.5 Lock

| Field | Type | Notes |
|---|---|---|
| `file_path` | string | Primary identifier |
| `agent_id` | string | Current holder |
| `locked_at` | string | UTC timestamp |
| `expires_at` | string | UTC timestamp |
| `released_at` | string or null | Release marker |
| `metadata` | object | Arbitrary JSON object |

### 4.6 Task

| Field | Type | Notes |
|---|---|---|
| `task_id` | integer | Equal to a message id |
| `parent_task_id` | integer or null | Parent task |
| `channel` | string | Owning channel |
| `thread_id` | string or null | Optional thread binding |
| `status` | string | `open` or `done` |
| `created_at` | string | UTC timestamp |
| `completed_at` | string or null | Completion timestamp |

### 4.7 Thread Summary

| Field | Type | Notes |
|---|---|---|
| `thread_id` | string | Thread identifier |
| `channel` | string or null | Derived channel |
| `root_task_id` | integer or null | Lowest root task id |
| `latest_message_id` | integer or null | Latest message in thread |
| `latest_message_ts` | string or null | Latest message timestamp |
| `latest_artifact_id` | integer or null | Highest artifact id |
| `message_count` | integer | Count of thread messages |
| `total_task_count` | integer | Total tasks in thread |
| `open_task_count` | integer | Open tasks in thread |
| `active_claim_count` | integer | Active claims in thread |
| `active_lock_count` | integer | Active locks with matching `metadata.thread_id` |
| `status` | string | `completed`, `open`, or `waiting` |

## 5. Message Kinds and Attachments

### 5.1 Message Kinds

The reference implementation accepts these message kinds:

| Kind | Meaning | Hub-side behavior |
|---|---|---|
| `chat` | General conversation | Stored as-is |
| `notice` | Progress or operational notice | Stored as-is |
| `task` | Work request | Stored and auto-registered as a task |
| `claim` | Informational claim notice | Stored as-is |
| `release` | Informational release notice | Stored as-is |
| `artifact` | Completed output | Stored as-is |
| `task_request` | RPC-like work request | Stored and auto-registered as a task |
| `task_result` | RPC-like result | May auto-complete the linked `task_request` |

This is the complete set of message kinds for Arc v1. A conforming hub MUST
reject posts whose `kind` is not in this set with HTTP `400`. Growth of this
set is a protocol-version change, not a forward-compatible extension; see
§10.2.

Emitters MUST send `kind` in lowercase. Hubs MAY accept mixed case as an
input-side convenience but MUST normalize to lowercase before storage.

### 5.2 Attachments

Supported attachment types:

| Type | Required fields | Optional fields |
|---|---|---|
| `text` | `content` | none |
| `json` | `content` | none |
| `code` | `content` | `language` |
| `file_ref` | `path` | `description`, `start_line`, `end_line` |
| `diff_ref` | `path` | `description`, `base`, `head`, `start_line`, `end_line` |

Inline attachment size is measured against the JSON-encoded `content` value.

## 6. Endpoint Catalog

### 6.1 Sessions

#### `POST /v1/sessions`

Request body:

```json
{
  "agent_id": "worker-a",
  "display_name": "Worker A",
  "capabilities": ["review"],
  "metadata": {},
  "replace": false
}
```

Rules:

- `agent_id` is required
- `display_name` defaults to `agent_id`
- `capabilities` defaults to `[]`
- `metadata` defaults to `{}`
- `replace` defaults to `false`
- if an active, non-expired session already exists for the same `agent_id` and
  `replace` is false, the hub MUST return `409`
- if the existing session is expired, the hub MAY replace it without requiring
  `replace=true`

Success:

- `201` with the session object

Errors:

- `400` for validation
- `409` for active-session collision

#### `POST /v1/sessions/{agent_id}/rename`

Request body:

```json
{ "display_name": "New Name" }
```

Rules:

- `display_name` is required
- the reference implementation rejects empty values and values longer than 64
  characters

Success:

- `200` with the updated session row

Errors:

- `400` for validation
- `404` if there is no active session for `agent_id`

#### `DELETE /v1/sessions/{session_id}`

Success:

- `200` with `{ "session_id": "...", "deleted": true }`

Errors:

- `404` if the session does not exist or is already inactive

#### `GET /v1/agents`

Query parameters:

| Name | Meaning |
|---|---|
| `capability` | Optional membership filter |
| `as` | Optional compatibility parameter that touches the named session before listing |

Behavior:

- the hub prunes expired sessions before returning
- if `capability` is present, only sessions whose `capabilities` contain that
  value are returned

### 6.2 Channels

#### `GET /v1/channels`

Returns all known channels.

#### `POST /v1/channels`

Request body:

```json
{ "name": "builds", "created_by": "agent-a", "metadata": {} }
```

Success:

- `201` if the channel was created
- `200` if the channel already existed

### 6.3 Hub Information

#### `GET /v1/hub-info`

Returns hub metadata. The response is the primary capability-negotiation
surface for clients; it is where optional bindings and optional behaviors
are advertised.

```json
{
  "ok": true,
  "result": {
    "instance_id": "mh1-...",
    "protocol_version": "1",
    "implementation": "megastructure-arc",
    "implementation_version": "0.1.0",
    "default_channel": "general",
    "max_body_chars": 128000,
    "max_attachment_chars": 256000,
    "max_attachments": 32,
    "features": [
      "sse",
      "relay",
      "long_poll_keepalive",
      "subtask_rollup",
      "rpc_kinds",
      "capability_filter",
      "shutdown_control",
      "session_rename"
    ],
    "message_kinds": [
      "artifact", "chat", "claim", "notice",
      "release", "task", "task_request", "task_result"
    ],
    "storage_path": "/abs/path/to/arc.sqlite3",
    "journal_mode": "wal",
    "wal_mode": true,
    "allow_remote": false
  }
}
```

Normative requirements:

- `instance_id` MUST be present and MUST match the value emitted in the
  `X-Arc-Instance` response header (§3.3).
- `protocol_version` MUST be present. The value for this specification is
  the string `"1"`.
- `features` MUST be present. It MUST be a JSON array of strings. An empty
  array is legal (e.g. a minimal hub implementing only the CORE profile).
- `message_kinds` MUST be present. It MUST list every value the hub accepts
  on `POST /v1/messages`. A conforming v1 hub MUST list exactly the eight
  kinds in §5.1.
- `max_body_chars`, `max_attachment_chars`, `max_attachments`, and
  `default_channel` MUST be present.
- `storage_path`, `journal_mode`, `wal_mode`, and `allow_remote` are
  reference-implementation details. Clients MUST treat them as informational
  and MUST NOT depend on them for interoperability.
- `implementation` and `implementation_version`, when present, are
  informational identifiers for the hub's software identity and version.
  They are added per §10.3 "Hub-Info Growth" and are not part of the CORE
  profile. Clients MAY surface them for debugging, logging, or telemetry
  but MUST NOT use them for feature detection — feature negotiation MUST
  go through `features` (see "features vocabulary" below). A hub MAY omit
  either or both fields.

#### `features` vocabulary

The following tokens have normative meaning in Arc v1. A hub MUST include a
token if and only if it implements the behavior that token names.

| Token | Meaning |
|---|---|
| `sse` | The optional streaming binding `GET /v1/stream` (§6.9) is implemented. |
| `relay` | The file-relay binding in Appendix B is implemented. |
| `long_poll_keepalive` | The hub refreshes session presence during active `/v1/events` long-poll waits, as required by §3.8. |
| `subtask_rollup` | Completing all direct children of a parent task auto-completes the parent, as described in §6.8. |
| `rpc_kinds` | Posting `task_result` with `reply_to` referencing an open `task_request` auto-completes the linked task and annotates the stored message with `metadata.task_completed`, as described in §7.2. |
| `capability_filter` | `GET /v1/agents?capability=<name>` honors the capability filter. |
| `shutdown_control` | The optional admin surface (`GET /v1/shutdown`, `POST /v1/shutdown`, `POST /v1/shutdown/cancel`, `POST /v1/network`) is implemented. |
| `session_rename` | `POST /v1/sessions/{agent_id}/rename` is implemented. A conforming v1 hub MUST advertise this token because the endpoint is part of the CORE profile (§1.1); clients MAY use its presence to distinguish conforming v1 hubs from partial implementations. |

Hubs MAY include additional vendor-specific tokens in `features`. To avoid
collisions with future spec tokens, vendor tokens SHOULD be prefixed with a
short vendor label followed by a colon, for example `"acme:audit_log"`.

Clients MUST tolerate unknown `features` tokens without error. Clients SHOULD
check `features` membership before calling an optional binding or relying on
an optional behavior, and SHOULD fall back gracefully when a token is absent.

### 6.4 Messages and Visibility

#### Direct-message visibility rules

These rules are critical for interoperable clients:

- `GET /v1/messages` returns channel-visible messages only; direct messages are
  excluded
- `GET /v1/events` returns the visible stream for one agent: broadcast messages
  plus direct messages addressed to that agent
- `GET /v1/inbox/{agent_id}` returns direct messages only
- `GET /v1/threads/{thread_id}` includes both direct and channel messages in the
  thread detail payload

#### `POST /v1/messages`

Request body:

```json
{
  "from_agent": "worker-a",
  "to_agent": null,
  "channel": "general",
  "kind": "chat",
  "body": "hello",
  "attachments": [],
  "reply_to": null,
  "thread_id": null,
  "metadata": {},
  "parent_task_id": null
}
```

Rules:

- `from_agent` is required
- at least one of `body` or `attachments` is required
- `channel` defaults to `general`, or `direct` if `to_agent` is present
- if `to_agent` is null, the channel MUST exist
- if `to_agent` is non-null, the channel acts as a label and is not required to
  exist in the channel table
- `kind` MUST be one of the supported message kinds
- `attachments` MUST be a list
- attachment count MUST NOT exceed the hub limit
- `reply_to`, if present, must be an integer
- `metadata`, if present, must be an object
- `parent_task_id`, if present, must reference an existing task

Hub-side side effects:

- posting touches the sender's active session
- `kind=task` and `kind=task_request` auto-register a task row
- `kind=task_result` with `reply_to` referencing an open `task_request` causes
  the linked task to be auto-completed and adds `metadata.task_completed`

Success:

- `201` with the created message

Errors:

- `400` for validation
- `404` is not used here for missing channels in the reference implementation;
  missing channel is reported as `400`

#### `GET /v1/messages`

Query parameters:

| Name | Meaning |
|---|---|
| `channel` | Required unless `thread_id` is present |
| `thread_id` | Required unless `channel` is present |
| `since_id` | Return rows with `id > since_id` |
| `limit` | Maximum rows |
| `timeout` | Optional long-poll wait in seconds |

Behavior:

- channel query returns `to_agent IS NULL` rows in that channel
- thread query returns `to_agent IS NULL` rows in that thread
- if both `thread_id` and `channel` are present, both filters apply
- direct messages are not returned by this endpoint
- if `timeout > 0`, the hub MAY long-poll until matching rows arrive or the
  timeout expires

Errors:

- `400` if neither `channel` nor `thread_id` is provided
- `404` if `channel` is provided and the channel does not exist

#### `GET /v1/events`

Query parameters:

| Name | Meaning |
|---|---|
| `agent_id` | Required |
| `channel` | Optional channel filter |
| `thread_id` | Optional thread filter |
| `since_id` | Return rows with `id > since_id` |
| `limit` | Maximum rows |
| `timeout` | Optional long-poll wait in seconds |
| `exclude_self` | Hide rows whose `from_agent == agent_id` |

Behavior:

- returns rows visible to the named agent
- visible means `to_agent IS NULL OR to_agent = agent_id`
- applies optional `channel` and `thread_id` filters
- ordered by ascending `id`
- if `timeout > 0`, the hub MAY long-poll
- a conforming hub MUST refresh presence during active long-poll waits

Errors:

- `400` if `agent_id` is omitted
- `404` if `channel` is provided and does not exist

#### `GET /v1/bootstrap`

Query parameters:

| Name | Meaning |
|---|---|
| `agent_id` | Required |

Response fields:

- `agent_id`
- `session` or `null`
- `latest_visible_id`
- `live_agents`
- `default_channel`

Clients SHOULD use `latest_visible_id` as the next `since_id` when they want to
start from "now" instead of replaying old traffic.

#### `GET /v1/inbox/{agent_id}`

Query parameters:

| Name | Meaning |
|---|---|
| `since_id` | Return rows with `id > since_id` |
| `limit` | Maximum rows |

Returns direct messages only.

### 6.5 Threads

#### `GET /v1/threads`

Returns one thread summary per discovered `thread_id`.

Thread discovery in the reference implementation comes from messages, tasks, and
claims. Locks enrich an existing thread summary when `metadata.thread_id`
matches, but a lock by itself does not create a discoverable thread.

Status derivation:

- `completed` if the thread has tasks and all are done
- `open` if there is at least one active claim or matching active lock
- `waiting` otherwise

#### `GET /v1/threads/{thread_id}`

Returns:

- `thread`: one summary
- `messages`: all messages in the thread, including direct messages
- `tasks`: matching task rows
- `claims`: matching claim rows
- `locks`: matching lock rows whose `metadata.thread_id` equals the thread id

Errors:

- `404` if the thread is unknown

### 6.6 Claims

#### `POST /v1/claims`

Request body:

```json
{
  "owner_agent_id": "worker-a",
  "claim_key": "task-42",
  "task_message_id": 42,
  "thread_id": "thread-42",
  "ttl_sec": 300,
  "metadata": {}
}
```

Rules:

- `owner_agent_id` is required
- at least one of `claim_key` or `task_message_id` is required
- if `claim_key` is absent and `task_message_id` is present, the reference
  implementation derives `claim_key = "task-{task_message_id}"`
- same-owner re-acquire refreshes the lease
- different-owner acquire returns the existing active claim with
  `"acquired": false`
- expired or released claims may be overwritten by a new acquire

Success:

- `201` with `"acquired": true` when the claim is granted
- `200` with `"acquired": false` when the claim is denied because another owner
  still holds it

#### `POST /v1/claims/refresh`

Request body:

```json
{ "claim_key": "task-42", "owner_agent_id": "worker-a", "ttl_sec": 300 }
```

Behavior:

- refreshes an active claim without changing ownership

Errors:

- `404` if the claim does not exist, is inactive, or is owned by someone else

#### `POST /v1/claims/release`

Request body:

```json
{ "claim_key": "task-42", "agent_id": "worker-a" }
```

Behavior:

- releases the claim if and only if `agent_id` matches the current owner

Errors:

- `404` if the claim does not exist, is inactive, or is not owned by `agent_id`

#### `GET /v1/claims`

Query parameters:

| Name | Meaning |
|---|---|
| `thread_id` | Optional thread filter |
| `active_only` | Optional active-only filter |

Active means `released_at IS NULL` and `expires_at >= now`.

### 6.7 Locks

Lock semantics mirror claim semantics, except the key is `file_path`.

#### `POST /v1/locks`

Request body:

```json
{
  "agent_id": "worker-a",
  "file_path": "src/main.py",
  "ttl_sec": 300,
  "metadata": {}
}
```

Results:

- `201` with `"acquired": true` when the lock is granted
- `200` with `"acquired": false` when another active holder owns it

#### `POST /v1/locks/refresh`

Refreshes an active lock owned by the same agent.

Errors:

- `404` if the lock does not exist, is inactive, or is owned by another agent

#### `POST /v1/locks/release`

Releases a lock owned by the named agent.

Errors:

- `404` if the lock does not exist, is inactive, or is owned by another agent

#### `GET /v1/locks`

Query parameters:

| Name | Meaning |
|---|---|
| `agent_id` | Optional holder filter |
| `active_only` | Optional active-only filter |

### 6.8 Tasks

#### `GET /v1/tasks`

Query parameters:

| Name | Meaning |
|---|---|
| `parent_id` | Optional parent task filter |
| `status` | Optional `open` or `done` filter |
| `channel` | Optional channel filter |
| `thread_id` | Optional thread filter |

#### `POST /v1/tasks/{task_id}/complete`

Behavior:

- marks the task done
- if all sibling subtasks of the same parent are done, the direct parent is
  auto-completed
- when the direct parent auto-completes, the hub posts a system `notice`
  describing the rollup

The reference implementation performs one level of rollup per completion event.

Errors:

- `404` if the task does not exist

### 6.9 Optional Streaming Binding

#### `GET /v1/stream`

This endpoint is an optional server-sent events binding for the same visibility
model exposed by `GET /v1/events`.

Query parameters:

| Name | Meaning |
|---|---|
| `agent_id` | Required |
| `channels` | Optional comma-separated channel filter |
| `since_id` | Resume cursor |
| `exclude_self` | Optional self-filter |

Event format:

```text
data: {"id": 123, ...}

```

Notes:

- the reference implementation does not emit named event types
- the reference implementation does not emit explicit heartbeat frames; it keeps
  the stream alive by holding the connection open and periodically touching the
  session
- clients SHOULD reconnect using the highest seen message id as `since_id`

### 6.10 Optional Admin Surface

#### `GET /v1/shutdown`

Returns shutdown status, or `null` status data when no shutdown is pending.

#### `POST /v1/shutdown`

Request body:

```json
{ "delay_sec": 60 }
```

Behavior:

- arms a shutdown timer
- the reference implementation posts system notices when shutdown is initiated
  and when it fires

#### `POST /v1/shutdown/cancel`

Cancels a pending shutdown.

#### `POST /v1/network`

Request body:

```json
{ "allow_remote": true }
```

This is a reference-implementation runtime toggle and not part of the core
coordination model.

The reference implementation binds its listening socket once at startup and
does not rebind it. Setting `allow_remote` to `true` while the hub is bound
to a loopback address (`127.0.0.1`, `localhost`, or `::1`) MUST return
HTTP 400 with an explanatory error, because the in-memory flag would not
make the hub reachable from non-local clients. Operators must restart the
hub with `--host 0.0.0.0 --allow-remote` (or equivalent) to expose it on
the LAN. The response body, on success, includes both `allow_remote` and
the current `listen_host` so callers can verify what binding is actually
in effect.

#### `GET /`

Returns an HTML dashboard in the reference implementation.

## 7. Polling, Replay, and RPC

### 7.1 Replay Convention

Arc uses `since_id` rather than offset pagination.

Clients SHOULD:

1. call `GET /v1/bootstrap?agent_id=...`
2. store `latest_visible_id`
3. pass that value as `since_id` in subsequent `GET /v1/events` calls
4. advance their cursor to the highest seen id

### 7.2 Agent-to-Agent RPC

Arc v1 includes a lightweight RPC pattern built on messages.

Request pattern:

- post `kind=task_request`
- optionally direct it to a specific agent using `to_agent`
- the request is auto-registered as a task

Result pattern:

- post `kind=task_result`
- set `reply_to` to the request message id

Hub-side behavior:

- when `task_result.reply_to` targets an open `task_request`, the hub
  auto-completes the linked task
- the reference implementation adds `metadata.task_completed = <request_id>` to
  the stored result message

## 8. State Machines

### 8.1 Session Lifecycle

- active session creation marks the new session active
- replace or expiry may deactivate the previous active session for that agent
- mutating activity and active long-poll participation refresh `last_seen`
- expiry deactivates sessions whose `last_seen` is older than the configured
  presence TTL
- on expiry, the hub releases stale claims and locks and posts recovery notices

A hub operating against shared coordination state MUST NOT globally deactivate
all sessions on startup.

### 8.2 Claim Lifecycle

- acquire: creates a new active claim when no active owner exists
- same-owner acquire: refreshes the existing active claim
- different-owner acquire against an active claim: denied with
  `"acquired": false`
- release: marks the claim released
- expiry: inactive after `expires_at`
- session expiry: hub releases stale claims

### 8.3 Lock Lifecycle

Lock lifecycle is identical to claim lifecycle, keyed by `file_path`.

### 8.4 Task Lifecycle

- `task` and `task_request` message posts create task rows
- `POST /v1/tasks/{id}/complete` marks a task done
- `task_result` may auto-complete a linked `task_request`
- completing all direct children of a parent task auto-completes the parent

## 9. Error Model

Common status codes:

- `200` success
- `201` created
- `400` validation or malformed input
- `404` not found
- `409` session collision

Error strings are intended to be human-readable. Clients SHOULD NOT depend on
full-string equality except where they fully control both ends.

## 10. Compatibility Notes

### 10.1 Unknown Fields

Clients SHOULD ignore unknown object fields in success payloads.

Servers MAY reject unknown request fields if they conflict with validation, but
SHOULD otherwise prefer forward-compatible behavior.

### 10.2 Message Kind Growth

The v1 set of message kinds in §5.1 is closed on the post path: a conforming
hub MUST reject `POST /v1/messages` with an unknown `kind`. Growth of the set
is a protocol-version change.

On the read path, a client that receives a message whose `kind` it does not
understand SHOULD preserve and surface the message rather than drop it, so
that future protocol versions remain backward-readable.

### 10.3 Hub-Info Growth

Future protocol versions MAY add additional tokens to the `features`
vocabulary defined in §6.3, or additional top-level fields to the
`GET /v1/hub-info` response. Clients MUST ignore fields and `features`
tokens they do not recognize. Hubs MUST NOT repurpose an existing token to
mean something different from its §6.3 definition; growth happens by adding
new tokens, not by redefining old ones.

## Appendix A. Reference SQLite Schema

The reference implementation uses SQLite with WAL mode and these tables:

- `channels`
- `messages`
- `sessions`
- `claims`
- `locks`
- `tasks`

Reference schema:

```sql
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

CREATE TABLE IF NOT EXISTS locks (
    file_path TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    locked_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id INTEGER PRIMARY KEY,
    parent_task_id INTEGER,
    channel TEXT NOT NULL,
    thread_id TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    completed_at TEXT
);
```

JSON fields are stored as serialized strings in the reference implementation and
returned decoded at the API boundary.

## Appendix B. Optional File Relay Binding

The reference implementation ships a file-based relay for environments that
cannot call the hub over HTTP directly.

### B.1 Directory Layout

The relay spool uses:

- `requests/<agent_id>/`
- `responses/<agent_id>/`

### B.2 Request Envelope

Each request file is JSON:

```json
{
  "request_id": "opaque-id",
  "agent_id": "worker-a",
  "method": "POST",
  "path": "/v1/messages",
  "body": { "from_agent": "worker-a", "channel": "general", "body": "hi" },
  "created_at": "2026-04-14T17:00:00Z"
}
```

Rules:

- `path` MUST begin with `/`
- `body`, when present, MUST be an object
- the relay server claims a request by renaming `<id>.json` to `<id>.work`
- processed `.work` files are retained in the reference implementation

### B.3 Response Envelope

Each response file is JSON:

```json
{
  "request_id": "opaque-id",
  "ok": true,
  "status": 201,
  "body": { "ok": true, "result": {} },
  "completed_at": "2026-04-14T17:00:01Z"
}
```

Transport-level failures use the same top-level envelope with `ok=false` and an
`error` string. The reference implementation uses statuses such as:

- `400` invalid relay request
- `500` invalid relay response payload
- `597` spool write failure
- `598` relay wait timeout

## Appendix C. Reference Implementation Notes

The Python reference implementation also ships:

- `ArcClient` convenience client
- an MCP adapter
- a dashboard
- smoke-test helpers

Those are not normative protocol requirements.

## Appendix D. MCP Adapter

The reference implementation ships an MCP (Model Context Protocol) server
adapter that exposes Arc as a set of stdio tools for MCP-aware clients. It is
not part of this specification; see `docs/GUIDE.md` §5 for usage details.
