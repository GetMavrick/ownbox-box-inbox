"""When a box's own files are changed, updates stop — and the box says so, names them, and puts them back.

#1472 (owner-approved direction, 2026-09-23: "we want to encourage people to be able to build their
own machines"), OSDev4's half:

  R3  THE TRUE REASON, AND THE WAY BACK. An edited box used to be told a release "could not be
      verified … worth telling us about": a security-shaped sentence, pointing at us, for a change
      only the box's own people could undo. Now /settings/updates names the changed files, live,
      and the owner can put them back — the changes saved under my/ FIRST, nothing ever deleted.
  R7  AN UPDATE NEVER STALLS IN SILENCE. A file somebody ADDED at a path the next release adds made
      `git checkout` refuse after verification had passed; `box_update.sh` exited under `set -e`
      and the page said "being installed" for ever. Verification now refuses that as `collision`,
      naming the file — checked here against what git itself does, not against our own idea of it —
      and any other refused checkout is written to the log the page reads.

Run: python tests/test_the_box_says_what_you_changed.py
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "changed.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
os.environ["AIOS_UPDATES_LOG"] = str(T / "updates.jsonl")
for _k in [k for k in list(os.environ) if k.startswith("ZERNIO_API_KEY")] + ["ANTHROPIC_API_KEY"]:
    os.environ.pop(_k, None)

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


if not shutil.which("git"):
    print(("FAIL" if os.environ.get("CI") else "SKIP") + ": git not found; nothing here was tested")
    raise SystemExit(1 if os.environ.get("CI") else 0)

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
GIT = ["git", "-c", "user.name=release", "-c", "user.email=release@ownbox.test"]


def git(repo, *args, check=True):
    r = subprocess.run([*GIT, "-C", str(repo), *args], env=ENV, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args}: {r.stderr.strip()}")
    return r


def repo_with_two_releases(name: str, adds: dict) -> pathlib.Path:
    """v1: app.py + .gitignore. v2: whatever `adds` names. Left checked out at v1, like a box."""
    r = T / name
    r.mkdir()
    git(r, "init", "-q")
    (r / "app.py").write_text("print('v1')\n")
    (r / ".gitignore").write_text("*.log\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "v1")
    git(r, "tag", "v1")
    for rel, text in adds.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text)
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "v2")
    git(r, "tag", "v2")
    git(r, "checkout", "-q", "--detach", "v1")
    return r


from core.release import verify  # noqa: E402

# ── R7: a collision is exactly what git refuses ────────────────────────────────────────────────
print("— R7: collisions, measured against git itself —")
ADDS = {"tools/run.py": "x\n", "new.txt": "x\n", "folder": "a file where the buyer made a folder\n",
        "notes.log": "ignored\n"}
cases = [
    ("the same path", {"new.txt": "mine"}, ["new.txt"]),
    ("a file of theirs where the release needs a folder", {"tools": "mine"}, ["tools"]),
    ("a folder of theirs where the release needs a file", {"folder/mine.py": "mine"}, ["folder/mine.py"]),
    ("a file of theirs BESIDE the release's new one", {"tools/mine.py": "mine"}, []),
    ("an ignored file at the same path", {"notes.log": "mine"}, []),
    ("nothing added at all", {}, []),
]
for i, (label, theirs, expect) in enumerate(cases):
    r = repo_with_two_releases(f"c{i}", ADDS)
    for rel, text in theirs.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text)
    commit = git(r, "rev-parse", "v2^{commit}").stdout.strip()
    got = verify.collisions(r, commit)
    real = git(r, "checkout", "-q", "--detach", "v2", check=False).returncode != 0
    ok(f"{label}: named exactly {expect or 'nothing'}", got == expect, str(got))
    ok(f"...and git AGREES (checkout {'refused' if real else 'worked'})", bool(expect) == real,
       f"collisions={got} git_refused={real}")

# verify_release calls it, in the one place it has to: after the dirty-tree check, before the verdict
src = (ROOT / "core/release/verify.py").read_text()
body = src[src.index("def verify_release"):src.index("def collisions")]
ok("verify_release REFUSES on a collision, naming the file",
   'clash = collisions(repo, commit)' in body and '_refuse(tag, "collision"' in body)
ok("...after the dirty-tree check, so an edited box hears about its edits first",
   body.index('"dirty_tree"') < body.index('"collision"'))

# ── R7: a checkout that fails is written where the page reads ───────────────────────────────────
print("\n— R7: a refused checkout leaves a trace —")
script = (ROOT / "scripts/box_update.sh").read_text()
block = script[script.index("set +e\nexec 3>&2"):script.index("printf '%s\\n' \"$TAG\" > /var/lib/aios/release")]
r = repo_with_two_releases("sh", ADDS)
(r / "new.txt").write_text("mine\n")
log = T / "sh-updates.jsonl"
run = subprocess.run(
    ["bash", "-c", "set -euo pipefail\n" + block.replace(".venv/bin/python", sys.executable)
     .replace("/var/lib/aios/updates.jsonl", str(log)) + "\necho SHOULD-NOT-REACH"],
    cwd=r, env={**ENV, "TAG": "v2"}, capture_output=True, text=True)
line = json.loads(log.read_text().splitlines()[-1]) if log.exists() and log.read_text().strip() else {}
ok("the updater stops, and does not carry on to install", run.returncode != 0
   and "SHOULD-NOT-REACH" not in run.stdout, run.stdout[-300:])
ok("...and writes install_failed with the tag and git's own words",
   line.get("status") == "install_failed" and line.get("tag") == "v2" and "new.txt" in line.get("detail", ""),
   str(line))
ok("...and the box is exactly where it was", git(r, "rev-parse", "HEAD").stdout.strip()
   == git(r, "rev-parse", "v1^{commit}").stdout.strip())
ok("the release checkout line itself is untouched (test_self_deploy pins it)",
   re.search(r'^git checkout --quiet --detach "\$TAG"$', script, re.M) is not None)

# ── R3: the page's state ────────────────────────────────────────────────────────────────────────
print("\n— R3: what the Updates screen says —")
from core import box_updates  # noqa: E402

BOX = repo_with_two_releases("box", {"core/thing.py": "print('ours')\n"})
git(BOX, "checkout", "-q", "--detach", "v2")
box_updates.REPO = BOX
box_updates.UPDATES_LOG = T / "updates.jsonl"
box_updates._installed = lambda: "release/2026.09.23.7"
(BOX / "my").mkdir()
(BOX / "my" / "notes.md").write_text("mine\n")


def write_log(**d):
    d.setdefault("at", "2099-01-01T00:00:00Z")
    box_updates.UPDATES_LOG.write_text(json.dumps(d) + "\n")


write_log(status="up_to_date")
ok("an untouched box: current, and nothing about edits", box_updates.state()["state"] == "current")
ok("files ADDED under my/ are not 'changed'", box_updates.changed_files() == [])

(BOX / "app.py").write_text("print('edited on the box')\n")
(BOX / "core" / "thing.py").unlink()
(BOX / "extra.py").write_text("x = 1\n")
git(BOX, "add", "extra.py")
st = box_updates.state()
ok("AN EDITED BOX SAYS SO, LIVE — even though the last check said up to date",
   st["state"] == "edited" and st["ok"] is False, str(st))
ok("...naming every changed file: edited, deleted, and added to git",
   sorted(st["changed"]) == ["app.py", "core/thing.py", "extra.py"], str(st.get("changed")))
ok("...in words that point at the way back, not at us",
   "Nothing of yours is lost" in st["said"] and "will not install updates until" in st["said"]
   and st["said"].startswith("3 files")
   and "telling us" not in st["said"] and "verified" not in st["said"])

# ── R3: put them back ───────────────────────────────────────────────────────────────────────────
print("\n— R3: putting them back —")
_real_git = box_updates._git
box_updates._git = lambda *a: (_ for _ in ()).throw(RuntimeError("disk")) if a[0] == "apply" else _real_git(*a)
res = box_updates.put_back(by="owner")
ok("if the saved copy cannot be PROVEN, nothing is restored", not res["ok"] and not res["restored"]
   and box_updates.changed_files(), str(res))
box_updates._git = _real_git
for p in (BOX / "my" / "put-aside").glob("*.patch"):
    p.rename(p.with_suffix(".unproven"))                 # keep it — nothing here is deleted either

res = box_updates.put_back(by="owner")
saved = BOX / res["saved"]
ok("put_back succeeds and says where the changes went", res["ok"] and res["saved"].startswith("my/put-aside/")
   and res["saved"] in res["said"], str(res))
ok("THE BOX'S FILES ARE AS THEY CAME", box_updates.changed_files() == [])
ok("...the deleted file is back", (BOX / "core" / "thing.py").read_text() == "print('ours')\n")
ok("...the edited file is the release's again", (BOX / "app.py").read_text() == "print('v1')\n")
ok("NOTHING WAS DELETED: the file they added to git is still on disk, just untracked",
   (BOX / "extra.py").read_text() == "x = 1\n")
ok("...and their own my/ files are untouched", (BOX / "my" / "notes.md").read_text() == "mine\n")
patch = saved.read_text()
ok("the saved patch holds every change", all(n in patch for n in ("app.py", "core/thing.py", "extra.py"))
   and "edited on the box" in patch)
git(BOX, "rm", "-q", "--cached", "--ignore-unmatch", "extra.py")
(BOX / "extra.py").rename(T / "extra.moved")             # step aside so the patch can recreate it
applied = git(BOX, "apply", str(saved), check=False)
ok("...and applying it brings the whole change back — proof it was saved, not approximated",
   applied.returncode == 0 and (BOX / "app.py").read_text() == "print('edited on the box')\n"
   and not (BOX / "core" / "thing.py").exists(), applied.stderr)
git(BOX, "checkout", "-q", "--", ".")
(BOX / "extra.py").unlink(missing_ok=True)
ok("the saved patch lives in my/, which no release carries — so it does not itself stop updates",
   box_updates.changed_files() == [])
ok("a second put_back with nothing changed is harmless and says so",
   box_updates.put_back(by="owner")["said"].startswith("Nothing to put back"))

write_log(status="all_refused", refused=[{"tag": "release/2026.09.24.1", "reason": "dirty_tree", "detail": " M app.py"}])
ok("after putting back, before the next check: the page says the next check installs",
   box_updates.state()["state"] == "waiting")
write_log(status="all_refused", refused=[{"tag": "release/2026.09.24.1", "reason": "collision", "detail": "new.txt"}])
st = box_updates.state()
ok("A COLLISION IS NAMED, with what to do", st["state"] == "collision" and "new.txt" in st["said"]
   and "my/" in st["said"], str(st))
write_log(status="install_failed", tag="release/2026.09.24.1", detail="error")
ok("a failed install says so, instead of 'being installed' for ever",
   box_updates.state()["state"] == "install_failed")
write_log(status="all_refused", refused=[{"tag": "x", "reason": "untrusted_signer", "detail": ""}])
ok("a real verification refusal still reads as one", box_updates.state()["state"] == "refused")

# ── R3: the screen ──────────────────────────────────────────────────────────────────────────────
print("\n— R3: the screen —")
from core import dash, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402


def client_for(user_id):
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(user_id))
    return c


def visible(html: str) -> str:
    import html as _h
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return _h.unescape(re.sub(r"<[^>]+>", " ", html))


owner = client_for(state.owner_user()["id"])
member = client_for(state.add_user("sam@acme.co", name="Sam", role="member")["id"])
write_log(status="up_to_date")
(BOX / "app.py").write_text("print('edited again')\n")
page = owner.get("/settings/updates").get_data(as_text=True)
text = visible(page)
ok("the owner sees the changed file by name", "Changed on this box" in text and "app.py" in text)
ok("...told the changes are saved first, never deleted", "saved first, never deleted" in text)
ok("...and the way back, behind a tick that must be checked", 'action="/settings/updates/put-back"' in page
   and 'name="confirm"' in page and "required" in page[page.index('name="confirm"') - 80:page.index('name="confirm"') + 80])
mtext = visible(member.get("/settings/updates").get_data(as_text=True))
ok("a member sees what changed, and no button", "app.py" in mtext and "Put them back" not in mtext
   and "owner of this box can put them back" in mtext)
ok("a member's POST is refused and changes nothing",
   member.post("/settings/updates/put-back", data={"confirm": "yes"}).status_code == 403
   and box_updates.changed_files() == ["app.py"])
text = visible(owner.post("/settings/updates/put-back", data={}).get_data(as_text=True))
ok("without the tick nothing happens, and the page says why",
   box_updates.changed_files() == ["app.py"] and "tick the box" in text)
text = visible(owner.post("/settings/updates/put-back", data={"confirm": "yes"}).get_data(as_text=True))
ok("with it, the files are put back and the page says where the changes were saved",
   box_updates.changed_files() == [] and "my/put-aside/" in text and "Changed on this box" not in text)
spoof = visible(owner.get("/settings/updates?said=Your+box+is+compromised&ok=1").get_data(as_text=True))
ok("THE PAGE NEVER REPEATS WHAT A LINK TELLS IT TO SAY", "compromised" not in spoof)
anon = app.test_client().post("/settings/updates/put-back", data={"confirm": "yes"})
ok("a stranger is sent to sign in", anon.status_code in (302, 303))

print("\n— the vocabulary —")
RESERVED = re.compile(r"\b(phone|phones|ring|rings|call|calls|called|calling|dial|line|lines|voice|"
                      r"answer|answers|answered)\b", re.I)
(BOX / "app.py").write_text("print('edited once more')\n")
screens = [visible(owner.get("/settings/updates").get_data(as_text=True)),
           visible(member.get("/settings/updates").get_data(as_text=True))]
for d in ({"status": "all_refused", "refused": [{"reason": "collision", "detail": "a"}]},
          {"status": "install_failed", "tag": "t"},
          {"status": "all_refused", "refused": [{"reason": "dirty_tree", "detail": ""}]}):
    write_log(**d)
    screens.append(box_updates.state()["said"])
screens.append(box_updates.put_back(by="owner")["said"])
hits = sorted({m.group(0) for s in screens for m in RESERVED.finditer(s)})
ok("no reserved noun in anything this screen can say", not hits, str(hits))

print("\nALL WHAT-YOU-CHANGED CHECKS PASS" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
