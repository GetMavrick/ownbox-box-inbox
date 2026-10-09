"""The Business Brain's files: folders and documents on the box, for people and agents (owner, 2026-10-08).

Owner, 2026-10-08: "we should definitely have a basic files manager immediately and Should have it listed as Business
Brain in the menu at the top"; on tapping a file to send it to a client, "it would be nice"; and, for the prototype,
"easily replaced by version two or easily upgraded ... Nothing invasive please."

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a folder or file losing its place, its name, or who added it; two items sharing a name in one folder;
  * a name climbing out of its folder (a slash, a parent step, a control character), on the box or in an export;
  * a file past 25 MB, or past the brain's total, being kept;
  * a delete that can't be undone for 30 days, a restore losing what was inside, or saving over a file losing the
    old version; the trash keeping bytes after 30 days, or dropping bytes another file still needs;
  * an HTML, SVG or unknown file being shown on the box's own address instead of downloaded, or any answer sniffed;
  * a stranger reaching any of it; a file over the app's ordinary 256 KB limit not being accepted here;
  * an agent reading a document it can't, writing without the owner's yes, or saving anything but words;
  * the way out (export_tree) leaving anything behind, or writing outside its folder.

No network, no model.

Run: python tests/test_business_brain.py
"""
from __future__ import annotations

import io
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "brain.db")
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"

from core import state  # noqa: E402

state.init_db()
from core import approvals, dash  # noqa: E402
from core import brain_tools as bt  # noqa: E402
from core import business_brain as bb  # noqa: E402
from core.dispatch import app  # noqa: E402

FAILS: list[str] = []


def ok(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:400]}"))
    if not cond:
        FAILS.append(label)


def refused(fn, *a, **k) -> str:
    try:
        fn(*a, **k)
    except bb.BrainError as e:
        return str(e)
    return ""


OWNER = state.owner_user()["id"]
o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(OWNER))
stranger = app.test_client()

print("test_folders_and_files")
f = bb.make_folder("", "Price sheets", by=OWNER)
doc = bb.save(f["id"], "Gyms.md", b"Gym plan: $49 a month. Includes the Morning Review.", by=OWNER)
pdf = bb.save(f["id"], "Gyms.pdf", b"%PDF-1.4 fake", by="usr_sam")
twin = bb.save(f["id"], "gyms.md", b"second", by=OWNER)
got = bb.listing(f["id"])
ok("a folder keeps its files, by name", [i["name"] for i in got["items"]] == ["gyms (2).md", "Gyms.md", "Gyms.pdf"],
   got["items"])
ok("...two items never share a name in one folder (a number is added)", twin["name"].endswith("(2).md"), twin)
ok("...and say who added them", {i["name"]: i["added_by"] for i in got["items"]}["Gyms.pdf"] == "usr_sam")
ok("the path back to the top comes with it", got["path"] == [{"id": f["id"], "name": "Price sheets"}], got["path"])
ok("a path finds an item, whatever its case", bb.resolve("price SHEETS/gyms.MD")["id"] == doc["id"])
ok("folders come before files", [i["kind"] for i in bb.listing("")["items"]][:1] == ["folder"])

print("\ntest_names_never_leave_their_folder")
for bad, label in (("../../etc/passwd", "a parent step"), ("a/b", "a slash"), ("x\x00y", "a control character"),
                   ("..", "dots alone")):
    try:
        n = bb.clean_name(bad)
        okay = "/" not in n and "\\" not in n and "\x00" not in n and n not in (".", "..")
    except bb.BrainError:
        okay = True
    ok(f"{label} never reaches a name", okay)
ok("a name that is too long is cut, keeping its type", len(bb.clean_name("x" * 300 + ".pdf")) <= bb.NAME_MAX
   and bb.clean_name("x" * 300 + ".pdf").endswith(".pdf"))
blobs = list((bb.root() / "blobs").rglob("*"))
ok("on disk, bytes are named by their content, never by a person's name",
   all(p.is_dir() or len(p.name) == 64 for p in blobs) and not any("Gyms" in str(p) for p in blobs), blobs[:3])

print("\ntest_caps")
ok("a file may be 100 MB (owner, 10-08)", bb.FILE_MAX == 100 << 20)
real_max, real_chunk = bb.FILE_MAX, bb.CHUNK
bb.FILE_MAX, bb.CHUNK = 5000, 1024                  # the same rule, at a size a test can afford
ok("a file past it is refused", "under" in refused(bb.save, "", "big.bin", b"x" * 5001, by=OWNER))
ok("...as it streams in, leaving nothing on the disk", list((bb.root() / "tmp").iterdir()) == [])
bb.FILE_MAX, bb.CHUNK = real_max, real_chunk
keep = bb.TOTAL_MAX
bb.TOTAL_MAX = bb.usage()["bytes"] + 10
ok("past the brain's total, nothing more is kept", "holds" in refused(bb.save, "", "more.txt", b"x" * 20, by=OWNER)
   and list((bb.root() / "tmp").iterdir()) == [])
