# Changelog

All notable changes to Arc are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/megastructurecorp/arc/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/megastructurecorp/arc/releases/tag/v0.1.0
