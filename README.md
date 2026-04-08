# Megahub

Local-first agent coordination over HTTP and SQLite, with a relay mode for sandboxed harnesses that cannot reach host localhost or safely use SQLite on a mounted workspace.

Megahub now has one canonical implementation: [`megahub_single.py`](./megahub_single.py). The installable `megahub` package is a thin compatibility and tooling layer around that core.

## What It Provides

- REST API for sessions, messages, threads, claims, locks, tasks, inbox, and hub info
- SQLite persistence with WAL enabled automatically
- Thread-aware dashboard at `GET /`
- File-based relay transport for constrained sandboxes
- Deterministic smoke runner for brittle harnesses via `smoke_agent.py`
- Zero runtime dependencies beyond Python 3.10+

## Quickstart

Package install:

```bash
python -m pip install -e .
python -m megahub ensure
```

Single-file use:

```bash
python megahub_single.py ensure
```

That starts the hub if needed and reports the local URL.

## Supported Modes

### 1. Single Hub

All agents talk to one local HTTP hub.

```bash
python -m megahub ensure
python -m megahub status
```

### 2. Shared-Filesystem Multi-Hub

Each environment runs its own local hub, but all hubs point at the same SQLite file on a shared mount.

```bash
python -m megahub serve --port 8765 --storage /shared/megahub.sqlite3
python -m megahub serve --port 9876 --storage /shared/megahub.sqlite3
```

This is useful when agents cannot reach each other's localhost but can share a filesystem that supports SQLite WAL/locking correctly.

### 3. Sandbox Relay Mode

Use this when the sandbox:

- can read and write workspace files
- cannot reach the host machine's localhost
- cannot safely use SQLite directly on the mounted filesystem

Start the real hub and the relay on the host:

```bash
python -m megahub ensure
python -m megahub relay --spool-dir .megahub-relay
```

The relay watches the spool directory, forwards sandbox-written requests to the host hub, and writes responses back into the shared workspace.

## Smoke Runner For Constrained Harnesses

For harnesses that need a low-improvisation command path, keep the root `smoke_agent.py` wrapper and run:

```bash
py smoke_agent.py --role smoke-a --transport http
py smoke_agent.py --role smoke-b --transport relay --relay-dir .megahub-relay
py smoke_agent.py --role smoke-c --transport http
```

This is the supported smoke path for environments like Claude Co-work style sandboxes.

## Common Commands

```bash
python -m megahub ensure
python -m megahub serve
python -m megahub relay --spool-dir .megahub-relay
python -m megahub status
python -m megahub thread
python -m megahub replay --thread-id demo-thread
```

## Python API

```python
from megahub import MegahubClient

with MegahubClient("http://127.0.0.1:8765") as client:
    client.open_session("agent-a", replace=True)
    client.send_message({
        "from_agent": "agent-a",
        "channel": "general",
        "kind": "task",
        "body": "Coordinate work",
        "thread_id": "demo-thread",
    })
```

## Package Surface

These remain stable:

- `from megahub import MegahubClient, HubConfig, create_server, ensure_hub, run_server`
- `python -m megahub ...`
- `python megahub_single.py ...`

Relay support remains first-class:

- `megahub/file_relay.py`
- `megahub/smoke_agent.py`
- root `smoke_agent.py`

## Protocol Reference

The protocol and wire contract are documented in [`docs/PROTOCOL.md`](./docs/PROTOCOL.md).

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers the canonical core, wrapper compatibility, relay transport, smoke-agent interop, shared-storage behavior, and import-order regressions.

## License

MIT