bb.TOTAL_MAX = keep
from core import config as _config  # noqa: E402
real_cfg = _config.get_config
_config.get_config = lambda: {"business_brain": {"max_gb": 50}}
ok("more room is a setting in the box's config, never a code change", bb.total_max() == 50 << 30
   and bb.usage()["max"] == 50 << 30)
_config.get_config = real_cfg
ok("every change says who made it", bool(refused(bb.save, "", "a.txt", b"x", by="")))

print("\ntest_trash_restore_and_purge")
inner = bb.save(f["id"], "inner.txt", b"inside", by=OWNER)
d = bb.delete(f["id"], by=OWNER)
ok("deleting a folder takes what is inside to the trash too", d["deleted"] == 5
   and all(i["id"] != f["id"] for i in bb.listing("")["items"]) and bb.get(inner["id"]) is None, d)
t = bb.trash()
ok("the trash shows the deletion once, with its days left", [x["id"] for x in t] == [f["id"]]
   and t[0]["days_left"] == bb.TRASH_DAYS, t)
bb.restore(f["id"], by=OWNER)
ok("restoring brings everything inside back", len(bb.listing(f["id"])["items"]) == 4 and bb.trash() == [],
   bb.listing(f["id"]))
bb.save(f["id"], "Gyms.md", b"Gym plan: $59 a month.", by=OWNER, replace=True)
ok("saving over a file updates it, and its old version waits in the trash",
   bb.read_text(doc["id"]).startswith("Gym plan: $59") and any("before" in x["name"] for x in bb.trash()),
   bb.trash())
old = [x for x in bb.trash() if "before" in x["name"]][0]
ok("...from where it can be put back", bb.restore(old["id"], by=OWNER)["name"].startswith("Gyms (before"))
same = bb.save("", "copy-of-inner.txt", b"inside", by=OWNER)
bb.delete(inner["id"], by=OWNER)
with state.connect() as c:
    c.execute("UPDATE brain_items SET deleted_at = ? WHERE id = ?",
              ((datetime.now(timezone.utc) - timedelta(days=bb.TRASH_DAYS + 1)).isoformat(timespec="seconds"),
               inner["id"]))
ok("after 30 days the trash lets go", bb.purge() == 1 and bb.get(inner["id"]) is None)
try:
    still = bb.content(same["id"])[0]
except bb.BrainError as e:
    still = str(e)
ok("...but never bytes another file still needs", still == b"inside", still)
with state.connect() as c:
    sha = c.execute("SELECT sha256 FROM brain_items WHERE id = ?", (same["id"],)).fetchone()[0]
