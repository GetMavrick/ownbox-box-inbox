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
ok("ABOVE THEIR LIST, NO WAITING COUNT (owner, 10-06): only the drafts ready to send, when there are any, linked",
   not any("waiting on" in s for s in re.findall(r'<p class="quiet sum">(.*?)</p>', lst)))

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
ok("PROSPECTS LISTS WHO CAME FROM AN AD, and only them", listed("from_ad=1") == {"fb-tom"}, listed("from_ad=1"))
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
ok("INSIDE A CONVERSATION ON A MOBILE, OUR PILLS STEP ASIDE with the summary line, the offer and the cards below",
   "html.ib-thread-open :is(.sum,.pills,#ownbox-notify,.ib-below){display:none}" in inbox)
ok("THEIR TOASTS (Moved to Done, Undo) TAKE THE BOX'S COLOURS, so a dark box shows a dark one",
   "[data-sonner-toaster]{--normal-bg:var(--card);--normal-text:var(--ink);" in inbox)
p6 = (ROOT / "web" / "inbox-ui" / "patches" / "0006-email-icon.patch").read_text(encoding="utf-8")
ok("AN EMAIL WEARS AN ENVELOPE, NOT THE LETTER E: one patch to their platform badge",
   p6.startswith("Ownbox:") and set(re.findall(r"^\+\+\+ b/(\S+)", p6, re.M)) == {"src/components/platform-icon.tsx"}
   and '"aria-label":"Email"' in built_all.replace(" ", ""), p6[:160])

print("\nwhat a walk of their screens found (the parity walk)")
ok("THE GEAR GOES TO THE INBOX'S SETTINGS, not the box's System Settings",
   'settingsHref:"/inbox/settings"' in built_all)
ok("...THEIR IN-TAB NOTIFICATIONS ARE OFF where the box offers its own", "browserNotifications:!1" in built_all)
p7 = (ROOT / "web" / "inbox-ui" / "patches" / "0007-the-box-platforms.patch").read_text(encoding="utf-8")
ok("THEIR PLATFORM MENU OFFERS THE BOX'S PLATFORMS, email among them, and their unread sort says unread",
   p7.startswith("Ownbox:") and "'Unread first'" in p7 and "'email'" in p7 and '"Unread first"' in built_all, p7[:120])
ok("A SEND THAT MAY HAVE LANDED IS NEVER HANDED BACK TO BE SENT AGAIN (their composer, on a 409)",
   "status===409" in built_all.replace(" ", ""))

print("\na row's actions without a swipe (a screen reader, a keyboard)")
ok("EACH ROW HAS ITS ACTIONS AS REAL BUTTONS NAMED FOR THE PERSON, for a screen reader on any screen",
   '"ib-acts"' in built_all and "Actions for " in built_all and "Label for " in built_all)
_flat_inbox = page(owner, "/inbox/inbox")[1].replace(" ", "")
ok("...drawn nowhere until a keyboard reaches them, then where the mouse's bar sits",
   "#ib-inbox.ib-acts{position:absolute;width:1px;height:1px;" in _flat_inbox
   and "#ib-inbox.ib-acts:focus-within{width:auto;height:auto;" in _flat_inbox)

print("\nwhat sits below the inbox is reachable")
_css_flat = (app_ui.STATIC / "inbox-ui.css").read_text(encoding="utf-8").replace(" ", "")
ok("THEIR PAGE LOCK IS GONE: the page scrolls to other machines' cards below the inbox (it never could)",
   "html,body{height:100%;overflow:hidden}" not in _css_flat and "html,body{overflow:hidden" not in _css_flat)
status, _below = page(owner, "/inbox/inbox")
ok("...and those cards start below the screen, not peeking under the list from the room kept for the bottom bar",
   'querySelector(".ib-below");if(b)b.style.marginTop=pb?pb+"px":""' in _below)

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
_inner = re.findall(r"dangerouslySetInnerHTML=\{\{ __html: ([^}]+) \}\}", email)
ok("...THE SENDER'S MARKUP NEVER REACHES THE PAGE: the only HTML put in it is the box's own render.readable (escaped "
   "first), never a field of the sender's", _inner and all(x.strip() in ("words", "email.quotedReadable ?? ''")
                                                         for x in _inner) and "body_html" not in email, _inner)
ok("...AND A SENDER'S HTML ONLY EVER IN A SANDBOX WITH NO SCRIPTS: same origin only so its height can be read "
   "(#2029), never allow-scripts beside it, no referrer",
   "const FRAME_SANDBOX = 'allow-same-origin allow-popups allow-popups-to-escape-sandbox';" in email
   and "allow-scripts" not in email and 'referrerPolicy="no-referrer"' in email and "srcDoc={doc}" in email)
