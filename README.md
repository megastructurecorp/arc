# Megahub

Local-first agent coordination hub. HTTP + SQLite. Zero dependencies, zero config, no cloud.

Megahub gives multiple AI agents (or any software agents) a lightweight way to coordinate work on a single machine. Agents register sessions, post messages on channels and threads, claim tasks to prevent conflicts, deliver artifacts, and release ownership — all through a simple REST API backed by SQLite.

**Zero external dependencies.** The entire project runs on Python 3.10+ standard library. No pip install required beyond the package itself.

## Features

- **HTTP REST API** — 17 endpoints for messages, channels, sessions, claims, locks, tasks, and inbox
- **SQLite persistence** — no external database required
- **Claims with TTL** — atomic task locking with stale-claim recovery
- **File locks** — per-file advisory locks to prevent edit conflicts between agents
- **Structured subtasks** — parent-child task trees with automatic completion rollup
- **Live dashboard** — auto-refreshing HTML dashboard at `GET /` showing agents, claims, locks, channels
- **PID file discovery** — `.megahub.pid` enables agents in different directories to find the hub
- **Orchestration CLI** — `megahub orchestrate` seeds a task, dispatches to agents, and polls for completion
- **Thread-scoped workflows** — group tasks, artifacts, and status updates
- **Polling-based** — agents poll with `since_id` for new messages (simple, reliable)
- **Persistent bridges** — long-lived agent connections with handler plugins
- **Round-robin coordination** — built-in deterministic multi-agent turn-taking
- **Six message kinds** — `task`, `claim`, `artifact`, `release`, `notice`, `chat`
- **Attachment support** — inline `text`/`json`/`code` and reference `file_ref`/`diff_ref`
- **Single-file edition** — drop one ~700-line Python file into any project
- **Implementation-complete spec** — rebuild it from scratch in any language using [docs/PROTOCOL.md](docs/PROTOCOL.md)
- **Localhost by default** — no auth needed for local development

## Quickstart

### Option A: Let an agent start it (recommended)

The `ensure` command checks if the hub is running and starts it if not. The port binding acts as a natural mutex — only one process can bind, so multiple agents can all safely call this:

```bash
megahub ensure              # Package install
python megahub_single.py ensure   # Single file
```

Output:

```json
{ "running": true, "started": true, "url": "http://127.0.0.1:8765" }
```

### Option B: Start manually

```bash
megahub serve               # Package install
python megahub_single.py    # Single file
```

The hub is now running at `http://127.0.0.1:8765`. In another terminal:

```bash
# Post a task
megahub send --from-agent alice --kind task --body "Implement the parser" --thread-id task-001

# Claim it
megahub claim --owner bob --task-message-id 1 --thread-id task-001

# Deliver an artifact
megahub send --from-agent bob --kind artifact --body "Parser complete" --thread-id task-001 --reply-to 1

# Release the claim
megahub release --key task-1 --agent bob

# View the thread
megahub messages --thread-id task-001
```

## Deployment Modes

Megahub supports two first-class deployment modes. Both use the same protocol, the same CLI, and the same SQLite storage — they differ only in how hub processes relate to the database file.

### Mode 1: Single Hub (default)

One hub process serves all agents. This is the simplest setup and the default quickstart path.

```
Agent A ──► ┌──────────┐
Agent B ──► │  Hub :8765│──► megahub.sqlite3
Agent C ──► └──────────┘
```

All agents connect to the same hub URL (default `http://127.0.0.1:8765`). The port binding acts as a natural mutex — only one process can bind, so `megahub ensure` is safe to call from every agent.

### Mode 2: Shared-Filesystem Coordination

Multiple hub processes, each on a different port or machine, all point `--storage` at the **same SQLite file** on a shared or mounted filesystem. Each hub serves its own set of agents, but they all read and write the same database.

```
┌─────────────────────────┐     ┌─────────────────────────┐
│  Container / Sandbox A  │     │  Container / Sandbox B  │
│  Agent A ──► Hub :8765  │     │  Agent B ──► Hub :9876  │
└────────────┬────────────┘     └────────────┬────────────┘
             │                               │
             └───────► megahub.sqlite3 ◄─────┘
                   (shared filesystem mount)
```

Start each hub pointing at the shared file:

```bash
# On machine/container A:
megahub serve --port 8765 --storage /shared/mount/megahub.sqlite3

# On machine/container B:
megahub serve --port 9876 --storage /shared/mount/megahub.sqlite3
```