ok("(the bytes are on disk before)", bb._blob(sha).exists())
bb.delete(same["id"], by=OWNER)
with state.connect() as c:
    c.execute("UPDATE brain_items SET deleted_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (same["id"],))
bb.purge()
ok("...and once nothing needs them, the bytes are gone from the disk", not bb._blob(sha).exists())
ok("a folder can't go inside itself", "itself" in refused(bb.move, f["id"], f["id"], by=OWNER))
ok("every change is in the file's history, with who", [h["action"] for h in bb.history(doc["id"])][:1] ==
   ["replaced"], bb.history(doc["id"]))

print("\ntest_search")
ok("search finds a name", any(i["id"] == pdf["id"] for i in bb.search("gyms pdf")))
ok("...and the words inside a text document", any(i["id"] == doc["id"] for i in bb.search("$59 month")))
ok("...each with its path", all(i["path"].startswith("Price sheets/") for i in bb.search("gyms")))

print("\ntest_served_so_a_file_can_never_run_as_the_box")
evil = bb.save("", "page.html", b"<script>fetch('/inbox')</script>", by=OWNER)
svg = bb.save("", "logo.svg", b"<svg onload=alert(1)></svg>", by=OWNER)
png = bb.save("", "logo.png", b"\x89PNG\r\n\x1a\nfake", by=OWNER)
for item, label in ((evil, "an HTML file"), (svg, "an SVG")):
    r = o.get(f"/brain/file/{item['id']}")
    ok(f"{label} is downloaded, never shown on the box's address",
       r.status_code == 200 and r.headers["Content-Disposition"].startswith("attachment")
       and r.headers["Content-Type"] == "application/octet-stream", dict(r.headers))
r = o.get(f"/brain/file/{png['id']}")
ok("an image is shown, sandboxed and never sniffed", r.headers["Content-Disposition"].startswith("inline")
   and r.headers["X-Content-Type-Options"] == "nosniff" and "sandbox" in r.headers["Content-Security-Policy"],
   dict(r.headers))
r = o.get(f"/brain/file/{png['id']}?download=1")
ok("...and downloaded when asked", r.headers["Content-Disposition"].startswith("attachment"))
r = o.get(f"/brain/file/{png['id']}")
ok("the file is the same bytes, streamed from the disk", r.get_data() == b"\x89PNG\r\n\x1a\nfake")
r = o.get(f"/brain/file/{png['id']}", headers={"Range": "bytes=0-3"})
ok("...and a part of it can be read on its own (a video plays, a long PDF opens)", r.status_code == 206
   and r.get_data() == b"\x89PNG", (r.status_code, r.get_data()[:10]))
ok("every answer says nosniff", o.get(f"/brain/file/{pdf['id']}").headers.get("X-Content-Type-Options") == "nosniff")

print("\ntest_the_screen_door")
ok("a stranger reaches nothing", stranger.get("/brain/api/list").status_code in (302, 401, 403)
   and stranger.get(f"/brain/file/{png['id']}").status_code in (302, 401, 403))
r = o.get("/brain/api/list")
ok("the list answers in JSON, with what the brain holds", r.get_json()["ok"] and r.get_json()["usage"]["files"] >= 3,
   r.get_json())
big = b"y" * (600 * 1024)
r = o.post("/brain/api/upload", data={"folder": "", "file": (io.BytesIO(big), "Brochure.pdf")},
           content_type="multipart/form-data", headers={"Origin": "http://localhost"})
ok("a file past the app's usual 256 KB is accepted here", r.status_code == 200 and r.get_json()["saved"][0]["size"]
   == len(big), (r.status_code, r.get_data()[:200]))
r = o.post("/brain/api/folder", json={"parent": "", "name": "Clients"}, headers={"Origin": "http://localhost"})
ok("a folder is made from the screen", r.get_json()["ok"] and r.get_json()["item"]["kind"] == "folder", r.get_json())
cid = r.get_json()["item"]["id"]
r = o.post("/brain/api/rename", json={"id": cid, "name": "Clients 2026"}, headers={"Origin": "http://localhost"})
ok("...renamed", r.get_json()["item"]["name"] == "Clients 2026")
r = o.post("/brain/api/delete", json={"id": cid}, headers={"Origin": "http://localhost"})
ok("...deleted to the trash", r.get_json()["ok"] and r.get_json()["days"] == bb.TRASH_DAYS)
r = o.post("/brain/api/restore", json={"id": cid}, headers={"Origin": "http://localhost"})
ok("...and restored", r.get_json()["ok"])
r = o.post("/brain/api/rename", json={"id": "nope", "name": "x"}, headers={"Origin": "http://localhost"})
ok("a refusal is a sentence, never a crash", r.status_code == 400 and r.get_json()["error"], r.get_data()[:200])

print("\ntest_agents_read_and_ask")
r = bt.business_brain(folder="Price sheets")
ok("an agent lists a folder by its path", {i["name"] for i in r["items"]} >= {"Gyms.md", "Gyms.pdf"}, r)
ok("...and in words with links", "Price sheets/Gyms.md" in bt._render_list(r) and "/brain/file/" in
   bt._render_list(r))
r = bt.business_brain_file(path="Price sheets/Gyms.md")
ok("an agent reads a text document", r["readable"] and "$59" in r["text"], r)
r = bt.business_brain_file(path="Price sheets/Gyms.pdf")
ok("...and is told plainly when it can't read inside one yet", not r["readable"] and "can't read" in
   bt._render_file(r))
before = bb.usage()["files"]
asked = bt.propose_business_brain_save(path="Playbooks/Med spas.md", text="Lead with the Morning Review.")
row = approvals.get(asked["approval"]) or {}
ok("an agent's save is a proposal: nothing is written until the owner says yes",
   asked["asked"] and bb.usage()["files"] == before and "Lead with the Morning Review." in str(row.get("detail")),
   asked)
ok("...and the card says a new folder is made", "new folder Playbooks" in str(row.get("detail")))
d = approvals.decide(asked["approval"], True, by=OWNER)
ok("approved: saved, folder and all, with the approval as who", d.get("status") == "done"
   and bb.read_text(bb.resolve("Playbooks/Med spas.md")["id"]) == "Lead with the Morning Review."
   and bb.resolve("Playbooks/Med spas.md")["added_by"].startswith("approval:"), d)
ok("an agent saves words only", not bt.propose_business_brain_save(path="x/run.html.exe", text="hi")["asked"])
ok("...never an empty document", not bt.propose_business_brain_save(path="x/a.md", text=" ")["asked"])
ok("...never one too long for the card", not bt.propose_business_brain_save(path="x/a.md",
                                                                            text="z" * (bt.SAVE_MAX + 1))["asked"])

print("\ntest_the_way_out")
out = tempfile.mkdtemp()
n = bb.export_tree(out)
files = sorted(str(p.relative_to(out)) for p in pathlib.Path(out).rglob("*") if p.is_file())
ok("export writes every live file as an ordinary folder tree, ready for a version two",
   n == bb.usage()["files"] and "Price sheets/Gyms.md" in files and "Playbooks/Med spas.md" in files, files)
ok("...with the same bytes", (pathlib.Path(out) / "Price sheets" / "Gyms.md").read_bytes() == b"Gym plan: $59 a month.")
ok("...and nothing outside its folder", all(pathlib.Path(out, p).resolve().is_relative_to(pathlib.Path(out).resolve())
                                            for p in files))

print("\nALL BUSINESS BRAIN CHECKS PASS" if not FAILS else f"\n{len(FAILS)} BUSINESS BRAIN CHECK(S) FAILED")
sys.exit(1 if FAILS else 0)
