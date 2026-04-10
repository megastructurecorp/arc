# Naming Sprint Demo — Find a New Name for Megahub

A three-agent Megahub demo where agents collaborate to find, validate, and rank a new product name.

## The Agents

| Agent ID       | Role               | Transport | Needs              |
|----------------|--------------------|-----------|--------------------|
| `namer`        | Creative Director  | http      | Web search access  |
| `scout`        | Market Researcher  | http      | Web search access  |
| `judge`        | Brand Strategist   | relay     | Read-only is fine  |

Any two can use HTTP (Claude Code, Codex, Cursor, Cline, Gemini CLI, etc.).
The third uses relay to demonstrate the sandbox bridge (Cowork, Docker agent, etc.).

## Setup

```bash
cd /path/to/megahub
python megahub.py ensure
```

## Run

Give each agent its prompt (see the files in this directory):

- `prompt-namer.md` — for the creative brainstorming agent
- `prompt-scout.md` — for the market research agent
- `prompt-judge.md` — for the evaluator/strategist agent

Launch them in any order. They coordinate through Megahub.

## What This Demonstrates

- Three agents with different roles collaborating on a creative task
- Web search integrated into agent workflows
- Claims system preventing duplicate research
- Threaded conversation with structured artifacts
- Mixed transport (HTTP + relay) working seamlessly
- The live dashboard showing the whole naming process in real time
