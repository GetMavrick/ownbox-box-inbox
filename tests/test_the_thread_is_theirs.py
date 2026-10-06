"""The thread is Zernio's where the box has it switched on, and ours everywhere else (#1990 Phase 1, step 1.3).

Owner, 2026-10-06, through OSDev1's review: the new screens go behind a per-box switch, the owner's own box first, and the old
inbox stays the default until his rulings pass on both. marketing/customer_voice/app_ui.py.

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED:
  · with `inbox_screens` off (every box, by default), a conversation opens on anything but the page it always had;
  · with it on, the list is not their inbox inside the box's frame (menu, bottom bar), or the box's look is not
    layered between their reset and their utilities (unlayered, it flattened their rows), or the bottom bar does not
    step aside inside a conversation on a mobile;
  · a link to a conversation does not open it in their inbox, selected, or no longer marks it read;
  · their built files are served to someone not signed in, or anything outside their folder is served, or the
    entries are cached so a mobile runs yesterday's build;
  · the page carries the Zernio key, its setting, or an Authorization header.
  · a swipe's lists (Done, Trash, Junk) are offered while empty, or the one on screen is not pressed, or their
    accent washes out the box's own (its pressed pills, its bottom bar).
Run: python tests/test_the_thread_is_theirs.py
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(pathlib.Path(tempfile.mkdtemp()) / "box.db")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"

if not (ROOT / "marketing" / "customer_voice").is_dir():      # a Lead or Content box ships no Inbox Machine
    print("  --   this box ships no Inbox Machine")
    sys.exit(0)

from core import box_secrets, dash, labs, state  # noqa: E402

state.init_db()
from core.dispatch import app  # noqa: E402
from marketing.customer_voice import app_ui  # noqa: E402
from marketing.customer_voice.inbox import store  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:600]}"))
    if not cond:
        FAILS.append(label)


owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(state.owner_user()["id"]))
stranger = app.test_client()
SP = "default"
NAME = "Priya </script><b>Shah"
store.upsert_conversation(space=SP, zcid="ig-priya", platform="instagram", participant=NAME,
                          last_inbound_at="2026-10-06T05:40:00Z", account_id="acct-ig-glow")
store.record_message(space=SP, zcid="ig-priya", zmid="ig-priya-m1", direction="in", sent_by="contact",
                     body="Do you have any openings for a HydraFacial this Saturday?")


def page(c, path: str) -> tuple[int, str]:
    r = c.get(path)
    return r.status_code, r.get_data(as_text=True)


print("\nthe switch")
ok("inbox_screens is a labs feature, off unless HQ switches it on", "inbox_screens" in labs.KNOWN
   and not labs.on("inbox_screens"))
status, old = page(owner, "/inbox/inbox/ig-priya")
ok("SWITCHED OFF, A CONVERSATION OPENS ON THE PAGE IT ALWAYS HAD", status == 200 and 'id="ib-inbox"' not in old
   and "/inbox/ui/" not in old, old[:300])
status, old_list = page(owner, "/inbox/inbox")
ok("...and the list is the list it always was, its look unlayered", status == 200 and 'id="ib-inbox"' not in old_list
   and "@layer shell" not in old_list)

print("\nswitched on: their inbox, in the box's frame")
labs.switch("inbox_screens", True, by="test")
status, lst = page(owner, "/inbox/inbox")
ok("SWITCHED ON, THE LIST IS THEIRS: their inbox mounted, by version", status == 200 and 'id="ib-inbox"' in lst
   and re.search(r'import \{ mountInbox \} from "/inbox/ui/inbox\.js\?v=[0-9a-f]{12}"', lst) is not None, lst[:400])
ok("...with their styles, by version", re.search(r'href="/inbox/ui/inbox-ui\.css\?v=[0-9a-f]{12}"', lst) is not None)
ok("...IN THE BOX'S FRAME: its menu and its bottom bar are drawn", 'class="tabs"' in lst and 'id="railnav"' in lst)
ok("THE BOX'S LOOK SITS IN A LAYER BETWEEN THEIR RESET AND THEIR UTILITIES, so neither flattens the other",
   "@layer theme, base, shell, components, utilities;" in lst and "layer(shell);" in lst and "@layer shell{" in lst
   and lst.index("@layer theme, base, shell") < lst.index("/inbox/ui/inbox-ui.css"))
ok("INSIDE A CONVERSATION ON A MOBILE THE COMPOSER TAKES THE BOTTOM: the bar steps aside",
   "html.ib-thread-open nav.tabs{display:none}" in lst)
ok("OUR SUMMARY LINE LEADS THEIR LIST: who is waiting on a reply, in words",
   re.search(r'<p class="quiet sum">.*waiting on a reply', lst) is not None
   and lst.index('class="quiet sum"') < lst.index('id="ib-inbox"'), lst[:300])

# A NEW MESSAGE ARRIVES, so the conversation is unread again before a link opens it.
store.upsert_conversation(space=SP, zcid="ig-priya", platform="instagram", participant=NAME,
                          last_inbound_at="2026-10-06T06:20:00Z", account_id="acct-ig-glow")
store.record_message(space=SP, zcid="ig-priya", zmid="ig-priya-m2", direction="in", sent_by="contact",
                     body="Is parking free?")


def unread() -> bool:
    """The list's own reading of it (store.list_conversations computes `unread`; get_conversation does not)."""
    return bool(next((k for k in store.list_conversations(SP) if k["zernio_conversation_id"] == "ig-priya"),
                     {}).get("unread"))


