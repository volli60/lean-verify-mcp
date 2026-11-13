---
name: lean-verify
description: Formally prove a property of code in Lean 4 in any project via the lean-verify MCP server. Use when asked to prove an algorithm's correctness, an invariant, or a theorem in Lean.
---

# Lean 4 proof protocol

Tools: `lean_init`, `lean_build`, `lean_spec_freeze`, `lean_audit`, `lean_status` (server `lean-verify`).
For interactive work (proof goals, hover, lemma search) use `lean-lsp-mcp` if connected.

## Steps

1. **Init.** If there is no Lean project, call `lean_init(project_path)`. Use Mathlib only if requested or unavoidable.
2. **Model.** Translate the relevant code into a pure functional model (`def`) in `LeanVerification/<Module>.lean`. State which source function the model corresponds to. Model ≠ code drift is the main risk: where possible, add a cross-check test in the project.
3. **Statements.** Write the theorem statements. Show them to the user in plain language and **stop until they confirm**. Never weaken a statement to make it provable.
4. **Freeze.** After confirmation: `lean_spec_freeze(project_path)`. From then on statements must not change; the audit checks this.
5. **Proof.** Loop: edit → `lean_build` → fix errors. `omega`, `simp`, `decide`, `induction` and helper lemmas are fine (helpers are not frozen and can be added freely).
6. **Gate.** Work is done only when `lean_audit` returns `verdict: PASS`. Forbidden: `sorry`, `admit`, `axiom`, `native_decide`, `unsafe`, `implemented_by`, `extern`.
7. **Report.** In the user's language: what was proved (per theorem), what the model simplifies, which assumptions remain.

## Prohibitions

- Do not change a frozen statement or delete a frozen theorem; if a statement is false, tell the user and show a counterexample.
- Do not claim success without `PASS` from `lean_audit`.
- Do not edit `spec.lock.json` by hand.
- Limit: if there is no progress on a theorem after 15 iterations, stop and describe where you are stuck.