ok("A FRAME AS TALL AS ITS EMAIL (owner, 10-06: 'HTML emails are not displayed!!'): it starts at the box's guess, never "
   "over 600px, is measured once drawn (and again on a resize) from the email's own height, and loads at once",
   "const FRAME_START_PX = 600;" in email and "onLoad={measure}" in email
   and "documentElement?.getBoundingClientRect().height" in email and "body?.scrollHeight" in email
   and "addEventListener('resize', measure)" in email and 'loading="lazy"' not in email and "loading=" not in email)
ok("...AND THE THREAD SETTLES ON ITS NEWEST MESSAGE (owner, 10-07: 'the scroll bars start at the very bottom'): one "
   "placer per thread re-places it on every change of layout while the thread settles, until he scrolls, taps or types; "
   "the newest email at its top (stopped at the bottom when it fits), anything else at the bottom",
   "new ResizeObserver(" in email and "const SETTLE_MS = 4000;" in email and "querySelectorAll<HTMLElement>('[data-message-id]')" in email
   and "c.scrollTop = Math.max(0, top - 8);" in email and "c.scrollTop = c.scrollHeight;" in email
   and "'wheel', 'touchstart', 'pointerdown', 'keydown'" in email and "conv={msg.conversationId}" in email)

print("\nan HTML email, on the owner's box (2026-10-06: 'they all look like garbage')")
from marketing.customer_voice.inbox import render as _render  # noqa: E402
_SRC = ('<!doctype html><html xmlns="http://www.w3.org/1999/xhtml"><head><title>LoopCV</title><style>p{color:red}</style>'
        '</head><body><p>Hi Brian,</p><p>Your weekly report is ready.</p><script>alert(1)</script></body></html>')
store.upsert_conversation(space=SP, zcid="mail-loop", platform="email", participant="no-reply@loopcv.com",
                          last_inbound_at="2026-10-06T07:00:00Z", account_id="hello-box")
store.record_message(space=SP, zcid="mail-loop", zmid="mail-loop-m1", direction="in", sent_by="contact", body=_SRC,
                     detail={"body_html": _SRC, "body_text": "", "headers": {"Subject": "Your weekly report"}})
_LONG = "https://www.linkedin.com/comm/jobs/view/4123?trackingId=" + "x" * 300
store.upsert_conversation(space=SP, zcid="mail-li", platform="email", participant="LinkedIn Job Alerts",
                          last_inbound_at="2026-10-06T06:50:00Z", account_id="hello-box")
store.record_message(space=SP, zcid="mail-li", zmid="mail-li-m1", direction="in", sent_by="contact",
                     body="Apply now: " + _LONG, detail={"body_text": "Apply now: " + _LONG, "headers": {}})
_loop = (owner.get("/inbox/api/conversations/mail-loop/messages").get_json() or {}).get("messages", [{}])[0]
_aios = (_loop.get("metadata") or {}).get("aios") or {}
ok("A MAIL WITH NO TEXT PART READS AS ITS WORDS, never '<!doctype html>'", "<" not in _loop.get("message", "<")
   and "Your weekly report is ready." in _loop.get("message", "") and "alert(1)" not in _loop.get("message", ""),
   _loop.get("message", "")[:200])
ok("...AND IS SHOWN AS THE SENDER WROTE IT, in the old thread's frame: its CSP inside, the sender's own HTML",
   "Content-Security-Policy" in (_aios.get("frame") or {}).get("doc", "") and "Your weekly report is ready."
   in (_aios.get("frame") or {}).get("doc", "") and (_aios.get("frame") or {}).get("height", 0) >= 140, _aios)
_JOB = ('<!--[if (gte mso 9)|(IE)]><table cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->'
        '<table role="presentation" width="100%"><tbody><tr><td><table role="presentation"><tbody><tr>'
        '<td><a href="https://www.linkedin.com/comm/jobs/view/4123/?trackingId=a%3D%3D">AI Architect</a></td></tr>'
        '<tr><td><p>Acme Health · Irvine, CA</p></td></tr></tbody></table></td></tr></tbody></table>'
        '<!--[if (gte mso 9)|(IE)]></td></tr></table><![endif]-->')
_LI_HTML = ('<html xmlns="http://www.w3.org/1999/xhtml" lang="en"><head><meta name="viewport" content="width=device-width; '
            'initial-scale=0.666667"><!--[if mso]><style type="text/css"> </style><![endif]--><style>@media (max-width:'
            '600px){.hide-mobile{display:none!important}}</style></head><body dir="ltr" style="margin:0;padding:0">'
            '<div style="display:none;max-height:0;overflow:hidden">2 new jobs</div>'
            '<h2>Your job alert for senior AI Consultant</h2>' + _JOB * 30
            + '<a href="javascript:fetch(\'/inbox/api/conversations\')">Unsubscribe</a>'
            '<!--[if !mso]><!--><img alt="LinkedIn" src="https://static.licdn.com/x.png"><!--<![endif]--></body></html>')