unread_before = unread()
r = owner.get("/inbox/inbox/ig-priya")
ok("A LINK TO A CONVERSATION OPENS IT IN THEIR INBOX, SELECTED (a notification, the Morning Review)",
   r.status_code in (301, 302, 303)
   and r.headers.get("Location", "").endswith("/inbox/inbox?conversation=acct-ig-glow%3Aig-priya"),
   (r.status_code, r.headers.get("Location")))
ok("OPENING IT MARKS IT READ, as it always did", unread_before and not unread(), (unread_before, unread()))
ok("a conversation that is not this box's is still a 404, never a redirect",
   owner.get("/inbox/inbox/somebody-elses").status_code == 404)
box_secrets.put(box_secrets.ZERNIO, "z" * 67)
status, keyed = page(owner, "/inbox/inbox")
ok("NO ZERNIO KEY, ITS SETTING OR AN AUTHORIZATION HEADER ON THE PAGE",
   "z" * 40 not in keyed and box_secrets.ZERNIO not in keyed and "ZERNIO_API_KEY" not in keyed
   and "Authorization" not in keyed)

print("\nour filters, above their list")
ok("OUR FILTERS LEAD THEIR LIST: All, and Unanswered now someone is waiting",
   'class="chips pills"' in keyed and 'href="/inbox/inbox?waiting=1">Unanswered<' in keyed
   and keyed.index('class="chips pills"') < keyed.index('id="ib-inbox"'), keyed[:400])
status, waiting_page = page(owner, "/inbox/inbox?waiting=1")
ok("...Unanswered stays pressed while its list is shown, so the way back to All is one tap",
   status == 200 and re.search(r'class="chip on" aria-pressed="true" href="[^"]*">Unanswered<', waiting_page)
   is not None and 'id="ib-inbox"' in waiting_page)
p4 = (ROOT / "web" / "inbox-ui" / "patches" / "0004-box-list-filters.patch").read_text(encoding="utf-8")
ok("THEIR LIST ASKS THE BOX FOR THE FILTER THE ADDRESS CARRIES, through one patch to their list hook",
   p4.startswith("Ownbox:") and p4.count("+++ b/src/") == 1 and "['waiting', 'from_ad']" in p4, p4[:160])
ok("...and it is built in", '["waiting","from_ad"]' in " ".join(f.read_text(encoding="utf-8")
                                                               for f in app_ui.STATIC.glob("*.js")))
# AND THE BOX FILTERS: someone who came from an ad and has been answered, beside Priya, who is waiting.
store.upsert_conversation(space=SP, zcid="fb-tom", platform="facebook", participant="Tom Becker",
                          ad_meta_id="ad-spring-facials", ad_title="Spring facials",
                          last_inbound_at="2026-10-06T05:10:00Z", account_id="acct-fb-glow")
