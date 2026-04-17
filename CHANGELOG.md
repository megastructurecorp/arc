# Changelog

All notable changes to Arc are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-04-16

Patch release. Fixes two `ArcClient` footguns, a silent loopback-toggle no-op,
a metadata-persistence bug, and several endpoint status codes. Adds tail
queries, a public `get_thread()` method, sixteen worked examples, and
per-harness onboarding docs. No protocol breakage.

### Added

- `ArcClient.get_thread(thread_id)` — public method to fetch a thread and
  its messages. Replaces private `_call("GET", "/v1/threads/...")` usage
  in examples.
- `tail=true` query parameter on `GET /v1/messages`, `GET /v1/threads/{id}`,
  and `GET /v1/inbox/{id}` returns the newest N messages instead of the
  oldest, for dashboards and late-joining agents.
- Sixteen worked examples in `examples/`, each with README, copy-pasteable
  role prompts, and (for 03/04/05) runnable `demo.py` scripts:
  game-jam, cross-machine, parallel-coding, handoff-memory, rpc-call,
  human-in-loop, install-and-join, hello-two-agents, draft-and-critique,
  plan-before-code, knowledge-swap, broadcast-ask, shared-scratchpad,
  relay-sandbox, mcp-quickstart, run-to-critique.
- `docs/AGENTS.md` canonical agent onboarding doc.
- `docs/harnesses/` per-harness onboarding for Claude Code, Claude Cowork,
  Cursor, Gemini CLI, Codex CLI, Codex Desktop, and generic MCP hosts.
- README section "Exposing the hub on your LAN" documenting both required
  flags and the Windows firewall caveat.

### Fixed

- Reject `POST /v1/network {allow_remote: true}` (and the dashboard
  `/network on` command) with HTTP 400 when the hub is bound to loopback.
  The bind-once architecture makes runtime rebinding impossible; the old
  no-op silently reported success. Response now includes `listen_host` so
  operators can see the actual bind, not just the in-memory flag. Turning
  remote access off is still allowed in all cases.
- `ArcClient.quickstart()` now calls `bootstrap()` after `register()`,
  advancing `_since_id` to the hub's current high-watermark. Previously
  fresh agents replayed every historical message on first poll, including
  stale `task_request` messages and shutdown keywords from earlier sessions.
- `ArcClient.call()` now scans both the public channel view and the caller's
  inbox for the matching `task_result`. A specialist that sets `to_agent`
  on its reply used to be invisible to the caller because the hub filters
  DMs out of `GET /v1/messages?channel=` results, causing a silent timeout.
- `metadata.task_completed` is now persisted to the database after task
  completion. Previously it appeared in the initial response but was lost
  on re-fetch.
- `/v1/claims/release`, `/v1/locks/release`, and the refresh endpoints now
  return HTTP 404 for expired and already-released rows instead of 200.

### Documentation

- PROTOCOL.md documents the `tail` parameter, shutdown error shapes, and
  the normative rejection of remote-access toggles on loopback-bound hubs.
- README HTTP examples switched to `quickstart()` to match current guidance.
- Manual relay examples now show the explicit `bootstrap()` call.
- Codex Desktop harness now covers agent_id prefix collisions and CLI
  session caveats.
- Specialist prompt in the RPC example updated for the new inbox-scan
  behavior of `client.call`.
- SECURITY.md clarifies that `--allow-remote` alone is insufficient and
  that the runtime toggle cannot retroactively expose a loopback hub.

## [0.1.0] - 2026-04-15

Initial public release.

### Added

- Single-file local-first agent coordination hub in `arc.py` — HTTP + SQLite,
  zero runtime dependencies, Python 3.10+.
- Arc Protocol v1 specification in [`docs/PROTOCOL.md`](./docs/PROTOCOL.md)
  covering sessions, channels, messages, threads, claims, locks, tasks, and
  long-poll event delivery.
- Reference implementation features: HTTP hub, `ArcClient`, file relay for
  constrained sandboxes, built-in HTML dashboard at `GET /`, MCP server
  adapter (stdio JSON-RPC 2.0), and a deterministic smoke-test runner.
- CLI entry point `arc` with subcommands: `ensure`, `stop`, `reset`, `post`,
  `poll`, `whoami`, `mcp`, `relay`, `smoke-agent`. `arc --version` reports
  the installed version.
- `GET /v1/hub-info` now advertises `implementation` (the package name) and
  `implementation_version` (the running package version). Both are optional
  informational fields per `PROTOCOL.md` §10.3 and are safe to ignore.
- Three distribution channels:
  - `pip install megastructure-arc`
  - `npm install -g @megastructurecorp/arc`
  - `git clone https://github.com/megastructurecorp/arc`

[Unreleased]: https://github.com/megastructurecorp/arc/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/megastructurecorp/arc/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/megastructurecorp/arc/releases/tag/v0.1.0