store.upsert_conversation(space=SP, zcid="mail-jobs", platform="email", participant="LinkedIn Job Alerts",
                          last_inbound_at="2026-10-06T23:02:00Z", account_id="hello-box")
store.record_message(space=SP, zcid="mail-jobs", zmid="mail-jobs-m1", direction="in", sent_by="contact", body="",
                     detail={"body_html": _LI_HTML, "body_text": "", "headers": {"Subject": "Your job alert"}})
_jobs = (owner.get("/inbox/api/conversations/mail-jobs/messages").get_json() or {}).get("messages", [{}])[0]
_jf = ((_jobs.get("metadata") or {}).get("aios") or {}).get("frame") or {}
ok("A LINKEDIN-SHAPED ALERT (dozens of nested tables, Outlook comments): the box's guess runs far past the email, which "
   "is why the screen measures it rather than trusting the guess",
   _jf.get("height", 0) >= 3000 and "Your job alert for senior AI Consultant" in _jf.get("doc", "")
   and _jf.get("doc", "").count("AI Architect") == 30, _jf.get("height"))
ok("...and its frame carries no link that could run script, while its job links are kept",
   "javascript:" not in _jf.get("doc", "").lower() and 'data-ownbox-removed="href"' in _jf.get("doc", "")
   and "https://www.linkedin.com/comm/jobs/view/4123/?trackingId=a%3D%3D" in _jf.get("doc", ""), _jf.get("doc", "")[-400:])
ok("SHOW IMAGES (owner, 10-07): an email that asks for pictures from the internet is sent the box's two policies to "
   "swap, and one that asks for none is sent nothing to press",
   (_jf.get("images") or {}) == {"hidden": _render.csp_meta(), "shown": _render.csp_meta(images=True)}
   and "images" not in (_aios.get("frame") or {}), (_jf.get("images"), (_aios.get("frame") or {}).keys()))
ok("...the screen swaps the one for the other, never adds a second, for that email alone and remembered nowhere",
   "frame.doc.replace(swap.hidden, swap.shown)" in email and "setShown(true)" in email
   and "localStorage" not in email and "sessionStorage" not in email)
_rows = {c["id"]: c for c in (owner.get("/inbox/api/conversations").get_json() or {})["data"]}
ok("ITS ROW'S LAST LINE IS ITS WORDS, not its source", _rows.get("mail-loop", {}).get("lastMessage", "").startswith(
   "Hi Brian, Your weekly report is ready."), _rows.get("mail-loop", {}).get("lastMessage"))
_li = (owner.get("/inbox/api/conversations/mail-li/messages").get_json() or {}).get("messages", [{}])[0]
_li_html = ((_li.get("metadata") or {}).get("aios") or {}).get("readable", "")
ok("A WALL OF TRACKING LINKS READS AS SHORT LINKS: the old thread's render.readable, every character kept in the href",
   f'href="{_LONG}"' in _li_html.replace("&amp;", "&") and "…" in _li_html and _LONG not in _li_html.split(">", 1)[-1],
   _li_html[:240])
ok("THE CONVERSATION GETS THE WINDOW ON A DESKTOP, not the old list's 780px column",
   "@media (min-width:821px){.ib .wrap{max-width:1600px}}" in page(owner, "/inbox/inbox")[1])

ok("AN EMAIL IS A CARD, NOT A CHAT BUBBLE (owner, 10-06, the enterprise layout): a readable column, headed by who wrote it",
   "max-w-[760px]" in p2 and "const card = Boolean(emailOf(msg));" in p2 and "max-w-[760px]" in built
   and "msg.senderName || 'You'" in email, "")

_div = (ROOT / "web" / "inbox-ui" / "ownbox" / "list-divider.tsx").read_text(encoding="utf-8")
ok("THE LIST'S WIDTH IS THE PERSON'S: a divider dragged or moved by the arrow keys, remembered, 280 to 560px",
   'role="separator"' in _div and "ArrowLeft" in _div and "localStorage" in _div and "LIST_MIN = 280" in _div
   # A CANCELLED DRAG KEEPS THE WIDTH (a cancel's clientX is often 0): never the drop's handler.
   and "onPointerCancel={onCancel}" in _div and "onPointerCancel={onUp}" not in _div
   and "md:w-[var(--ib-list-w,24rem)]" in (ROOT / "web" / "inbox-ui" / "patches" / "0005-list-row-slot.patch")
   .read_text(encoding="utf-8") and "width:var(--ib-list-w,24rem)" in (app_ui.STATIC / "inbox-ui.css").read_text())

