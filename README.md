# Arc

[![PyPI](https://img.shields.io/pypi/v/megastructure-arc.svg)](https://pypi.org/project/megastructure-arc/)
[![npm](https://img.shields.io/npm/v/%40megastructurecorp%2Farc.svg)](https://www.npmjs.com/package/@megastructurecorp/arc)
[![Python](https://img.shields.io/pypi/pyversions/megastructure-arc.svg)](https://pypi.org/project/megastructure-arc/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![CI](https://github.com/megastructurecorp/arc/actions/workflows/ci.yml/badge.svg)](https://github.com/megastructurecorp/arc/actions/workflows/ci.yml)

Local-first agent coordination over HTTP and SQLite, with a file-relay mode for sandboxed agents that cannot reach host localhost or safely use SQLite on the shared mount.

One canonical implementation in [`arc.py`](./arc.py). Single Python file. Zero runtime dependencies. Python 3.10+. MIT.

## Install

Three ways to get Arc:

```bash
# PyPI
pip install megastructure-arc

# npm (preferred for MCP hosts — Claude Desktop, Cursor, Cline, Claude Code, etc.)
npm install -g @megastructurecorp/arc
# …or inline without a global install:
#   npx -y @megastructurecorp/arc ensure

# From source
git clone https://github.com/megastructurecorp/arc
cd arc && python3 arc.py ensure
```

Verify:

```bash
arc --version     # "arc 0.1.0"
```

The `pip` and `npm` packages install an `arc` command on your `PATH`. The npm package is a thin shim that locates a local Python 3.10+ interpreter and runs the bundled `arc.py` — **you still need Python installed to use the npm package.** The shim exists because most MCP hosts are JavaScript-native and install agent tools via `npx`, so shipping only to PyPI leaves a large ergonomic gap.

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
arc ensure
```

The hub runs in the background on `http://127.0.0.1:6969`. Open that URL in a browser to see the live dashboard.

The relay for sandboxed agents starts automatically alongside the hub — no extra commands needed.

Stop the hub (and relay):

```bash
arc stop
```

Stop the hub and delete all data (sessions, messages, claims, locks, tasks):

```bash
arc reset
```

All commands accept `--host`, `--port`, `--storage`, and `--spool-dir` flags if you're not using the defaults.

If you are running from a git clone without installing, substitute `py -3 arc.py ensure` (Windows) or `python3 arc.py ensure` (macOS / Linux) for `arc ensure` anywhere in this README. Everything else is identical.

## Exposing the hub on your LAN

By default the hub binds to `127.0.0.1` and only accepts connections from the same machine. To make it reachable from other devices on your network, you must pass **both** `--host 0.0.0.0` and `--allow-remote` when starting the hub:

```bash
arc stop
arc ensure --host 0.0.0.0 --allow-remote
```

The bind address is fixed at startup. The `/network on` dashboard command and `POST /v1/network` endpoint cannot retroactively expose a hub that was started on loopback — they will return HTTP 400 with a message telling you to restart with the flags above. Stop the hub, restart with `--host 0.0.0.0 --allow-remote`, and connect from the LAN client using your machine's LAN IP (e.g. `http://192.168.1.42:6969`).

Arc has no built-in authentication, so any device that can reach the port can read and write all messages. See [`SECURITY.md`](./SECURITY.md) before exposing it. You may also need to allow inbound TCP on the chosen port in your OS firewall — Windows in particular silently drops non-standard ports on the "Private" profile until you add a rule.

## Talking To The Hub Without curl

Arc ships a small CLI built on the `ArcClient` class so you never need to hand-roll HTTP requests:

```bash
arc post   --agent me "hello from the cli"
arc post   --agent me --to teammate "private ping"
arc poll   --agent me --timeout 30
arc whoami --agent me
```

`poll` defaults to `exclude_self=true` (you will not see your own messages echoed back) and uses long-poll. `post --agent me` implicitly registers the session with `replace=true`, which will evict any bot already running under that `agent_id` — use a distinct id when interleaving with a live agent. The same is true of `poll --agent me` and `whoami --agent me`: they are stateful convenience commands, not read-only inspection tools.

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

The host must already be running `arc ensure`; the relay thread starts automatically as part of the hub. Hub-level errors (400, 404, 409) round-trip through the relay as `arc.ArcError` with the original error text intact.

## Running From Source on Windows

**If you installed via `pip` or `npm`, skip this section — you already have an `arc` command on `PATH` that handles everything below automatically.**

On fresh Windows 11 installs, `python` is often aliased to the Microsoft Store shim and will not run scripts. If you are running Arc from a git clone, use the official Python launcher instead:

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

Better yet, skip curl and use the built-in CLI: `arc post --agent me "hi"` handles quoting and encoding correctly on every shell. If the Python console mangles non-ASCII output (em-dashes, curly quotes, emoji), set `PYTHONIOENCODING=utf-8` in your environment — the hub stores UTF-8 correctly regardless, this only affects what your terminal prints back.

## Choose The Right Mode

### Mode 1: Single Hub

Use this when all agents can reach the same local HTTP server.

Start the hub:

```bash
arc ensure
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
arc --port 6969 --storage /shared/arc.sqlite3

# Sandbox B
arc --port 9876 --storage /shared/arc.sqlite3
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
arc ensure
```

The relay is built in and starts automatically. To use a custom spool directory:

```bash
arc ensure --spool-dir .arc-relay
```

The relay is intentionally append-only:

- requests land under `requests/<agent>/`
- responses land under `responses/<agent>/`
- processed requests are renamed to `.work`

## Agent Instructions

### The Canonical Self-Diagnosing Prompt

The single most important thing to get right when bringing an agent onto Arc
is the transport choice: does the agent talk to the hub over HTTP, or does it
talk over the file relay? Picking wrong is silent. An agent that picks HTTP
when it should have picked relay can run `arc ensure` inside its own sandbox,
succeed, and then have a monologue on an isolated hub that nobody else can
see — a "party of one."

The fix is not auto-detection, it is a prompt that makes the agent run a
one-command self-test and branch on the result. Hand this prompt to any
agent — sandboxed or not — and it will route itself to the correct
transport. Replace `<your_id>` with the agent's identifier.

> **Arc bootstrap instructions**
>
> Arc is a coordination hub. Before using it, run this one command and
> read the output:
>
> ```bash
> arc whoami --agent <your_id>
> ```
>
> If `arc` is not on your `PATH` (e.g. you are running from a git clone
> without installing), use one of these equivalents instead:
>
> ```bash
> py -3 arc.py whoami --agent <your_id>       # Windows, git clone
> python3 arc.py whoami --agent <your_id>     # macOS / Linux, git clone
> ```
>
> Decide your transport based on the result. Do not guess, do not probe,
> do not try to start your own hub.
>
> **Case A — the command returns a JSON object containing a `session`
> field.** You have direct HTTP access to a running hub. Use the normal
> client:
>
> ```python
> import arc
> client = arc.ArcClient("<your_id>")
> client.register(display_name="<your display name>")
> client.post("general", "hello")
> for msg in client.poll(timeout=30):
>     ...
> ```
>
> **Case B — the command errors with a connection-refused / network-
> unreachable message, AND there is a `.arc-relay/` directory in your
> working tree.** You are in a constrained sandbox. The host is already
> running the real hub; you must talk to it via the file relay. Use:
>
> ```python
> import arc
> client = arc.ArcClient.over_relay("<your_id>", spool_dir=".arc-relay")
> client.register(display_name="<your display name>")
> client.post("general", "hello from the sandbox")
> for msg in client.poll(timeout=30):
>     ...
> ```
>
> **Case C — neither of the above.** Stop. Ask the operator. Do not
> guess.
>
> **Hard rules, regardless of case:**
>
> - **DO NOT start your own Arc hub in a sandbox.** Not `arc ensure`, not
>   `py -3 arc.py ensure`, not `python3 arc.py ensure`, not any other form.
>   Starting a second hub will succeed silently and isolate you from every
>   other agent on the real one.
> - **DO NOT start any other Arc server process.** The host already has
>   one.
> - **DO NOT fall back from HTTP to relay on your own.** If HTTP fails
>   unexpectedly, report it to the operator.
> - After `register()`, post a one-line "hello" to `#general` and confirm
>   you see your own message echoed back (or see another agent respond)
>   before assuming you are connected. A silent `register()` success is
>   not proof of a working link.
> - If you need to know which hub implementation you are talking to
>   (useful when multiple agents coordinate across versions), read the
>   `implementation` and `implementation_version` fields from
>   `GET /v1/hub-info`. They are informational only — do not use them for
>   feature detection, use `features` instead.

This prompt is the only one you need for agent onboarding. It covers normal,
sandboxed, and misconfigured environments with the same text.

### Why The Self-Test Instead Of A Smart Client

Arc deliberately does not ship a `connect()` helper that probes HTTP and
falls back to relay. Auto-detection sounds helpful, but:

- An HTTP probe that returns `200` does not prove it reached the *right*
  hub. A sandboxed agent's own isolated hub would also return `200`.
- A relay spool directory existing does not prove there is a live relay
  thread draining it. Stale spools from previous runs look identical.
- Silent fallback hides the real error. "Hub down" becomes "no reachable
  hub at `<url>` or relay spool `<dir>`", which mentions two transports
  that were never both supposed to work.
- The operator already knows the deployment topology. The agent does not.
  Moving the transport decision from the operator to a probe is a step
  backwards on clarity.

The explicit `ArcClient(...)` and `ArcClient.over_relay(...)` constructors
are the supported API. The self-test above is how an agent picks between
them without guessing.

### Advanced: Shared-Filesystem Multi-Hub

Use this only when each sandbox can safely run its own local hub against a
shared SQLite file on a filesystem with working locking:

> Arc is available in `arc.py`. Start your own local hub pointing at the
> shared database file:
>
> ```bash
> arc --port <your_port> --storage /shared/arc.sqlite3
> ```
>
> Then talk only to your own local hub URL inside that sandbox. Do not try
> to call another sandbox's localhost. Do not share ports across sandboxes.

This mode is exotic. If you are not certain your filesystem supports SQLite
WAL locking across the mount, prefer the relay transport (Case B above)
instead.

## Smoke Validation

Arc includes a deterministic smoke runner inside the same file.

Example mixed validation:

```bash
arc ensure

arc smoke-agent --role smoke-a --transport http
arc smoke-agent --role smoke-b --transport relay --relay-dir .arc-relay
arc smoke-agent --role smoke-c --transport http
```

This validates that:

- direct HTTP agents can see relay-originated work
- relay agents can claim and post artifacts
- the constrained sandbox path does not require direct localhost access

## Common Commands

```bash
arc --version                       # print installed version
arc ensure                          # start hub (idempotent)
arc stop                            # stop the running hub
arc reset                           # stop hub + delete database
arc --port 6969                     # run hub + relay in foreground
arc smoke-agent --role smoke-b --transport relay --relay-dir .arc-relay
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
