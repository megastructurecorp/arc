# RUNME

Quick PowerShell commands for this repo.

## First-time setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

## Reopen later

```powershell
cd C:\Users\humbl\megahub
.\.venv\Scripts\Activate.ps1
```

## Start Megahub

```powershell
python -m megahub ensure
```

This starts the hub if it is not already running.

## Start sandbox relay mode

Use this only when a sandboxed agent can write files into the repo but cannot
reach your local hub or safely use SQLite on the mounted filesystem.

```powershell
python -m megahub ensure
python -m megahub relay --spool-dir .megahub-relay
```

Leave the relay running in its own terminal while sandboxed agents use `_hub.py`
with:

```powershell
$env:MEGAHUB_TRANSPORT = "relay"
$env:MEGAHUB_RELAY_DIR = ".megahub-relay"
$env:MEGAHUB_AGENT_ID = "sandbox-agent"
py _hub.py GET /v1/channels
```

## Run the smoke test trio

With the hub and relay already running:

```powershell
py smoke_agent.py --role smoke-a --transport http
py smoke_agent.py --role smoke-b --transport relay --relay-dir .megahub-relay
py smoke_agent.py --role smoke-c --transport http
```

Or use the prompts in `prompts\smoke_bridge_trio.md`.

## Check status

```powershell
python -m megahub status
```

## Run tests

```powershell
python -m unittest discover -s tests -v
```

## If editable install needs to be refreshed

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## Current local URL

Megahub is currently set up to run at:

`http://127.0.0.1:8765`
