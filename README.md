# lean-verify-mcp

An MCP "gate" server for formal verification in Lean 4: project init, build, theorem statement
freezing, and audit (no `sorry`/extra axioms, statements unchanged).
Interactive proof work (goals, hover, lemma search) is provided by the separate `lean-lsp-mcp` server.

## Requirements
`elan` (provides `lake`, `lean`) and `uv`. `uv run --script` installs the `mcp>=1.2,<2` dependency itself (Python ≥ 3.11).

## Install (once, works in every project)
As a Claude Code plugin (MCP server + skill together):
```
/plugin marketplace add /path/to/lean-verify-mcp     # or <github-user>/lean-verify-mcp once pushed
/plugin install lean-verify@lean-verify-marketplace
```
Manual alternative (run from the repository root):
```
claude mcp add lean-verify -s user -- uv run --script "$PWD/server.py"
mkdir -p ~/.claude/skills && ln -sfn "$PWD/skills/lean-verify" ~/.claude/skills/lean-verify
```

## Tools
| Tool | What it does |
|---|---|
| `lean_init(project_path, name, mathlib)` | creates `<project_path>/lean_verification` (without nested .git and .github), first build |
| `lean_build(project_path)` | `lake build`, number of errors and warnings |
| `lean_spec_freeze(project_path, theorems?)` | hashes theorem types into `spec.lock.json` (a partial freeze keeps existing entries) |
| `lean_audit(project_path)` | `PASS`/`FAIL`: build, forbidden keywords, axioms, statements unchanged |
| `lean_status(project_path)` | summary: theorems with sorry, changed, unfrozen |

`project_path` is the root of your project (or of the Lean project itself).

## Usage from a prompt
> /lean-verify Prove that `uniqueCounters` never produces duplicates. Code: internal/engine/exec.go.

The skill guides the model through the protocol: model → statements for approval → freeze → proof → audit.

## What the audit does not guarantee
That the Lean model faithfully translates the code. Verify that separately (cross-check tests, reference data).