store.record_message(space=SP, zcid="fb-tom", zmid="fb-tom-m1", direction="in", sent_by="contact",
                     body="Is the spring offer still on?")
store.record_message(space=SP, zcid="fb-tom", zmid="fb-tom-m2", direction="out", sent_by="owner",
                     body="It is, until the end of the month!")


def listed(query: str) -> set:
    r = owner.get("/inbox/api/conversations?" + query)
    return {c["id"] for c in (r.get_json(silent=True) or {}).get("data") or []}


ok("UNANSWERED LISTS WHO IS WAITING, and only them",
   "ig-priya" in listed("waiting=1") and "fb-tom" not in listed("waiting=1"), listed("waiting=1"))
ok("LEADS LISTS WHO CAME FROM AN AD, and only them", listed("from_ad=1") == {"fb-tom"}, listed("from_ad=1"))
ok("...and with neither, the list is everyone it was", {"ig-priya", "fb-tom"} <= listed("sortOrder=desc"),
   listed("sortOrder=desc"))

print("\nthe list's actions: swipe to Done, Trash or a label")
status, before = page(owner, "/inbox/inbox")
ok("NOTHING IN DONE, TRASH OR JUNK, NO PILL FOR THEM: a list that can't change the screen is not offered",
   not any(f">{n}<" in before for n in ("Done", "Trash", "Junk")), before[:400])
store.upsert_conversation(space=SP, zcid="fb-elena", platform="facebook", participant="Elena Brooks",
                          last_inbound_at="2026-10-06T05:30:00Z", account_id="acct-fb-glow")
store.record_message(space=SP, zcid="fb-elena", zmid="fb-elena-m1", direction="in", sent_by="contact",
                     body="Thanks so much, see you next week!")
store.mark_conversation(SP, "fb-elena", status="archived")
status, inbox = page(owner, "/inbox/inbox")
ok("ONE SWIPED TO DONE, AND DONE IS OFFERED beside the inbox's own filters",
   'href="/inbox/inbox?status=archived">Done<' in inbox and ">Trash<" not in inbox and ">Junk<" not in inbox
   and inbox.index('class="chips pills"') < inbox.index('id="ib-inbox"'), inbox[:400])
status, done = page(owner, "/inbox/inbox?status=archived")
ok("...DONE ON SCREEN IS PRESSED, and All is the way back to the inbox",
   status == 200 and 'class="chip on" aria-pressed="true" href="/inbox/inbox">Done<' in done
   and 'class="chip" aria-pressed="false" href="/inbox/inbox">All<' in done and ">Unanswered<" not in done
   and 'id="ib-inbox"' in done, done[:400])
ok("...and the address can ask only for the box's lists", app_ui.view_of("deleted") == "deleted"
   and app_ui.view_of("junk") == "junk" and app_ui.view_of("everything") == "" and app_ui.view_of(None) == "")
p5 = (ROOT / "web" / "inbox-ui" / "patches" / "0005-list-row-slot.patch").read_text(encoding="utf-8")
ok("THEIR LIST TAKES A SLOT AROUND EACH ROW AND A BADGE BESIDE THE NAME, through one patch to their list",
   p5.startswith("Ownbox:") and set(re.findall(r"^\+\+\+ b/(\S+)", p5, re.M))
   == {"src/components/conversation-list/index.tsx", "src/components/conversation-list/row.tsx"}
   and "renderRow?:" in p5 and "rowBadge?:" in p5, p5[:200])
built_all = " ".join(f.read_text(encoding="utf-8") for f in app_ui.STATIC.glob("*.js"))
ok("THE SWIPE IS BUILT IN: Done, Delete and the labels, each told with an Undo, on the box's own route",
   all(w in built_all for w in ('"Moved to Done"', '"Moved to Trash"', '"Undo"', '"Lead"', '"Booked"', '"Customer"',
                                '"Junk"', 'method:"PUT"')), "")
css_flat = (app_ui.STATIC / "inbox-ui.css").read_text(encoding="utf-8").replace(" ", "")
ok("THEIR ACCENT HAS ITS OWN NAME: theirs, unlayered, set the box's --accent and washed out its pressed pills",
   "--accent:var(--wash)" not in css_flat and "--ib-accent:var(--wash)" in css_flat
   and "var(--ib-accent)" in css_flat, "")

