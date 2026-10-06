"""A person sends a photo or a video from Zernio's composer (#1990 Phase 1, step 1.6c).

Their composer posts a form with one `attachment` (JPEG, PNG, GIF or MP4, under 25 MB). The box reads it on the one route
that raises the app's 256 KB body cap, judges it in the send path (the gate, as with opt-out and the ledger), and sends
it through the Zernio gateway as the multipart form Zernio's own app forwards.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  * a photo does not reach Zernio as the file, with the account, the words and Zernio's own conversation id;
  * a file that is not really a JPEG, PNG, GIF or MP4 is sent because its name or the browser said so;
  * a file over 25 MB is sent, or the cap on every other route moves;
  * a file goes to an email thread or an opted-out person, or skips the ledger (a double tap sends it twice);
  * the thread does not show what was sent until the next poll.

No network: the SDK object is stood in for; everything above it is real.

Run: python tests/test_a_photo_goes_out.py
"""
from __future__ import annotations

import io
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp(prefix="photo_"))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(T / "box.db")
os.environ["AIOS_MY_MACHINES"] = str(T / "machines")
os.environ["DASH_TOKEN"] = "test-dash-pw"
os.environ.pop("ZERNIO_API_KEY", None)

from core.vendors.zernio import isolation, transport  # noqa: E402

POSTS = []


class FakeSDK:
    def __init__(self, key, **kw):
        self.key, self.kw = key, kw

    def _post(self, path, data=None, files=None, params=None):
        POSTS.append({"path": path, "files": files, "retries": self.kw.get("max_retries")})
        return {"success": True, "data": {"messageId": f"z-{len(POSTS)}"}}


transport.raw_client = lambda key, **kw: FakeSDK(key, **kw)
isolation.assert_member = lambda account_id, **kw: None

from core import dash, spaces as _spaces, state  # noqa: E402

state.init_db()
_spaces.space_by_name = lambda name, allow_default_alias=True: {"name": name, "zernio_key": "k", "key": "k"}
SPACE = _spaces.DEFAULT

from core.dispatch import app  # noqa: E402
from marketing.customer_voice.inbox import reply, store  # noqa: E402

state.init_db()
_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -- {str(detail)[:400]}" if not cond and detail else ""))
    if not cond:
        _failed += 1


o = app.test_client()
o.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 300_000          # over the app's 256 KB cap on purpose
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def convo(zcid, platform, account="acct-1"):
    store.upsert_conversation(space=SPACE, zcid=zcid, platform=platform, participant="Dana", account_id=account)
    store.record_message(space=SPACE, zcid=zcid, zmid=f"{zcid}-in", direction="in", sent_by="contact", body="Hi")


def send(zcid, content, name="IMG_0042.jpeg", message="Here it is", key=None, **form):
    data = {"accountId": "acct-1", "attachment": (io.BytesIO(content), name), **form}
    if message:
        data["message"] = message
    headers = {"Idempotency-Key": key} if key else {}
    return o.post(f"/inbox/api/conversations/{zcid}/messages", data=data, headers=headers,
                  content_type="multipart/form-data")


print("test_a_photo_goes_out")
convo("ig-1", "instagram")
r = send("ig-1", JPEG, key="tap-1")
ok("a 300 KB photo is accepted on the send route (the app's 256 KB cap is raised for it alone)",
   r.status_code == 200 and (r.get_json() or {}).get("messageId") == "z-1", (r.status_code, r.get_data()[:200]))
p = POSTS[-1] if POSTS else {}
parts = {k: v for k, v in (p.get("files") or [])}
ok("it reaches Zernio's send for the conversation, as their own app's form",
   p.get("path") == "/v1/inbox/conversations/ig-1/messages", p.get("path"))
ok("...the file, named for what it really is, with its type", parts.get("attachment", (None,) * 3)[1:] == (JPEG, "image/jpeg")
   and parts["attachment"][0] == "IMG_0042.jpg", parts.get("attachment", ("",))[0])
ok("...with the account and the words", parts.get("accountId") == (None, "acct-1")
   and parts.get("message") == (None, "Here it is"), {k: v for k, v in parts.items() if k != "attachment"})
ok("...and the SDK never retries it (a retry is a second DM)", p.get("retries") == 1, p.get("retries"))
got = (o.get("/inbox/api/conversations/ig-1/messages").get_json() or {}).get("messages") or []
mine = next((m for m in got if m["id"] == "z-1"), {})
ok("the thread shows what was sent at once, by name and kind",
   mine.get("attachments") == [{"type": "image", "url": "", "name": "IMG_0042.jpg", "mimeType": "image/jpeg"}]
   and mine.get("message") == "Here it is", mine)
n = len(POSTS)
r = send("ig-1", JPEG, key="tap-1")
ok("a double tap sends it once (the ledger, as for words)", len(POSTS) == n and r.status_code == 200, r.get_json())
r = send("ig-1", MP4, name="clip.mov", message="")
ok("a video with no words goes too, named for what it is",
   r.status_code == 200 and dict(POSTS[-1]["files"]).get("attachment", ("",))[0] == "clip.mp4"
   and "message" not in dict(POSTS[-1]["files"]), r.get_json())

print("\ntest_only_what_their_composer_sends")
n = len(POSTS)
r = send("ig-1", b"<script>alert(1)</script>", name="cat.jpg")
ok("a file that only says it is a photo is refused, in words", r.status_code == 422 and len(POSTS) == n, r.get_json())
r = send("ig-1", b"%PDF-1.7" + b"\x00" * 50, name="quote.pdf")
ok("a PDF is refused (their composer sends photos and MP4 only)", r.status_code == 422 and len(POSTS) == n, r.get_json())
big = b"\xff\xd8\xff" + b"\x00" * (reply.MAX_ATTACHMENT + 10)
r = send("ig-1", big)
ok("a file over 25 MB is refused and nothing is sent", r.status_code in (413, 422) and len(POSTS) == n,
   (r.status_code, r.get_data()[:120]))
r = send("ig-1", PNG, voiceNote="true")
ok("a voice note waits for Phase 2 (WhatsApp, Telegram)", r.status_code == 501 and len(POSTS) == n, r.get_json())
ok("the 256 KB cap on every other route has not moved",
   o.put("/inbox/api/settings", json={"selectedAccountIds": ["x" * 300_000]}).status_code == 413)
ok("...nor for words sent to this one: only a form with a file is let past it",
   o.post("/inbox/api/conversations/ig-1/messages", json={"message": "x" * 300_000}).status_code == 413
   and len(POSTS) == n)

print("\ntest_never_where_it_must_not_go")
convo("mail-1", "email")
r = send("mail-1", PNG)
ok("a file is never sent to an email thread, and says why (the mail path sends words)",
   r.status_code == 422 and len(POSTS) == n and "words only" in (r.get_json() or {}).get("error", ""), r.get_json())
convo("ig-2", "instagram")
store.set_opted_out(SPACE, "ig-2")
r = send("ig-2", PNG)
ok("nor to someone who opted out", r.status_code == 422 and len(POSTS) == n, r.get_json())
o2 = app.test_client()
r = o2.post("/inbox/api/conversations/ig-1/messages", data={"attachment": (io.BytesIO(PNG), "a.png")},
            content_type="multipart/form-data")
ok("nor by someone not signed in", r.status_code in (302, 401, 403) and len(POSTS) == n, r.status_code)

print("\nALL PHOTO CHECKS PASS" if not _failed else f"\n{_failed} PHOTO CHECK(S) FAILED")
sys.exit(1 if _failed else 0)
