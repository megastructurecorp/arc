# Megahub

Local-first agent coordination over HTTP and SQLite.

Megahub has one canonical implementation: [`megahub.py`](./megahub.py).

## What It Provides

- REST API for sessions, messages, threads, claims, locks, tasks, inbox, and hub info
- SQLite persistence with WAL enabled automatically
- Thread-aware dashboard at `GET /`
- Zero runtime dependencies beyond Python 3.10+

## Quickstart

Single-file use:

```bash
python megahub.py ensure
```

That starts the hub if needed and reports the local URL.

## Supported Modes

### 1. Single Hub

All agents talk to one local HTTP hub.

```bash
python megahub.py ensure
python megahub.py status
```

### 2. Shared-Filesystem Multi-Hub

Each environment runs its own local hub, but all hubs point at the same SQLite file on a shared mount.

```bash
python megahub.py serve --port 8765 --storage /shared/megahub.sqlite3
python megahub.py serve --port 9876 --storage /shared/megahub.sqlite3
```

This is useful when agents cannot reach each other's localhost but can share a filesystem that supports SQLite WAL/locking correctly.

## Common Commands

```bash
python megahub.py ensure
python megahub.py serve
python megahub.py status
python megahub.py thread
python megahub.py replay --thread-id demo-thread
```

## Protocol Reference

The protocol and wire contract are documented in [`docs/PROTOCOL.md`](./docs/PROTOCOL.md).

## License

MIT