**Why this works:** SQLite in WAL (Write-Ahead Logging) mode supports concurrent readers and writers across multiple processes on the same filesystem. Megahub enables WAL mode automatically. Messages, claims, locks, and threads posted by any agent on any hub are immediately visible to all other agents on all other hubs.

**When to use this mode:**

- Agents run in different containers or sandboxes that cannot reach each other's `localhost`
- CI/CD pipelines where each step runs in an isolated environment but shares a volume
- Desktop agent harnesses (like Cowork, Claude Code, or similar) where each agent session has its own network namespace but can mount a shared directory
- Any situation where agents cannot connect to a single hub URL but can share a filesystem

**Important constraints:**

- All hubs must point at the **same file**, not just the same directory
- The shared filesystem must support POSIX file locking (most local and NFS v3+ mounts do; some FUSE-based mounts may not)
- Use `megahub status` to verify all hubs report the same storage path
- Each hub has its own `X-Megahub-Instance` header so agents can tell which hub they're connected to

**Verifying it works:**

```bash
# On machine A:
megahub send --from-agent alice --kind chat --body "Hello from A" --thread-id test-shared

# On machine B:
megahub messages --thread-id test-shared
# → Shows alice's message
```

## Task Workflow

Megahub is built around a five-step task lifecycle:

```
1. TASK       Post kind:"task" with a thread_id
2. CLAIM      Acquire a claim referencing the task
3. ARTIFACT   Post kind:"artifact" with attachments
4. RELEASE    Release the claim
5. SUMMARY    Post kind:"notice" to close the thread
```

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for the full protocol specification — detailed enough to reimplement the hub in any language.

## Thread Command Center

Megahub now provides a thread-centric view of all active work. From the CLI:

```bash
# List all active threads with status, task counts, and last activity
megahub thread

#   THREAD                               STATUS      TASKS    CLAIMS   LAST ACTIVITY
#   improve-megahub-001-p1a              open        1/1      1        9m ago
#   improve-megahub-001-p1b              waiting     1/1      0        11m ago

# Drill into a specific thread
megahub thread improve-megahub-001-p1a

#   Thread: improve-megahub-001-p1a
#   Channel: dev-megahub
#   Status: open
#   Root Task: #2 - P1a: Thread command center...
#   Tasks (1): ...
#   Active Claims (1): task-2 owned by agent-b expires in 49m
#   Active Locks (0): (none)

# Replay the full history of a thread as a chronological narrative
megahub replay --thread-id improve-megahub-001-p1a
```

The live dashboard at `GET /` also shows an Active Threads table. Click any thread row to see its tasks, claims, locks, and messages in a drill-down panel.

## Python Client

```python
from megahub import MegahubClient

with MegahubClient("http://127.0.0.1:8765") as client:
    # Register
    client.open_session("my-agent", display_name="My Agent")

    # Post a task
    resp = client.send_message({
        "from_agent": "my-agent",
        "channel": "general",
        "kind": "task",
        "body": "Refactor the auth module",
        "thread_id": "task-auth-001",
    })
    task_id = resp["result"]["id"]

    # Claim it
    client.acquire_claim(
        "my-agent",
        task_message_id=task_id,
        thread_id="task-auth-001",
    )

    # Post artifact and release
    client.send_message({
        "from_agent": "my-agent",
        "kind": "artifact",
        "body": "Auth refactor complete",
        "thread_id": "task-auth-001",
        "reply_to": task_id,
        "attachments": [{"type": "code", "language": "python", "content": "..."}],
    })
    client.release_claim(f"task-{task_id}", "my-agent")
```

## CLI Reference


| Command                       | Description                                       |
| ----------------------------- | ------------------------------------------------- |
| `megahub ensure`              | Start the hub if not already running, then exit   |
| `megahub serve`               | Start the hub daemon (foreground)                 |
| `megahub orchestrate`         | Seed a task, dispatch to agents, poll for completion |
| `megahub agents`              | List active agents                                |
| `megahub channels`            | List channels                                     |
| `megahub create-channel NAME` | Create a channel                                  |
| `megahub send`                | Send a message                                    |
| `megahub messages [CHANNEL]`  | Read channel or thread messages                   |
| `megahub inbox AGENT_ID`      | Read an agent's inbox                             |
| `megahub status`              | Show hub URL, PID, storage path, agents, threads  |
| `megahub thread`              | List all active threads with status summary       |
| `megahub thread THREAD_ID`    | Show thread detail: tasks, claims, locks, artifacts |
| `megahub replay --thread-id`  | Replay a thread as a chronological narrative      |
| `megahub claim`               | Acquire a claim                                   |
| `megahub release`             | Release a claim                                   |
| `megahub refresh-claim`       | Extend TTL of a held claim                        |
| `megahub refresh-lock`        | Extend TTL of a held file lock                    |
| `megahub claims`              | List claims                                       |
| `megahub bridge`              | Run a persistent polling bridge                   |


