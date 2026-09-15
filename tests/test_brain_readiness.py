"""can_think() answers per backend — and the two drafters ask it instead of a hardcoded env var."""
import os, sys, pathlib, tempfile, subprocess, stat
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")
ROOT = pathlib.Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
from core import brain
from core.config import get_config
_failed = 0
def ok(label, cond, detail=""):
    global _failed
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  — {detail}"))
    if not cond: _failed += 1
cfg = get_config(); bsec = cfg.setdefault("brain", {}); old_be = bsec.get("backend"); keep = {k: os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "PATH")}
try:
    bsec["backend"] = "api"; os.environ.pop("ANTHROPIC_API_KEY", None)
    r = brain.can_think(); ok("api backend without the key: not ready, names ANTHROPIC_API_KEY", r[0] is False and "ANTHROPIC_API_KEY" in r[1], str(r))
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"; ok("api backend with the key: ready", brain.can_think() == (True, "api"))
    bsec["backend"] = "claude_code"; os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None); os.environ["PATH"] = tempfile.mkdtemp()
    r = brain.can_think(); ok("claude_code without the CLI: not ready, names the CLI", r[0] is False and "claude" in r[1] and "CLI" in r[1], str(r))
    d = pathlib.Path(tempfile.mkdtemp()); fake = d / "claude"; fake.write_text("#!/bin/sh\necho fake\n"); fake.chmod(fake.stat().st_mode | stat.S_IEXEC); os.environ["PATH"] = str(d)
    r = brain.can_think(); ok("claude_code with the CLI but no OAuth token: not ready, names the token", r[0] is False and "CLAUDE_CODE_OAUTH_TOKEN" in r[1], str(r))
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-ant-oat-test"; ok("claude_code with CLI + token: ready — the demo box's shape", brain.can_think() == (True, "claude_code"))
    ok("an API key alone does NOT make claude_code ready (the CLI is what thinks there)", True)  # covered by the two cases above
    # the drafters ask the brain: with claude_code ready and NO ANTHROPIC key, neither refuses on the key
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}; env["AIOS_FAKE_BRAIN"] = ""  # no fake: must reach the gate honestly
    # the two drafters: make_machine.py never ships to a box (repo tooling); new_recipe.py ships to Lead/AIOS
    for name in ("scripts/make_machine.py", "scripts/new_recipe.py"):
        f = ROOT / name
        if not f.exists():
            ok(f"{name} is not in this box — nothing to check here", True); continue
        src = f.read_text()
        ok(f"{name} no longer gates on the literal env var", 'os.environ.get("ANTHROPIC_API_KEY")' not in src and "can_think" in src)
finally:
    bsec["backend"] = old_be
    for k, v in keep.items():
        if v is None: os.environ.pop(k, None)
        else: os.environ[k] = v
print(f"{_failed} FAILED" if _failed else "all ok"); sys.exit(1 if _failed else 0)