r = owner.get("/inbox/api/conversations/fb-elena")
got = (r.get_json(silent=True) or {}).get("data") or {}
ok("A LINK TO ONE IN DONE STILL OPENS: the page asks the box for it alone, in the list's own shape",
   r.status_code == 200 and got.get("id") == "fb-elena" and got.get("accountId") == "acct-fb-glow"
   and got.get("participantName") == "Elena Brooks", (r.status_code, got))
ok("...for a conversation of this box only, to someone signed in only",
   owner.get("/inbox/api/conversations/nobody").status_code == 404
   and stranger.get("/inbox/api/conversations/fb-elena").status_code in (301, 302, 303, 401, 403))
r = owner.get("/inbox/api/conversations/fb-elena/messages")
ok("...and it takes no other address: a thread's messages are still its messages",
   r.status_code == 200 and "messages" in (r.get_json(silent=True) or {})
   and "data" not in (r.get_json(silent=True) or {}), r.get_data(as_text=True)[:200])
ok("...and the page asks for it whenever its list doesn't hold the one in the address",
   "ownbox-conversation" in built_all)
ok("INSIDE A CONVERSATION ON A MOBILE, OUR PILLS STEP ASIDE with the summary line", "html.ib-thread-open .pills" in inbox)
ok("THEIR TOASTS (Moved to Done, Undo) TAKE THE BOX'S COLOURS, so a dark box shows a dark one",
   "[data-sonner-toaster]{--normal-bg:var(--card);--normal-text:var(--ink);" in inbox)
p6 = (ROOT / "web" / "inbox-ui" / "patches" / "0006-email-icon.patch").read_text(encoding="utf-8")
ok("AN EMAIL WEARS AN ENVELOPE, NOT THE LETTER E: one patch to their platform badge",
   p6.startswith("Ownbox:") and set(re.findall(r"^\+\+\+ b/(\S+)", p6, re.M)) == {"src/components/platform-icon.tsx"}
   and '"aria-label":"Email"' in built_all.replace(" ", ""), p6[:160])

print("\nthe AI draft card, above their composer")
built = " ".join(f.read_text(encoding="utf-8") for f in app_ui.STATIC.glob("*.js"))
ok("THE CARD IS IN THEIR INBOX: Drafted for you, with Send, Edit and Discard",
   "Drafted for you. Read it before you send." in built and "/draft/discard" in built
   and "Idempotency-Key" in built, "")
patch = (ROOT / "web" / "inbox-ui" / "patches" / "0001-draft-card-slot.patch")
ptext = patch.read_text(encoding="utf-8") if patch.is_file() else ""
ok("...through one small patch to their thread and composer, which says why it exists",
   ptext.startswith("Ownbox:") and "aboveComposer" in ptext and "fill" in ptext
   and ptext.count("+++ b/src/") == 2, ptext[:200])

print("\nan email, read as an email")
ok("THE BUBBLE FOLDS AN EMAIL'S HISTORY: its subject, its own words, Show the full email",
   "Show the full email" in built and "Hide the earlier email" in built, "")
patch2 = (ROOT / "web" / "inbox-ui" / "patches" / "0002-email-in-the-bubble.patch")
p2 = patch2.read_text(encoding="utf-8") if patch2.is_file() else ""
ok("...through one small patch to their bubble, which only an email from the box changes",
   p2.startswith("Ownbox:") and p2.count("+++ b/src/") == 1 and "emailOf(msg) ?" in p2
   and "msg.message && (" in p2, p2[:200])
email = (ROOT / "web" / "inbox-ui" / "ownbox" / "email-text.tsx").read_text(encoding="utf-8")
ok("...PLAIN TEXT ONLY: the email's own markup never reaches the bubble",
   "dangerouslySetInnerHTML" not in email and "body_html" not in email and "innerHTML" not in email)

print("\nsaved replies in their composer")
from marketing.customer_voice.inbox import snippets  # noqa: E402

