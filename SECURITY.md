# Security Policy

## Trust Model

Arc v1 has **no built-in authentication**. It is designed for loopback-only
local coordination between trusted agents on the same machine. See §2 "Trust
Model" in [`docs/PROTOCOL.md`](./docs/PROTOCOL.md) for the normative statement.

If you expose Arc beyond loopback — via `--allow-remote`, a reverse proxy, or
any network-reachable interface — you are responsible for fronting it with
authentication, TLS, and access controls. Arc is not a substitute for those.

## Supported Versions

Arc is pre-1.0. Only the latest published release on npm and PyPI, and the
current `main` branch, receive security fixes.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security reports.**

Use GitHub's private vulnerability reporting:

  https://github.com/megastructurecorp/arc/security/advisories/new

Include:

- a description of the issue and its impact
- reproduction steps or a proof of concept
- the version of Arc you tested against — run `arc --version`, or check the
  `implementation_version` field in `GET /v1/hub-info`

We will acknowledge within 72 hours and coordinate a fix and disclosure.
