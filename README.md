# Megahub

Local-first agent coordination over HTTP and SQLite, with a file-relay mode for sandboxed agents that cannot reach host localhost or safely use SQLite on the shared mount.

This repo ships one canonical implementation: [`megahub.py`](./megahub.py).

## What It Supports

`megahub.py` provides:

- a local HTTP coordination hub
- SQLite-backed persistence
- sessions, channels, messages, claims, locks, tasks, inbox, and thread views
- an HTML dashboard at `GET /`
- a host-side relay for constrained sandboxes
- a deterministic smoke runner for validating mixed HTTP and relay agents

## Starting & Stopping

Start the hub (idempotent — safe to run multiple times):

```bash
python megahub.py ensure
```

The hub runs in the background on `http://127.0.0.1:6969`. Open that URL in a browser to see the live dashboard.

The relay for sandboxed agents starts automatically alongside the hub — no extra commands needed.

Stop the hub (and relay):

```bash
python megahub.py stop
```

Stop the hub and delete all data (sessions, messages, claims, locks, tasks):

```bash
python megahub.py reset
```

All commands accept `--host`, `--port`, `--storage`, and `--spool-dir` flags if you're not using the defaults.

## Choose The Right Mode

### Mode 1: Single Hub

Use this when all agents can reach the same local HTTP server.

Start the hub:

```bash
python megahub.py ensure
```

Default URL:

```text
http://127.0.0.1:6969
```

### Mode 2: Shared-Filesystem Multi-Hub

Use this when agents cannot reach each other's localhost, but each environment can:

- run its own local process
- use the same SQLite file
- rely on the shared filesystem to support SQLite WAL and locking correctly

Example:

```bash
# Sandbox A
python megahub.py --port 6969 --storage /shared/megahub.sqlite3

# Sandbox B
python megahub.py --port 9876 --storage /shared/megahub.sqlite3
```

Each sandbox talks only to its own local hub. All hubs share the same coordination state through the same SQLite file.

### Mode 3: Sandbox Relay

Use this when the sandbox can write ordinary files into the shared workspace, but cannot:

- reach the host machine's `127.0.0.1`
- safely use SQLite directly on the shared mount

In this mode:

1. the host runs Megahub (the relay starts automatically as a background thread)
2. the sandbox writes request files into the relay spool directory
3. the relay forwards those requests to the HTTP hub
4. the relay writes response files back for the sandbox to read

Start the host side:

```bash
python megahub.py ensure
```

The relay is built in and starts automatically. To use a custom spool directory:

```bash
python megahub.py ensure --spool-dir .megahub-relay
```

The relay is intentionally append-only:

- requests land under `requests/<agent>/`
- responses land under `responses/<agent>/`
- processed requests are renamed to `.work`

## Agent Instructions

### Instructions For Normal Agents

Use this when the agent can call the local hub directly:

> Megahub is available in `megahub.py`.
> First ensure the hub is running with `python megahub.py ensure`.
> Then use `http://127.0.0.1:6969`.
> Register your session, poll for messages, claim work, post artifacts, and release claims when done.

### Instructions For Isolated Agents Sharing SQLite

Use this when each sandbox can safely run its own local hub against the same shared database:

> Megahub is available in `megahub.py`.
> Start your own local hub pointing at the shared database file:
> `python megahub.py --port <your_port> --storage /shared/megahub.sqlite3`
> Then talk only to your own local hub URL inside that sandbox.
> Do not try to call another sandbox's localhost.

### Instructions For Constrained Sandboxed Agents

Use this when the agent's sandbox cannot reach the host machine's `localhost` or use SQLite on the shared mount.

**WARNING: DO NOT start your own hub process!**
If an agent runs `python megahub.py ensure` inside an isolated sandbox, it will succeed, but they will be talking to an isolated hub and no other agents will see their messages. The host is ALREADY running the real hub.

Provide the agent with this exact prompt:

> You are operating in a constrained sandbox. You cannot communicate via HTTP or direct SQLite, and you MUST NOT start your own Megahub server process. Doing so will isolate you.
> Instead, interact with the hub exclusively using the file-based relay transport via the `FileRelayClient` class in `megahub.py`.
> The shared relay spool directory is located at `.megahub-relay`.
> 
> Example Python usage:
> ```python
> import megahub
> client = megahub.FileRelayClient(agent_id="my-agent-name", spool_dir=".megahub-relay")
> client.call("POST", "/v1/sessions", {"agent_id": "my-agent-name", "replace": True})
> client.call("POST", "/v1/messages", {"channel": "general", "kind": "chat", "body": "hello"})
> ```

### Instructions You Should Not Give To Constrained Sandboxes

Do **not** tell a constrained sandboxed agent to:

- run `python megahub.py ensure` (it will create a silent, isolated "party of one")
- bootstrap by calling host localhost unless you know it can
- depend on direct SQLite access unless you know the mount supports it
- follow the normal single-hub bootstrap blindly

For that environment, the right transport is relay mode.

## Smoke Validation

Megahub includes a deterministic smoke runner inside the same file.

Example mixed validation:

```bash
python megahub.py ensure

python megahub.py smoke-agent --role smoke-a --transport http
python megahub.py smoke-agent --role smoke-b --transport relay --relay-dir .megahub-relay
python megahub.py smoke-agent --role smoke-c --transport http
```

This validates that:

- direct HTTP agents can see relay-originated work
- relay agents can claim and post artifacts
- the constrained sandbox path does not require direct localhost access

## Common Commands

```bash
python megahub.py ensure                # start hub (idempotent)
python megahub.py stop                  # stop the running hub
python megahub.py reset                 # stop hub + delete database
python megahub.py --port 6969           # run hub + relay in foreground
python megahub.py smoke-agent --role smoke-b --transport relay --relay-dir .megahub-relay
curl http://127.0.0.1:6969/v1/hub-info
curl http://127.0.0.1:6969/v1/threads
curl "http://127.0.0.1:6969/v1/messages?thread_id=demo-thread"
```

## Protocol Reference

The wire contract is documented in [`docs/PROTOCOL.md`](./docs/PROTOCOL.md).

## Tests

```bash
python -m unittest discover -s tests -v
```

The restored test coverage includes relay transport and mixed HTTP/relay smoke scenarios.

## License

MIT
