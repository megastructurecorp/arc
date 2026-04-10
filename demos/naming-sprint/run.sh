#!/usr/bin/env bash
# Naming Sprint — Quick Start
#
# This script sets up Megahub and creates the naming-sprint channel.
# You then launch each agent in its own terminal / harness.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MEGAHUB_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$MEGAHUB_DIR"

echo "=== Starting Megahub ==="
python megahub.py ensure

echo ""
echo "=== Creating naming-sprint channel ==="
curl -s -X POST http://127.0.0.1:6969/v1/channels \
  -H "Content-Type: application/json" \
  -d '{"name": "naming-sprint", "created_by": "operator", "metadata": {"purpose": "Find a new name for Megahub"}}' | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))"

echo ""
echo "=== Megahub is running ==="
echo "Dashboard: http://127.0.0.1:6969"
echo ""
echo "Now launch your three agents with these prompts:"
echo "  1. namer  (Creative Director)  — $SCRIPT_DIR/prompt-namer.md"
echo "  2. scout  (Market Researcher)  — $SCRIPT_DIR/prompt-scout.md"
echo "  3. judge  (Brand Strategist)   — $SCRIPT_DIR/prompt-judge.md"
echo ""
echo "Example (Claude Code):"
echo "  claude-code --prompt-file $SCRIPT_DIR/prompt-namer.md"
echo ""
echo "Example (Codex):"
echo "  codex --prompt-file $SCRIPT_DIR/prompt-scout.md"
echo ""
echo "The judge uses relay mode — works in any sandboxed environment."
echo ""
echo "Watch the conversation unfold at http://127.0.0.1:6969"
