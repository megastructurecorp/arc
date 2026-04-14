#!/usr/bin/env node
// Arc npm shim — locates a Python 3.10+ interpreter and runs the bundled arc.py.
//
// The npm package ships arc.py as a data file; this script is what `bin` points at.
// No postinstall hook, no network access at install time — agent hosts can install
// silently and arc works on first invocation.
//
// Python resolution order:
//   Windows: py -3  →  python3  →  python
//   POSIX:   python3  →  python
//
// Exit code is forwarded from the Python process. stdio is inherited, so
// interactive use, piping, and MCP-over-stdio all work transparently.

import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { existsSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ARC_PY = resolve(__dirname, "..", "arc.py");

if (!existsSync(ARC_PY)) {
  console.error(`arc: cannot find arc.py at ${ARC_PY}`);
  console.error("arc: the npm package may be corrupted — try reinstalling.");
  process.exit(1);
}

const IS_WINDOWS = process.platform === "win32";
const CANDIDATES = IS_WINDOWS
  ? [
      ["py", ["-3"]],
      ["python3", []],
      ["python", []],
    ]
  : [
      ["python3", []],
      ["python", []],
    ];

const forwarded = process.argv.slice(2);

for (const [cmd, pre] of CANDIDATES) {
  const result = spawnSync(cmd, [...pre, ARC_PY, ...forwarded], {
    stdio: "inherit",
  });

  // ENOENT means this launcher isn't on PATH — try the next candidate.
  if (result.error && result.error.code === "ENOENT") {
    continue;
  }

  // Any other spawn-level error is fatal and we shouldn't fall through.
  if (result.error) {
    console.error(`arc: failed to launch ${cmd}: ${result.error.message}`);
    process.exit(1);
  }

  process.exit(result.status ?? 1);
}

console.error("arc: no Python 3.10+ interpreter found on PATH.");
console.error("arc: install Python 3.10 or newer from https://www.python.org/downloads/");
console.error("arc: then re-run 'arc' — this package does not need to be reinstalled.");
process.exit(1);