All commands accept `--base-url` (default `http://127.0.0.1:8765`).

### Serve options

```
megahub serve [--host HOST] [--port PORT] [--storage PATH]
              [--presence-ttl SEC] [--allow-remote] [--quiet-events]
```

### Send options

```
megahub send --from-agent ID [--channel CH] [--to-agent ID] [--kind KIND]
             [--body TEXT] [--thread-id TID] [--reply-to MSG_ID]
             [--metadata JSON]
```

### Orchestrate options

```
megahub orchestrate --task "description" --agents "alpha,beta,gamma"
                    [--channel NAME] [--thread-id TID]
                    [--timeout 300] [--poll-interval-sec 1]
```

### Bridge options

```
megahub bridge --agent-id ID [--display-name NAME] [--capability CAP]
               [--channel CH] [--thread-id TID]
               [--handler-command CMD | --builtin-handler {thread-reply,round-robin}]
               [--agent-name NAME] [--handler-style {ack,review,concise}]
               [--poll-interval-sec SEC] [--refresh-every SEC]
               [--use-events]
```

## Sample Agent Prompts

The real power of Megahub is what you tell the agents. Below are copy-paste prompts for common multi-agent patterns. Every prompt includes the **bootstrap step** — agents start the hub themselves if it's not running.

### The Bootstrap Protocol

Every agent gets the same bootstrap instructions. No agent is "special" — whoever runs first starts the hub, the rest just connect. The port binding is the mutex.

**Standard bootstrap (single hub — agents share localhost):**

> **Before doing any coordination**, ensure the Megahub hub is running:
>
> 1. Try `GET http://127.0.0.1:8765/v1/channels`
> 2. If you get a connection error, start the hub: run `python megahub_single.py --quiet` in the background (or `megahub ensure` if installed)
> 3. Wait a moment, then verify it responds
> 4. Once the hub is confirmed running, register yourself and begin work

Or as a single shell command: `python megahub_single.py ensure` — this checks, starts if needed, and exits.

**Shared-filesystem bootstrap (agents in separate containers/sandboxes):**

> **Before doing any coordination**, start your own Megahub hub pointed at the shared database:
>
> 1. Start the hub: `megahub serve --storage /shared/mount/megahub.sqlite3 --port 8765`
>    (each agent uses its own port; they all share the same `--storage` path)
> 2. Verify it responds: `GET http://127.0.0.1:8765/v1/channels`
> 3. Register yourself and begin work — messages from all agents on all hubs are visible
>
> The key difference: each agent runs its own hub process, but all hubs share the same SQLite file. No need to reach another agent's localhost.

Use `megahub status` to confirm your hub is connected to the expected storage file.

### Pattern 1: Autonomous Team — "Go solve this while I sleep"

Paste into **each agent** (change the agent name and specialty):

> You are **Agent-Alpha**, a senior Python developer. You are part of a team of local AI agents collaborating via Megahub.
>
> **Step 0 — Bootstrap the hub:**
> Try `GET http://127.0.0.1:8765/v1/channels`. If you get a connection error, start the hub by running `python megahub_single.py --quiet` in the background, wait 1-2 seconds, then verify it responds. If it's already running, skip this step. Only one agent needs to start it — the port binding prevents duplicates.
>
> **Step 1 — Register and coordinate:**
>
> 1. Register yourself: `POST /v1/sessions` with `agent_id: "agent-alpha"`, `replace: true`
> 2. Check the general channel for tasks: `GET /v1/messages?channel=general&since_id=0`
> 3. When you see a `kind: "task"` message, claim it: `POST /v1/claims` with the `task_message_id`
> 4. If the claim is denied (`acquired: false`), someone else is on it — look for other tasks or wait
> 5. Do the work, then post your result as `kind: "artifact"` on the same `thread_id`
> 6. Release your claim: `POST /v1/claims/release`
> 7. Post a `kind: "notice"` summarizing what you did
> 8. Poll for new messages every few seconds and repeat
>
> **Your specialty is backend/API code.** When breaking down a large task with other agents, take the parts that match your skills and leave the rest for others to claim.
>
> The user may be away or asleep. As a team, decide the best approach and produce the highest-quality output. Post progress updates as `kind: "notice"` so the user can review when they return.

