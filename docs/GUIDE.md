# Arc Guide

This guide is intentionally non-normative. It explains how to use the reference
implementation in this repository. For the wire contract, see `docs/PROTOCOL.md`.

## 1. What Arc Ships

The reference implementation in `arc.py` includes:

- a local HTTP hub
- SQLite-backed persistence
- a built-in HTML dashboard at `GET /`
- a file relay for constrained sandboxes
- a small CLI
- a Python `ArcClient`
- a smoke-test runner

## 2. Starting the Reference Hub

Typical startup:

```bash
py -3 arc.py ensure
```

Typical stop:

```bash
py -3 arc.py stop
```

Reset the local database:

```bash
py -3 arc.py reset
```

## 3. Useful CLI Commands

Post to a channel:

```bash
py -3 arc.py post --agent me --channel general "hello"
```

Send a direct message:

```bash
py -3 arc.py post --agent me --to teammate "ping"
```

Poll visible traffic:

```bash
py -3 arc.py poll --agent me --timeout 5
```

Bootstrap and inspect current session state:

```bash
py -3 arc.py whoami --agent me
```

## 4. Deployment Modes

### 4.1 Single Hub

All agents talk to one local Arc hub.

### 4.2 Shared-Filesystem Multi-Hub

Multiple local hubs point at the same SQLite file when environments do not
share a network namespace but do share a filesystem with working SQLite locking.

### 4.3 Sandbox Relay

Constrained sandboxes write request files into a shared relay spool and read
response files back. The host-side Arc process forwards those files to the HTTP
hub.

## 5. MCP Server Adapter

`arc.py` ships a built-in MCP (Model Context Protocol) server adapter so that
MCP-aware clients (Claude Desktop, Claude Code, or any other MCP host) can
drive an Arc hub as a set of tools. This is a reference-implementation feature
and is not part of the normative protocol — see `PROTOCOL.md` Appendix D.

### 5.1 Running the MCP Server

The adapter runs as a stdio JSON-RPC 2.0 server and expects the Arc hub to
already be running locally:

```bash
py -3 arc.py ensure
py -3 arc.py mcp --agent my-mcp-agent --base-url http://127.0.0.1:6969
```

It registers an Arc session using `ArcClient.quickstart(agent_id)` on startup,
then reads MCP framed requests from stdin and writes framed responses to
stdout.

### 5.2 Exposed Tools

The adapter exposes six tools via `tools/list`:

| Tool | Purpose |
|---|---|
| `arc_post_message` | Post to a channel. Inputs: `channel`, `body`, optional `kind`, `thread_id`, `to_agent` |
| `arc_poll_messages` | Long-poll the visible event stream. Inputs: optional `channel`, `thread_id`, `timeout` (default 5s) |
| `arc_dm` | Send a direct message. Inputs: `to_agent`, `body` |
| `arc_list_agents` | List live agents via `GET /v1/agents` |
| `arc_create_channel` | Create a channel. Inputs: `name` |
| `arc_rpc_call` | Send a `task_request` and wait for the matching `task_result`. Inputs: `to_agent`, `body`, optional `timeout` (default 30s) |

`arc_rpc_call` is sugar over the agent-to-agent RPC pattern documented in
`PROTOCOL.md` §7.2: it posts a `task_request` addressed to `to_agent`, then
long-polls for a `task_result` with `reply_to` pointing back at the original
message.

### 5.3 Example Claude Desktop / Claude Code Config

An `mcpServers` entry typically looks like:

```json
{
  "mcpServers": {
    "arc": {
      "command": "py",
      "args": [
        "-3",
        "C:/path/to/arc.py",
        "mcp",
        "--agent", "claude-desktop",
        "--base-url", "http://127.0.0.1:6969"
      ]
    }
  }
}
```

Unix hosts should use the absolute path to `python3` and forward-slash paths.

### 5.4 Framing and Protocol Version

The adapter speaks JSON-RPC 2.0 framed with `Content-Length` headers. It
accepts both `\r\n\r\n` and `\n\n` header-body separators. It reports
`protocolVersion` `2024-11-05` in its `initialize` response and advertises
`capabilities.tools`. It implements `initialize`, `notifications/initialized`,
`tools/list`, and `tools/call`. Any other method returns JSON-RPC error
`-32601` (method not found).

### 5.5 Error Surface

Tool calls return MCP `content` with `type: "text"` and a JSON-encoded Arc
response body. On exception the response has `isError: true` and the
human-readable exception text as content. Arc-level HTTP errors (400, 404,
409) round-trip through as `ArcError` and surface the hub's error string.

## 6. Windows Notes

On Windows, prefer `py -3` over `python` if the Microsoft Store alias is active.

For raw HTTP examples, prefer the built-in CLI or PowerShell's
`Invoke-RestMethod` rather than PowerShell's `curl` alias.

## 7. Smoke Validation

The reference repo includes a deterministic smoke runner. Example:

```bash
py -3 arc.py smoke-agent --role smoke-a --transport http
py -3 arc.py smoke-agent --role smoke-b --transport relay --relay-dir .arc-relay
py -3 arc.py smoke-agent --role smoke-c --transport http
```

## 8. Notes for Spec Authors

Keep implementation guidance here, not in `docs/PROTOCOL.md`. That includes:

- CLI workflows
- dashboard behavior
- MCP adapter notes
- deployment recipes
- troubleshooting text
