# Smoke Test Trio

Tiny 3-agent prompts to verify that one sandboxed agent can interoperate with two normal Megahub agents.
These prompts now use the single-command `smoke_agent.py` runner so the sandboxed harness has almost no room to improvise.

## Host Prep

Run these on your machine before starting the agents:

```powershell
cd C:\Users\humbl\megahub
.\.venv\Scripts\Activate.ps1
python -m megahub ensure
python -m megahub relay --spool-dir .megahub-relay
```

Use the same fixed thread for all three agents:

- `channel`: `smoke-room`
- `thread_id`: `smoke-relay-001`

If `smoke-room` does not exist yet, Agent 1 will create it.

All three prompts assume the repo root is the current working directory.

---

## Agent 1 — Normal HTTP Agent

```text
You are Agent 1 in a tiny Megahub smoke test.

Run exactly this command from the repo root:

`py smoke_agent.py --role smoke-a --transport http`

Do not inspect or edit files. Do not start servers. Do not do any extra work.
If the command fails, report only the command and stderr.
```

---

## Agent 2 — Sandboxed Relay Agent

```text
You are Agent 2 in a tiny Megahub smoke test.

You are the sandboxed agent. You are a bounded operator, not a programmer.

Forbidden:
- Do not inspect `_hub.py`, `smoke_agent.py`, or any repo source files
- Do not edit any files
- Do not start `megahub`
- Do not start `megahub relay`
- Do not use localhost HTTP directly
- Do not use SQLite directly
- Do not create replacement scripts
- Do not troubleshoot by exploring the repo

Run exactly these commands from the repo root:

`$env:MEGAHUB_TRANSPORT="relay"`
`$env:MEGAHUB_RELAY_DIR=".megahub-relay"`
`$env:MEGAHUB_AGENT_ID="smoke-b"`
`py smoke_agent.py --role smoke-b --transport relay --relay-dir .megahub-relay`

If any command fails:
- stop immediately
- do not improvise
- report only the failing command and stderr
```

---

## Agent 3 — Normal HTTP Verifier

```text
You are Agent 3 in a tiny Megahub smoke test.

Run exactly this command from the repo root:

`py smoke_agent.py --role smoke-c --transport http`

Do not inspect or edit files. Do not start servers. Do not do any extra work.
If the command fails, report only the command and stderr.
```

---

## Success Criteria

The smoke test passes if all of the following happen in thread `smoke-relay-001`:

1. `smoke-a` posts the task.
2. `smoke-b` sees it through relay mode, claims work, and posts an artifact.
3. `smoke-c` sees both sides in the same thread and posts a verification notice.
4. `smoke-a` sees both agents and posts the final host-side pass notice.

## Notes

- The host starts the hub and relay once.
- `smoke-a` and `smoke-c` use normal HTTP mode.
- `smoke-b` uses relay mode only.
- The sandboxed prompt is intentionally strict because some harnesses improvise when a command fails.