_dp = (ROOT / "web" / "inbox-ui" / "ownbox" / "details-panel.tsx").read_text(encoding="utf-8")
ok("THE PERSON BESIDE THE CONVERSATION on a wide screen (1800px up, so at 1500 the email keeps its column): their label (changed there), Prospect, waiting, Done, Delete",
   "min-[1800px]:flex" in _dp and "min-[1440px]" not in _dp and "actions.act(conversation, { disposition:" in _dp and "Prospect" in _dp
   and "Waiting on a reply" in _dp and "Came from an ad" in built)
store.mark_conversation(SP, "ig-priya", disposition="lead")
_one = (owner.get("/inbox/api/conversations/ig-priya").get_json() or {}).get("data") or {}
ok("...read from the list's own row, so a link to one shows its label and whether it waits, as the list would",
   (_one.get("metadata") or {}).get("aios", {}).get("disposition") == "lead", _one.get("metadata"))
store.mark_conversation(SP, "ig-priya", disposition=None)
_real_list, _real_log, _logged = store.list_conversations, app_ui.log, []


class _Log:
    def warning(self, event, **kw):
        _logged.append((event, kw))


def _list_fails(*a, **kw):
    raise RuntimeError(f"no such column near ig-priya {NAME}")


store.list_conversations, app_ui.log = _list_fails, _Log()
try:
    _res = owner.get("/inbox/api/conversations/ig-priya")
finally:
    store.list_conversations, app_ui.log = _real_list, _real_log
ok("...and when the list's query cannot answer, the stored row still opens it and a warning says so: the error's "
   "type only, never the conversation or the person", _res.status_code == 200
   and ((_res.get_json() or {}).get("data") or {}).get("id")
   and _logged == [("inbox.ui_conversation_unlisted", {"error": "RuntimeError"})], (_res.status_code, _logged))

_sr = (ROOT / "web" / "inbox-ui" / "ownbox" / "saved-replies.tsx").read_text(encoding="utf-8")
_la = (ROOT / "web" / "inbox-ui" / "ownbox" / "list-actions.tsx").read_text(encoding="utf-8")
_p1 = (ROOT / "web" / "inbox-ui" / "patches" / "0001-draft-card-slot.patch").read_text(encoding="utf-8")
_ib = (ROOT / "web" / "inbox-ui" / "ownbox" / "entry" / "inbox.tsx").read_text(encoding="utf-8")
ok("SAVED REPLIES LIVE INSIDE THE COMPOSER (owner, 10-07; OSDev1's assignment): one button in its own row (patch 0001's "
   "inComposer), picking fills the text, its Undo a toast that a send or a new conversation dismisses; nothing parked "
   "above the composer",
   "inComposer?: (ctx: ComposerSlot) => ReactNode;" in _p1 and "actions={inComposer?.(slot)}" in _p1
   and "{!recordingVoice && actions}" in _p1 and "setSent((n) => n + 1);" in _p1
   and "toast.dismiss(UNDO_TOAST);" in _sr and "[conversation.id, sent]" in _sr and "label: 'Undo'" in _sr
   and "inComposer={(ctx) =>" in _ib and "<SavedReplies" not in _ib.split("inComposer={(ctx) =>")[0].split("aboveComposer={(ctx) =>")[-1])
ok("A LIST IT JUST STARTED IS OFFERED AT ONCE (walk, 10-07): the first conversation swiped into Done, Trash or Junk "
   "adds that list's pill, in the box's order and markup, with no reload",
   "offerList(into)" in _la and "document.querySelector('.chips.pills')" in _la
   and "['archived', 'Done'], ['deleted', 'Trash'], ['junk', 'Junk']" in _la)

_body = app_ui.inbox_body()
_p8 = (ROOT / "web" / "inbox-ui" / "patches" / "0008-no-today-chip.patch").read_text(encoding="utf-8")
ok("A MESSAGE GETS THE PHONE'S SCREEN (owner, 10-07, Gmail's app the model): inside a conversation on a phone, no search "
   "bar, no gutter, a touch of room, an email edge to edge, and no Today chip over a thread that is all today",
   "@media (max-width:820px){html.ib-thread-open .bar-find{display:none}" in _body
   and "html.ib-thread-open .wrap{padding-left:0;padding-right:0}" in _body
   and r"html.ib-thread-open #ib-inbox .max-w-\[760px\]{max-width:none;border:0;border-radius:0;" in _body
   and ": msgDate.toDateString() !== new Date().toDateString();" in _p8
   and r'#ib-inbox textarea[aria-label="Message"]:not(:placeholder-shown){flex:1 1 100%}' in _body
   and not (ROOT / "web" / "inbox-ui" / "ownbox" / "signature-note.tsx").exists())

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

print("\nthe composer in dark")
ok("THEIR COMPOSER'S FIELD DRAWS NO BOX OF ITS OWN IN DARK, inside the composer's",
   "html.dark #ib-inbox textarea{background-color:transparent}" in page(owner, "/inbox/inbox")[1])

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
