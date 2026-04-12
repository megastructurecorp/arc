# Arc

Local-first agent coordination over HTTP and SQLite, with a file-relay mode for sandboxed agents that cannot reach host localhost or safely use SQLite on the shared mount.

This repo ships one canonical implementation: [`arc.py`](./arc.py).

## What It Supports

`arc.py` provides:

- a local HTTP coordination hub
- SQLite-backed persistence
- sessions, channels, messages, claims, locks, tasks, inbox, and thread views
- an HTML dashboard at `GET /`
- a host-side relay for constrained sandboxes
- a deterministic smoke runner for validating mixed HTTP and relay agents

## Starting & Stopping

Start the hub (idempotent — safe to run multiple times):

```bash
python arc.py ensure
```

The hub runs in the background on `http://127.0.0.1:6969`. Open that URL in a browser to see the live dashboard.

The relay for sandboxed agents starts automatically alongside the hub — no extra commands needed.

Stop the hub (and relay):

```bash
python arc.py stop
```

Stop the hub and delete all data (sessions, messages, claims, locks, tasks):

```bash
python arc.py reset
```

All commands accept `--host`, `--port`, `--storage`, and `--spool-dir` flags if you're not using the defaults.

## Talking To The Hub Without curl

Arc ships a small CLI built on the new `ArcClient` class so you never need to hand-roll HTTP requests:

```bash
python arc.py post   --agent me "hello from the cli"
python arc.py post   --agent me --to teammate "private ping"
python arc.py poll   --agent me --timeout 30
python arc.py whoami --agent me
```

`poll` defaults to `exclude_self=true` (you will not see your own messages echoed back) and uses long-poll. `post --agent me` implicitly registers the session with `replace=true`, which will evict any bot already running under that `agent_id` — use a distinct id when interleaving with a live agent.

For programmatic use:

```python
import arc
client = arc.ArcClient("my-agent")
client.register(display_name="My Agent")
client.post("general", "hello")
for msg in client.poll(timeout=30):
    ...
```

Sandboxed agents that cannot reach `127.0.0.1` use the same class with a different constructor — everything else is identical:

```python
import arc
client = arc.ArcClient.over_relay("sandboxed-agent", spool_dir=".arc-relay")
client.register()
client.post("general", "hello from the sandbox")
for msg in client.poll(timeout=30):     # still exclude_self by default, still tracks since_id
    ...
```

The host must already be running `python arc.py ensure`; the relay thread starts automatically as part of the hub. Hub-level errors (400, 404, 409) round-trip through the relay as `arc.ArcError` with the original error text intact.

## Using Arc On Windows

On fresh Windows 11 installs, `python` is often aliased to the Microsoft Store shim and will not run the script. Use the official launcher instead:

```powershell
py -3 arc.py ensure
py -3 arc.py post --agent me "hello"
py -3 arc.py poll --agent me --timeout 30
```

`curl` on Windows 10/11 is a real PowerShell alias that mangles UTF-8 in `-d` payloads. Two reliable workarounds:

1. Use PowerShell's native `Invoke-RestMethod`:
   ```powershell
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:6969/v1/messages `
     -ContentType 'application/json' `
     -Body '{"from_agent":"me","channel":"general","body":"hi"}'
   ```
2. Or write the JSON to a file and use `curl --data-binary`:
   ```powershell
   Set-Content -Path msg.json -Value '{"from_agent":"me","channel":"general","body":"hi"}' -Encoding utf8
   curl.exe --data-binary "@msg.json" -H "Content-Type: application/json" http://127.0.0.1:6969/v1/messages
   ```

Better yet, skip curl and use the built-in CLI: `py -3 arc.py post --agent me "hi"` handles quoting and encoding correctly on every shell.

## Choose The Right Mode

### Mode 1: Single Hub

Use this when all agents can reach the same local HTTP server.

Start the hub:

```bash
python arc.py ensure
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
python arc.py --port 6969 --storage /shared/arc.sqlite3

# Sandbox B
python arc.py --port 9876 --storage /shared/arc.sqlite3
```

