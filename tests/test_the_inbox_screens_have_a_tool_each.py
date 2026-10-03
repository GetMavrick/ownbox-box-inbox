"""Every action on the Inbox's screens has a tool a person's own AI can call, or a written reason it can't.

docs/SCOPE_INBOX_CONNECTOR.md §5. Owner, 2026-10-02: "If they don't have the ability to fully control that
unified inbox from an outside chat session like this one, then the machine is garbage... They're outsiders
and they need full control capabilities." The bar is parity with the screens, and this test keeps it: a new
POST route on the Inbox with no tool, and no reason in `BROWSER_ONLY`, fails CI, so the gap cannot reopen.

Run: python tests/test_the_inbox_screens_have_a_tool_each.py
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "parity.db")
os.environ.setdefault("DISPATCH_BEARER_TOKEN", "bearer")

from core import state                                                  # noqa: E402

state.init_db()

try:
    import marketing.customer_voice.app  # noqa: F401,E402
except ImportError:
    print("  --   no customer_voice on this box")
    sys.exit(0)

from core.connector import tools as registry                            # noqa: E402
from core.dispatch import app                                           # noqa: E402

_failed = 0

# EACH SCREEN ACTION AND THE TOOL THAT DOES IT FROM A CHAT. A list of tools, because one screen can hold more
# than one action (the Waiting screen sends written replies and throws them away).
TOOLS = {
    "/inbox/inbox/<path:zcid>/reply": ("inbox.propose_reply",),
    "/inbox/waiting": ("inbox.propose_drafts", "inbox.propose_discard_draft"),
    "/inbox/drafts": ("inbox.propose_drafting",),
    "/inbox/signature": ("inbox.propose_signature",),
}

# WHAT A CHAT CANNOT DO, AND WHY. Short on purpose; a route belongs here only for a reason a person accepts.
BROWSER_ONLY = {
    "/inbox/mailbox": "a mailbox password is typed by its owner into the box, never into a chat "
                      "(inbox.connect says where)",
    "/inbox/connect": "a social account sign-in is finished in a browser (inbox.connect says where)",
    "/inbox/setup": "the same credentials as the two above, on one screen",
    "/inbox/installed": "a device reporting that the mobile app is installed on it; there is no person's "
                        "action behind it",
}


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def main():
    print("test_the_inbox_screens_have_a_tool_each")
    posts = sorted({r.rule for r in app.url_map.iter_rules()
                    if r.rule.startswith("/inbox/") and "POST" in (r.methods or ())})
    ok("the Inbox has screen actions to check", len(posts) >= 5, str(posts))
    names = set(registry.registry())
    for rule in posts:
        if rule in TOOLS:
            missing = [t for t in TOOLS[rule] if t not in names]
            ok(f"{rule} has its tool(s): {', '.join(TOOLS[rule])}", not missing, f"not registered: {missing}")
        else:
            ok(f"{rule} has a tool, or a written reason a chat can't do it", rule in BROWSER_ONLY,
               "add its tool to TOOLS, or its reason to BROWSER_ONLY")
    stale = sorted((set(TOOLS) | set(BROWSER_ONLY)) - set(posts))
    ok("...and nothing listed here is a route that no longer exists", not stale, str(stale))
    seen_act = {t["name"] for t in registry.visible_to({"role": "act"})}
    ok("every tool named here is one a person's own AI (an act seat) can see",
       all(t in seen_act for ts in TOOLS.values() for t in ts))


if __name__ == "__main__":
    main()
    _wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
    if _wf.is_file():
        ok("this file is in the workflow's suite list", "test_the_inbox_screens_have_a_tool_each" in _wf.read_text())
    print("\nall ok" if not _failed else f"\n{_failed} FAILED")
    sys.exit(1 if _failed else 0)