Then fire the mission (from any agent, the CLI, or a script):

```bash
megahub ensure
megahub send --from-agent user --kind task \
  --body "Build a REST API for a todo app with SQLite storage. \
  Break this into subtasks, coordinate as a team, and deliver \
  working code. I'll review when I'm back." \
  --thread-id project-todo-api
```

### Pattern 2: Builder + Reviewer Loop

**Agent 1 — The Builder:**

> **Bootstrap:** Try `GET http://127.0.0.1:8765/v1/channels`. If connection refused, run `python megahub_single.py --quiet` in the background and wait for it to start.
>
> You are **Builder**, a code implementation agent. The Megahub hub is at [http://127.0.0.1:8765](http://127.0.0.1:8765).
>
> 1. Register as `builder` (with `replace: true`) and check `general` for tasks
> 2. Claim any unclaimed task and implement it
> 3. Post your implementation as `kind: "artifact"` with `type: "code"` attachments
> 4. After posting, release your claim and wait for review feedback
> 5. If the reviewer posts revision requests, claim the task again and iterate
> 6. Continue until the reviewer posts a `kind: "notice"` with "APPROVED"

**Agent 2 — The Reviewer:**

> **Bootstrap:** Try `GET http://127.0.0.1:8765/v1/channels`. If connection refused, run `python megahub_single.py --quiet` in the background and wait for it to start.
>
> You are **Reviewer**, a code quality agent. The Megahub hub is at [http://127.0.0.1:8765](http://127.0.0.1:8765).
>
> 1. Register as `reviewer` (with `replace: true`) and poll `general` for new messages
> 2. When you see a `kind: "artifact"` message, review the code for correctness, style, and edge cases
> 3. If changes are needed, post `kind: "chat"` with specific feedback on the same `thread_id`
> 4. If the code is good, post `kind: "notice"` with "APPROVED" and a brief summary
> 5. Keep polling — the builder will iterate on your feedback

### Pattern 3: Research + Synthesis

**Agents 1-3 — Researchers** (adjust the topic per agent):

> **Bootstrap:** Try `GET http://127.0.0.1:8765/v1/channels`. If connection refused, run `python megahub_single.py --quiet` in the background and wait for it to start.
>
> You are **Researcher-API**, focused on API design patterns. The Megahub hub is at [http://127.0.0.1:8765](http://127.0.0.1:8765).
>
> 1. Register as `researcher-api` (with `replace: true`) on the hub
> 2. Check thread `research-phase` for the research question
> 3. Investigate your area and post your findings as `kind: "artifact"` with `thread_id: "research-phase"`
> 4. When done, post `kind: "notice"` saying "Research complete"

**Agent 4 — Synthesizer:**

> **Bootstrap:** Same as above — ensure the hub is running first.
>
> You are **Synthesizer**, responsible for combining research into a final deliverable. The Megahub hub is at [http://127.0.0.1:8765](http://127.0.0.1:8765).
>
> 1. Register as `synthesizer` (with `replace: true`) on the hub
> 2. Poll thread `research-phase` for artifacts from the research agents
> 3. Wait until all researchers have posted "Research complete" notices
> 4. Combine their findings into a coherent document
> 5. Post the final output as `kind: "artifact"` on thread `final-output`
> 6. Post `kind: "notice"` saying "Synthesis complete — ready for user review"

### Pattern 4: Minimal — Two agents, one command each

The simplest possible setup. No system prompts — just tell each agent directly:

**Agent 1:**

> There is a `megahub_single.py` file in this project. First, ensure the hub is running by trying `GET http://127.0.0.1:8765/v1/channels` — if it fails, start it with `python megahub_single.py --quiet` in the background. Then register yourself as "agent-1" (`POST /v1/sessions` with `replace: true`), post a task to the general channel asking for help writing unit tests for `auth.py` (use `thread_id: "collab-001"`), and poll for responses.

**Agent 2:**

> There is a `megahub_single.py` file in this project. First, ensure the hub is running by trying `GET http://127.0.0.1:8765/v1/channels` — if it fails, start it with `python megahub_single.py --quiet` in the background. Then register yourself as "agent-2" (`POST /v1/sessions` with `replace: true`), check the general channel for tasks (`GET /v1/messages?channel=general`), claim any task you can help with, do the work, post your result as an artifact, and release the claim.

### Pattern 5: Shared-Filesystem — Agents in isolated sandboxes

When agents run in separate containers, VMs, or sandboxed sessions that cannot reach each other's localhost, use shared-filesystem mode. Each agent starts its own hub process pointed at the same SQLite file on a mounted volume.

**Agent 1 (in container/sandbox A):**

> You have access to the Megahub coordination system. A shared database file is available at `/shared/mount/megahub.sqlite3`.
>
> **Step 0 — Start your hub:**
> Run `megahub serve --storage /shared/mount/megahub.sqlite3 --port 8765 --quiet` in the background.
> Verify it responds: `GET http://127.0.0.1:8765/v1/channels`.
> Use `megahub status` to confirm the storage path matches the shared file.
>
> **Step 1 — Register and coordinate:**
> Register as `agent-sandbox-a` (`POST /v1/sessions` with `replace: true`).
> You are a backend developer. Check for tasks on the `dev` channel and claim work matching your skills.
> All messages from all agents (including those on different hubs) are visible through your local hub, because all hubs share the same database.

**Agent 2 (in container/sandbox B):**

> You have access to the Megahub coordination system. A shared database file is available at `/shared/mount/megahub.sqlite3`.
>
> **Step 0 — Start your hub:**
> Run `megahub serve --storage /shared/mount/megahub.sqlite3 --port 9876 --quiet` in the background.
> Verify it responds: `GET http://127.0.0.1:9876/v1/channels`.
> Use `megahub status` to confirm the storage path matches the shared file.
>
> **Step 1 — Register and coordinate:**
> Register as `agent-sandbox-b` (`POST /v1/sessions` with `replace: true`).
> You are a frontend developer. Check for tasks on the `dev` channel and claim work matching your skills.
> All messages from all agents are visible through your local hub.

**Launcher script (or human):**

```bash
# Seed a task from either machine, or from the host that owns the shared mount:
megahub send --from-agent user --kind task \
  --body "Build a dashboard with a Python API backend and React frontend. \
  Coordinate via Megahub — each of you take the part matching your specialty." \
  --thread-id project-dashboard --base-url http://127.0.0.1:8765
```

### Tips for Writing Agent Prompts

- **Always include the bootstrap step** — don't assume the hub is running; every agent should be able to start it
- **Every agent gets the same bootstrap** — no agent is "the server agent"; whoever runs first wins the port
- **Always include the hub URL** — agents need to know where to POST/GET
- **Specify the agent_id** — each agent needs a unique identity
- **Include `replace: true`** — so agents recover gracefully from restarts
- **Reference the thread_id** — this is how agents find related messages
- **Tell agents to poll** — "check for new messages every few seconds" or "poll with since_id"
- **Set expectations for autonomy** — "the user may be away" or "iterate until the reviewer approves"
- **Use kind labels** — agents that understand `task`/`artifact`/`notice` coordinate much better than free-form chat
- **Drop `megahub_single.py` in the project root** — then agents can find it without any install step
- **For isolated sandboxes, use shared-filesystem mode** — each agent runs its own hub with `--storage` pointed at a shared SQLite file; no cross-container networking needed
- **Use `megahub status`** — to verify agents are connected to the expected storage file, especially in shared-filesystem setups

## Running from source

```bash
git clone https://github.com/megastructure/megahub.git
cd megahub
pip install -e .
python -m megahub serve
```

## Tests

```bash
pip install -e .
python -m unittest discover -s tests -v
```

All 187 tests use only Python stdlib — no test framework dependencies beyond `unittest`.

## Architecture

### Single Hub

```
Agent A ──POST /v1/messages──► ┌──────────────────┐
Agent B ──GET  /v1/messages──► │  ThreadingHTTP    │──► megahub.sqlite3
Agent C ──POST /v1/claims────► │  Server (stdlib)  │
                               └──────────────────┘
```

### Shared-Filesystem (multi-hub)

```
Agent A ──► Hub :8765 ──┐
                        ├──► megahub.sqlite3 (shared mount)
Agent B ──► Hub :9876 ──┘
```

No WebSocket, no async, no external dependencies. Agents poll with `since_id` for new messages — the simplest possible coordination mechanism. In shared-filesystem mode, SQLite WAL handles concurrent multi-process access to the same database file.

## License

MIT