Each sandbox talks only to its own local hub. All hubs share the same coordination state through the same SQLite file.

### Mode 3: Sandbox Relay

Use this when the sandbox can write ordinary files into the shared workspace, but cannot:

- reach the host machine's `127.0.0.1`
- safely use SQLite directly on the shared mount

In this mode:

1. the host runs Arc (the relay starts automatically as a background thread)
2. the sandbox writes request files into the relay spool directory
3. the relay forwards those requests to the HTTP hub
4. the relay writes response files back for the sandbox to read

Start the host side:

```bash
python arc.py ensure
```

The relay is built in and starts automatically. To use a custom spool directory:

```bash
python arc.py ensure --spool-dir .arc-relay
```

The relay is intentionally append-only:

- requests land under `requests/<agent>/`
- responses land under `responses/<agent>/`
- processed requests are renamed to `.work`

## Agent Instructions

### Instructions For Normal Agents

Use this when the agent can call the local hub directly:

> Arc is available in `arc.py`.
> First ensure the hub is running with `python arc.py ensure`.
> Then use `http://127.0.0.1:6969`.
> Register your session, poll for messages, claim work, post artifacts, and release claims when done.

### Instructions For Isolated Agents Sharing SQLite

Use this when each sandbox can safely run its own local hub against the same shared database:

> Arc is available in `arc.py`.
> Start your own local hub pointing at the shared database file:
> `python arc.py --port <your_port> --storage /shared/arc.sqlite3`
> Then talk only to your own local hub URL inside that sandbox.
> Do not try to call another sandbox's localhost.

### Instructions For Constrained Sandboxed Agents

Use this when the agent's sandbox cannot reach the host machine's `localhost` or use SQLite on the shared mount.

**WARNING: DO NOT start your own hub process!**
If an agent runs `python arc.py ensure` inside an isolated sandbox, it will succeed, but they will be talking to an isolated hub and no other agents will see their messages. The host is ALREADY running the real hub.

Provide the agent with this exact prompt:

> You are operating in a constrained sandbox. You cannot communicate via HTTP or direct SQLite, and you MUST NOT start your own Arc server process. Doing so will isolate you.
> Instead, interact with the hub exclusively using the file-based relay transport via the `FileRelayClient` class in `arc.py`.
> The shared relay spool directory is located at `.arc-relay`.
> 
> Example Python usage:
> ```python
> import arc
> client = arc.FileRelayClient(agent_id="my-agent-name", spool_dir=".arc-relay")
> client.call("POST", "/v1/sessions", {"agent_id": "my-agent-name", "replace": True})
> client.call("POST", "/v1/messages", {"channel": "general", "kind": "chat", "body": "hello"})
> ```

### Instructions You Should Not Give To Constrained Sandboxes

Do **not** tell a constrained sandboxed agent to:

- run `python arc.py ensure` (it will create a silent, isolated "party of one")
- bootstrap by calling host localhost unless you know it can
- depend on direct SQLite access unless you know the mount supports it
- follow the normal single-hub bootstrap blindly

For that environment, the right transport is relay mode.

## Smoke Validation

Arc includes a deterministic smoke runner inside the same file.

Example mixed validation:

```bash
python arc.py ensure

python arc.py smoke-agent --role smoke-a --transport http
python arc.py smoke-agent --role smoke-b --transport relay --relay-dir .arc-relay
python arc.py smoke-agent --role smoke-c --transport http
```

This validates that:

- direct HTTP agents can see relay-originated work
- relay agents can claim and post artifacts
- the constrained sandbox path does not require direct localhost access

## Common Commands

```bash
python arc.py ensure                # start hub (idempotent)
python arc.py stop                  # stop the running hub
python arc.py reset                 # stop hub + delete database
python arc.py --port 6969           # run hub + relay in foreground
python arc.py smoke-agent --role smoke-b --transport relay --relay-dir .arc-relay
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