snippets.add(SP, "Parking", "Hi {first_name}, parking is free right outside the door. See you soon!")
r = owner.get("/inbox/api/conversations/ig-priya/saved-replies")
got = (r.get_json(silent=True) or {}).get("replies") or []
ok("THIS CONVERSATION'S SAVED REPLIES, FILLED IN FOR THIS PERSON: what fills the composer is what goes",
   r.status_code == 200 and [g["name"] for g in got] == ["Parking"]
   and got[0]["words"].startswith("Hi Priya,") and "{first_name}" not in got[0]["words"], got)
ok("...for a conversation of this box only", owner.get("/inbox/api/conversations/nobody/saved-replies").status_code
   == 404)
ok("...to someone signed in only", stranger.get("/inbox/api/conversations/ig-priya/saved-replies").status_code
   in (301, 302, 303, 401, 403))
ok("...and choosing one fills the composer, never sends", "Saved replies" in built and "ownbox-saved-replies" in built)

print("\nour look on their screens (#1990 1.5)")
css = (app_ui.STATIC / "inbox-ui.css").read_text(encoding="utf-8")
ok("THEIR PALETTE IS THE BOX'S TOKENS: their background is our ground, their accent our link",
   "--background:var(--ground)" in css.replace(" ", "") and "--primary:var(--link)" in css.replace(" ", ""), "")
ok("...AND THEY NEVER SET THE BOX'S OWN --card: theirs, unlayered, turned our dark cards white",
   "--card:oklch" not in css.replace(" ", ""), "")
p3 = (ROOT / "web" / "inbox-ui" / "patches" / "0003-our-look.patch").read_text(encoding="utf-8")
ok("...through one patch to their stylesheet, with their page-wide rules moved into a layer",
   p3.startswith("Ownbox:") and p3.count("+++ b/src/") == 1 and "+@layer base {" in p3, p3[:160])
ok("THEIR DARK FOLLOWS THE BOX'S: the page mirrors data-theme into their .dark class",
   'classList.toggle("dark",root.getAttribute("data-theme")==="dark")' in lst
   and '"class","data-theme"' in lst)

print("\ntheir built files")
r = owner.get("/inbox/ui/thread.js")
ok("served to a signed-in person, as JavaScript", r.status_code == 200 and r.mimetype == "application/javascript"
   and r.get_data(as_text=True).startswith("/*! Zernio unified-inbox"))
ok("...asked again each time, so a mobile never runs yesterday's", r.headers.get("Cache-Control") == "no-cache")
chunk = next((p.name for p in app_ui.STATIC.glob("chunk-*.js")), "")
r = owner.get(f"/inbox/ui/{chunk}")
ok("a chunk, named by its content, keeps for a year", r.status_code == 200
   and "immutable" in (r.headers.get("Cache-Control") or ""), (chunk, r.headers.get("Cache-Control")))
r = owner.get("/inbox/ui/inbox-ui.css")
ok("their styles as CSS", r.status_code == 200 and r.mimetype == "text/css")
r = stranger.get("/inbox/ui/thread.js")
ok("NOT TO SOMEONE WHO IS NOT SIGNED IN", r.status_code in (301, 302, 303, 401, 403), r.status_code)
outside = [p for p in ("/inbox/ui/app.py", "/inbox/ui/..%2Fapp.py", "/inbox/ui/nothing.js", "/inbox/ui/.hidden.js",
                       "/inbox/ui/LICENSE")
           if owner.get(p).status_code != 404]
ok("NOTHING OUTSIDE THEIR FOLDER, AND NOTHING NOT BUILT, IS SERVED", not outside, outside)
# THE NAME RULE ITSELF, since the folder holds nothing it should refuse today: a dotfile, a map, source, a bare name.
ok("...and only a built file's kind of name could ever be: no dotfile, source map, Python or bare name",
   all(app_ui._NAME.fullmatch(n) for n in ("thread.js", "chunk-3ZXEQRE6.js", "inbox-ui.css"))
   and not any(app_ui._NAME.fullmatch(n) for n in (".env.js", "thread.js.map", "app.py", "LICENSE", "a/b.js",
                                                   "..js")))

print()
print(f"{len(FAILS)} FAILED" if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
