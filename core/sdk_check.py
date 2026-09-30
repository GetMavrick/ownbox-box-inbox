"""Is a machine built only on what the box promises? (docs/SCOPE_MACHINE_MARKETPLACE.md §9.3)

The reader behind `python scripts/ownbox.py check` and behind the break notice (core/machine_breaks.py):
one list of rules, so what the owner is told when an update stops a machine is what the check said.
It reads a machine's files and never runs them. Every finding carries its fix in one sentence,
written for the AI that will do the fixing.

What it flags: AI used any way but `m.think`; anything imported from the box that `core.sdk` does not
promise; a machine.yaml missing `requires_foundation`, `needs:` or `sdk:`, or naming another folder.
What it does not: a machine acting on the world (sending, fetching, paying). On the owner's own box
that is theirs to decide (CLAUDE.md, non-negotiable 3).

CORE LEARNS A DIRECTORY, NEVER A MACHINE'S NAME: the folder is whatever it is handed.
"""
from __future__ import annotations

import ast
import pathlib
import re

# Libraries and addresses that reach an AI company directly. The box's AI goes through one door.
AI_MODULES = ("anthropic", "openai", "google.generativeai", "google.genai", "mistralai", "cohere",
              "ollama", "litellm", "langchain", "langchain_core", "langchain_openai",
              "langchain_anthropic", "llama_index", "claude_agent_sdk", "groq", "together")
AI_HOSTS = ("api.anthropic.com", "api.openai.com", "generativelanguage.googleapis.com",
            "api.mistral.ai", "api.cohere.ai", "api.groq.com")

FIX_AI = "Call m.think(task, prompt) instead; it uses this box's AI account under its monthly ceiling."
FIX_CORE = ("Use only `from core import sdk` (see BUILD_A_MACHINE.md); anything else in core/ can "
            "change in any update.")


def _finding(path: pathlib.Path, line: int, problem: str, fix: str) -> dict:
    return {"path": str(path), "line": line, "problem": problem, "fix": fix}


def _manifest_findings(folder: pathlib.Path) -> list[dict]:
    from core import sdk
    f = folder / "machine.yaml"
    if not f.is_file():
        return [_finding(f, 0, "there is no machine.yaml",
                         f"Run `python scripts/ownbox.py new {folder.name}` in an empty folder, or copy "
                         f"the starter's machine.yaml and set name: {folder.name}.")]
    try:
        import yaml
        m = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception as e:                                      # noqa: BLE001
        return [_finding(f, 0, f"machine.yaml cannot be read ({type(e).__name__})",
                         "Make it plain YAML, one `field: value` per line, like the starter's.")]
    if not isinstance(m, dict):
        return [_finding(f, 0, "machine.yaml is not a list of fields",
                         "Make it one `field: value` per line, like the starter's.")]
    out = []
    if str(m.get("name") or "") != folder.name:
        out.append(_finding(f, 0, f"name is {m.get('name')!r}, not the folder's name",
                            f"Set `name: {folder.name}`; the name and the folder must match."))
    if not str(m.get("version") or "").strip():
        out.append(_finding(f, 0, "version is missing", "Add `version: 0.1.0` and raise it when it changes."))
    if not str(m.get("requires_foundation") or "").strip():
        from core import packs
        out.append(_finding(f, 0, "requires_foundation is missing",
                            f'Add `requires_foundation: "{packs.foundation_version()}"`, the box version '
                            f"you built it on."))
    if "needs" not in m:
        out.append(_finding(f, 0, "needs: is missing",
                            "Add `needs: []`, or the plan features it needs (like `[coworkers]`), so the "
                            "box knows which plans run it."))
    if m.get("sdk") is None:
        out.append(_finding(f, 0, "sdk: is missing",
                            f"Add `sdk: {sdk.VERSION}` so a box can tell whether it keeps the promises "
                            f"this machine was built on."))
    else:
        try:
            if int(m["sdk"]) > sdk.VERSION:
                out.append(_finding(f, 0, f"it asks for sdk {m['sdk']}; this box has sdk {sdk.VERSION}",
                                    f"Set `sdk: {sdk.VERSION}`, or wait for the update that brings it."))
        except (TypeError, ValueError):
            out.append(_finding(f, 0, f"sdk {m['sdk']!r} is not a number", f"Set `sdk: {sdk.VERSION}`."))
    return out


def _is_ai(module: str) -> bool:
    return any(module == a or module.startswith(a + ".") for a in AI_MODULES)


def _core_import_ok(module: str, names=()) -> bool:
    """`from core import sdk`, `import core.sdk`, `from core.sdk import x` are the promise."""
    if module == "core":
        return bool(names) and all(n == "sdk" for n in names)
    return module == "core.sdk" or module.startswith("core.sdk.")


def _code_findings(folder: pathlib.Path, py: pathlib.Path) -> list[dict]:
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    except SyntaxError as e:
        return [_finding(py, e.lineno or 0, f"it does not parse: {e.msg}",
                         "Fix the Python syntax on that line; the box skips a machine that cannot load.")]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if _is_ai(a.name):
                    out.append(_finding(py, node.lineno, f"it imports {a.name}, an AI library", FIX_AI))
                elif (a.name == "core" or a.name.startswith("core.")) and not _core_import_ok(a.name):
                    out.append(_finding(py, node.lineno, f"it imports {a.name}, which the box does not promise",
                                        FIX_CORE))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mod, names = node.module, [a.name for a in node.names]
            if _is_ai(mod):
                out.append(_finding(py, node.lineno, f"it imports from {mod}, an AI library", FIX_AI))
            elif (mod == "core" or mod.startswith("core.")) and not _core_import_ok(mod, names):
                what = f"{mod}.{names[0]}" if mod == "core" else mod
                out.append(_finding(py, node.lineno, f"it imports {what}, which the box does not promise",
                                    FIX_CORE))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            host = next((h for h in AI_HOSTS if h in node.value), None)
            if host:
                out.append(_finding(py, node.lineno, f"it calls {host} directly", FIX_AI))
            elif node.value.startswith("core.") and not node.value.startswith("core.sdk") \
                    and re.fullmatch(r"core(\.[a-z_][a-z0-9_]*)+", node.value):
                out.append(_finding(py, node.lineno, f"it names the module {node.value} to import it",
                                    FIX_CORE))
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            first = node.args[0] if node.args else None
            if name in ("run", "Popen", "call", "check_call", "check_output") and isinstance(first, ast.List) \
                    and first.elts and isinstance(first.elts[0], ast.Constant) and first.elts[0].value == "claude":
                out.append(_finding(py, node.lineno, "it runs the claude command itself", FIX_AI))
            if name == "machine" and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                    and f.value.id == "sdk" and isinstance(first, ast.Constant) and first.value != folder.name:
                out.append(_finding(py, node.lineno, f"sdk.machine({first.value!r}) names another machine",
                                    f'Use sdk.machine("{folder.name}"), the folder\'s own name.'))
    return out


def check(folder: pathlib.Path) -> list[dict]:
    """Every finding for one machine folder, in file order. Never runs the machine's code."""
    folder = pathlib.Path(folder)
    out = _manifest_findings(folder)
    for py in sorted(folder.rglob("*.py")):
        if "__pycache__" in py.parts or (folder / "data") in py.parents:
            continue
        out += _code_findings(folder, py)
    return out
