# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.2,<2"]
# ///
"""lean-verify: an MCP "gate" server for formal verification in Lean 4.

Interactive proof work (goals, diagnostics, tactics) is handled by the separate lean-lsp-mcp server.
This is the deterministic part: project init, build, audit, theorem statement freezing.
The "done / not done" decision is made by the audit, not by the model.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("lean-verify")
HERE = Path(__file__).parent
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
FORBIDDEN = re.compile(r"\b(sorry|admit|axiom|native_decide|unsafe|implemented_by|extern)\b")
LOCK = "spec.lock.json"
TIMEOUT = 1800


def _run(cmd: list[str], cwd: Path, timeout: int = TIMEOUT) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]} (install elan: https://github.com/leanprover/elan)"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd)}"


def _proj(project_path: str, name: str = "lean_verification") -> Path:
    """Lean project root: the path itself if it has a lakefile, otherwise <path>/<name>."""
    p = Path(project_path).expanduser().resolve()
    for cand in (p, p / name):
        if (cand / "lakefile.toml").exists() or (cand / "lakefile.lean").exists():
            return cand
    raise ValueError(f"Lean project not found in {p} (call lean_init)")


def _lib(root: Path) -> str:
    f = root / "lakefile.toml"
    if f.exists():
        libs = tomllib.loads(f.read_text()).get("lean_lib", [])
        if libs:
            return libs[0]["name"]
    # lakefile.lean: first `lean_lib Name`
    m = re.search(r"lean_lib\s+(?:«)?(\w+)", (root / "lakefile.lean").read_text())
    if not m:
        raise ValueError("could not determine the library name from the lakefile")
    return m.group(1)


def _sources(root: Path, lib: str) -> list[Path]:
    out = []
    if (root / f"{lib}.lean").exists():
        out.append(root / f"{lib}.lean")
    if (root / lib).is_dir():
        out += sorted((root / lib).rglob("*.lean"))
    return out


def _strip_comments(src: str) -> str:
    src = re.sub(r"/-.*?-/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"--[^\n]*", "", src)


def _dump(root: Path) -> tuple[list[dict], str]:
    """Library theorems: name, type, axioms (queried via Lean)."""
    lib = _lib(root)
    tmp = root / ".lean_verify_dump.lean"
    tmp.write_text((HERE / "Dump.lean.tmpl").read_text().replace("__LIB__", lib))
    try:
        code, out = _run(["lake", "env", "lean", tmp.name], root)
    finally:
        tmp.unlink(missing_ok=True)
    rows, other = [], []
    for line in out.splitlines():
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
                continue
            except json.JSONDecodeError:
                pass
        other.append(line)
    if code != 0 and not rows:
        return [], out
    return rows, "\n".join(other)


def _fingerprint(row: dict) -> str:
    return hashlib.sha256(row["type"].encode()).hexdigest()[:16]


@mcp.tool()
def lean_init(project_path: str, name: str = "lean_verification", mathlib: bool = False) -> dict:
    """Creates a Lean 4 project at <project_path>/<name> (if absent) and runs the first build.

    mathlib=True adds the Mathlib dependency and downloads its cache (slow, several GB)."""
    base = Path(project_path).expanduser().resolve()
    if not base.is_dir():
        return {"ok": False, "error": f"directory does not exist: {base}"}
    root = base / name
    created = False
    if not (root / "lakefile.toml").exists() and not (root / "lakefile.lean").exists():
        if root.exists() and any(root.iterdir()):
            return {"ok": False, "error": f"{root} exists and is not empty, but it is not a Lake project"}
        code, out = _run(["lake", "new", name] + (["math"] if mathlib else []), base, 600)
        if code != 0:
            return {"ok": False, "error": out[-2000:]}
        # lake new creates a nested git repo and a GitHub workflow: unwanted inside someone else's repo
        shutil.rmtree(root / ".git", ignore_errors=True)
        shutil.rmtree(root / ".github", ignore_errors=True)
        # The template's demo executable imports `hello` from Basic.lean and breaks the build
        # as soon as the model rewrites that file: keep only the library.
        (root / "Main.lean").unlink(missing_ok=True)
        toml = root / "lakefile.toml"
        if toml.exists():
            t = re.sub(r"\[\[lean_exe\]\].*?(?=\n\[|\Z)", "", toml.read_text(), flags=re.S)
            lib = _lib(root)
            t = re.sub(r"^defaultTargets\s*=.*$", f'defaultTargets = ["{lib}"]', t, flags=re.M)
            toml.write_text(t.rstrip() + "\n")
        created = True
    cache_note = None
    if mathlib:
        ccode, cout = _run(["lake", "exe", "cache", "get"], root, 3600)
        if ccode != 0:
            cache_note = f"mathlib cache download failed (build will be slow): {cout[-500:]}"
    code, out = _run(["lake", "build"], root)
    return {"ok": code == 0, "created": created, "root": str(root),
            "toolchain": (root / "lean-toolchain").read_text().strip(),
            "cache_warning": cache_note, "build_output": out[-2000:]}


@mcp.tool()
def lean_build(project_path: str) -> dict:
    """`lake build`: result, number of errors/warnings, problem lines. ok=False if there are warnings."""
    root = _proj(project_path)
    code, out = _run(["lake", "build"], root)
    errors = [l for l in out.splitlines() if re.search(r"\berror:", l)]
    warns = [l for l in out.splitlines() if re.search(r"\bwarning:", l)]
    return {"ok": code == 0 and not warns, "exit_code": code, "errors": len(errors),
            "warnings": len(warns), "problems": (errors + warns)[:40], "tail": out[-1500:]}


@mcp.tool()
def lean_spec_freeze(project_path: str, theorems: list[str] | None = None) -> dict:
    """Freezes theorem statements in spec.lock.json (hash of the type). Afterwards
    lean_audit rejects the project if a statement changed or a theorem disappeared.
    theorems=None freezes all user theorems; otherwise only the listed ones are added/updated
    (existing entries for other theorems are kept).
    Call this AFTER a human has approved the statements."""
    root = _proj(project_path)
    code, _ = _run(["lake", "build"], root)
    if code != 0:
        return {"ok": False, "error": "project does not build; cannot freeze"}
    rows, msg = _dump(root)
    if theorems is not None:
        known = {r["name"] for r in rows}
        missing = [t for t in theorems if t not in known]
        if missing:
            return {"ok": False, "error": f"no such theorems: {missing}"}
        rows = [r for r in rows if r["name"] in set(theorems)]
    if not rows:
        return {"ok": False, "error": f"no user theorems found to freeze: {msg[-500:]}"}
    lock = {}
    if theorems is not None and (root / LOCK).exists():
        lock = json.loads((root / LOCK).read_text())
    lock.update({r["name"]: {"type": r["type"], "hash": _fingerprint(r)} for r in rows})
    (root / LOCK).write_text(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True))
    return {"ok": True, "frozen": len(lock), "file": str(root / LOCK)}


@mcp.tool()
def lean_audit(project_path: str) -> dict:
    """Readiness gate. verdict=PASS only if: the build has no errors or warnings;
    sources contain no sorry/admit/axiom/native_decide/unsafe/implemented_by/extern;
    every theorem's axioms are a subset of {propext, Classical.choice, Quot.sound};
    frozen statements are unchanged and none has disappeared."""
    root = _proj(project_path)
    lib = _lib(root)
    failures: list[str] = []

    code, out = _run(["lake", "build"], root)
    if code != 0:
        failures.append(f"lake build exited with code {code}")
    for l in out.splitlines():
        if re.search(r"\b(error|warning):", l):
            failures.append(l.strip())

    for f in _sources(root, lib):
        for i, line in enumerate(_strip_comments(f.read_text()).splitlines(), 1):
            if m := FORBIDDEN.search(line):
                failures.append(f"{f.relative_to(root)}:{i}: forbidden `{m.group(1)}`")

    rows, msg = _dump(root)
    if not rows and not failures:
        failures.append(f"no theorems found or the dump failed: {msg[-500:]}")
    for r in rows:
        bad = sorted(set(r["axioms"]) - ALLOWED_AXIOMS)
        if bad:
            failures.append(f"{r['name']}: non-standard axioms {bad}")

    lock_file = root / LOCK
    frozen = 0
    if lock_file.exists():
        lock = json.loads(lock_file.read_text())
        cur = {r["name"]: r for r in rows}
        frozen = len(lock)
        for n, v in lock.items():
            if n not in cur:
                failures.append(f"frozen theorem {n} has disappeared")
            elif _fingerprint(cur[n]) != v["hash"]:
                failures.append(f"statement of {n} changed:\n  was: {v['type']}\n  now: {cur[n]['type']}")
    else:
        failures.append("statements are not frozen (no spec.lock.json): call lean_spec_freeze")

    return {"verdict": "PASS" if not failures else "FAIL", "theorems": len(rows),
            "frozen": frozen, "failures": failures[:50]}


@mcp.tool()
def lean_status(project_path: str) -> dict:
    """Quick non-gating summary: theorems with sorry, frozen, changed, unfrozen."""
    root = _proj(project_path)
    rows, msg = _dump(root)
    if not rows and msg.strip():
        return {"ok": False, "error": f"dump failed: {msg[-500:]}"}
    lock = json.loads((root / LOCK).read_text()) if (root / LOCK).exists() else {}
    cur = {r["name"]: r for r in rows}
    return {
        "theorems": len(rows),
        "with_sorry": [r["name"] for r in rows if "sorryAx" in r["axioms"]],
        "nonstandard_axioms": {r["name"]: sorted(set(r["axioms"]) - ALLOWED_AXIOMS)
                               for r in rows if set(r["axioms"]) - ALLOWED_AXIOMS},
        "frozen": len(lock),
        "changed": [n for n, v in lock.items() if n in cur and _fingerprint(cur[n]) != v["hash"]],
        "missing": [n for n in lock if n not in cur],
        "unfrozen": [n for n in cur if n not in lock],
    }


if __name__ == "__main__":
    mcp.run()
