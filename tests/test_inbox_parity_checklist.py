"""THE PARITY CHECKLIST: every owner ruling the old inbox screens carry, checked on the old screens AND on Zernio's
(#1995 change 2, docs/PLAN_INBOX_MIRRORS_ZERNIO.md). The new screens stay behind `inbox_screens` until this passes;
only then does the switch default on.

One section per ruling, each run twice: the switch off (the old screens, every box today) and on (theirs).

WHAT WOULD HAVE TO BREAK FOR THIS TO GO RED, on either screen:
  · the search pill leaves the bar (#1977), or search stops reading what people wrote;
  · the Unanswered or ad filter is not offered, or does not filter;
  · the line above the list says how many wait on a reply again, or stops linking the drafts ready to send;
  · a draft is not offered above the reply box, or loses Send, Edit or Discard;
  · saved replies, their Undo, or the email signature note go missing;
  · someone who said STOP gets a reply box, or the banner that says so is gone, or a send to them is not refused;
  · a spent cap turns a person's send into a 500 instead of words;
  · the notification offer is not made where there is anyone to be told about (#1966);
  · a control a thumb presses is under 48px, or a field under 16px, at 390;
  · the person's dark choice does not reach the page, or their screens do not follow it;
  · the box is stopped and the inbox does not say so (#1357).
Run: python tests/test_inbox_parity_checklist.py
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = str(_tmp / "box.db")
os.environ["AIOS_PAUSE_FILE"] = str(_tmp / "PAUSED")
os.environ["DISPATCH_BEARER_TOKEN"], os.environ["DASH_TOKEN"] = "bearer", "pw"

if not (ROOT / "marketing" / "customer_voice").is_dir():      # a Lead or Content box ships no Inbox Machine
    print("  --   this box ships no Inbox Machine")
    sys.exit(0)

from core import dash, labs, pause, state  # noqa: E402

state.init_db()
from core.dash import theme  # noqa: E402
from core.dispatch import app  # noqa: E402
from marketing.customer_voice import app_ui  # noqa: E402
from marketing.customer_voice.drafter import store as drafts  # noqa: E402
from marketing.customer_voice.inbox import reply, signature, snippets, store  # noqa: E402

FAILS: list[str] = []


def ok(label: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {str(detail)[:500]}"))
    if not cond:
        FAILS.append(label)


SP = "default"
OWNER = state.owner_user()["id"]
owner = app.test_client()
owner.set_cookie(dash.COOKIE, dash.new_session(OWNER))


def say(zcid, platform, who, account, lines, **kw):
    store.upsert_conversation(space=SP, zcid=zcid, platform=platform, participant=who, account_id=account,
                              last_inbound_at="2026-10-06T08:00:00Z", **kw)
    for i, (direction, body) in enumerate(lines):
        store.record_message(space=SP, zcid=zcid, zmid=f"{zcid}-m{i}", direction=direction,
                             sent_by="contact" if direction == "in" else "owner", body=body)


# REALISTIC PEOPLE (they land in a demo's feed if this ever runs on a real box).
say("ig-priya", "instagram", "Priya Shah", "acct-ig", [("in", "Is parking free on Saturdays?"), ("out", "It is!"),
                                                      ("in", "Great, see you at 2:00 then.")])
say("fb-tom", "facebook", "Tom Becker", "acct-fb", [("in", "Is the spring offer still on?"),
                                                   ("out", "It is, until the end of the month!")],
    ad_meta_id="ad-spring", ad_title="Spring facials")
say("ig-grace", "instagram", "Grace Liu", "acct-ig", [("in", "Do you have a waitlist?"), ("in", "STOP")])
store.set_opted_out(SP, "ig-grace")
say("mail-dana", "email", "Dana Ruiz", "hello-glow", [("in", "Could we move it to 3:30 instead?")])
drafts.put(space=SP, zcid="ig-priya", in_reply_to="ig-priya-m2", body="See you Saturday at 2:00, Priya!")
snippets.add(SP, "Opening hours", "Hi {first_name}, we're open Tuesday to Saturday, 9 to 6.")
signature.put(SP, "Maya Ortiz\nGlow Med Spa")
BUILT = " ".join(f.read_text(encoding="utf-8") for f in app_ui.STATIC.glob("*.js"))
STOP_WORDS = "This person has opted out. Nothing is sent to them."


def screens():
    """Each ruling, on the old screens (switch off) and then theirs (on)."""
    for on in (False, True):
        labs.switch("inbox_screens", on, by="test")
        yield ("new" if on else "old"), on
    labs.switch("inbox_screens", False, by="test")


def page(path: str) -> str:
    return owner.get(path, follow_redirects=True).get_data(as_text=True)


print("\nthe search pill in the bar (#1977)")
for name, on in screens():
    lst = page("/inbox/inbox")
    ok(f"[{name}] the messages page's bar is the search", 'class="bar bar-find"' in lst
       and re.search(r'<form class="barfind"[^>]*role="search"', lst) is not None, lst[:300])
    if on:
        hits = {c["id"]: c for c in (owner.get("/inbox/api/conversations?q=parking").get_json() or {})["data"]}
        ok(f"[{name}] ...and it finds what people WROTE, the matching words shown so their list keeps the hit",
           list(hits) == ["ig-priya"] and "parking" in hits["ig-priya"]["lastMessage"], hits)
        ok(f"[{name}] ...the bar's search reaching their list from the address", "'q'" in BUILT or '"q"' in BUILT)
    else:
        found = page("/inbox/inbox?q=parking")
        ok(f"[{name}] ...and it finds what people WROTE", "Priya Shah" in found and "Tom Becker" not in found)

print("\none search at every width, never two (#1977): the bar's at 390, the page's at 1280")
for name, on in screens():
    flat = page("/inbox/inbox").replace(" ", "").replace("\n", "")
    bar_390 = ".bar{display:none;" in flat and "@media(max-width:820px){.bar{display:block}}" in flat
    if on:
        ok(f"[{name}] at 390 the bar's search, theirs in the list not drawn; at 1280 the bar hides and theirs shows",
           bar_390 and "@media(max-width:820px){#ib-inbox.ib-list-search{display:none}}" in flat
           and "ib-list-search" in BUILT)
        p4 = (ROOT / "web" / "inbox-ui" / "patches" / "0004-box-list-filters.patch").read_text(encoding="utf-8")
        ok(f"[{name}] ...and at 1280 theirs asks the box, so it finds what people wrote", "search: asked" in p4
           and "if (search) params.set('q', search);" in p4)
    else:
        ok(f"[{name}] at 390 the bar's search; at 1280 the bar hides and the page's own comes back",
           bar_390 and ".find-wide{display:none}" in flat and ".find-wide{display:block}" in flat)

print("\nthe Unanswered and ad filters")
for name, on in screens():
    lst = page("/inbox/inbox")
    ok(f"[{name}] both filters are offered above the list", 'href="/inbox/inbox?waiting=1"' in lst
       and 'href="/inbox/inbox?from_ad=1"' in lst, lst[:300])
    if on:
        def ids(q):
            return {c["id"] for c in (owner.get("/inbox/api/conversations?" + q).get_json() or {})["data"]}
        ok(f"[{name}] ...and each filters: who is waiting, who came from an ad",
           "ig-priya" in ids("waiting=1") and "fb-tom" not in ids("waiting=1") and ids("from_ad=1") == {"fb-tom"})
    else:
        w, a = page("/inbox/inbox?waiting=1"), page("/inbox/inbox?from_ad=1")
        ok(f"[{name}] ...and each filters: who is waiting, who came from an ad",
           "Priya Shah" in w and "Tom Becker" not in w and "Tom Becker" in a and "Priya Shah" not in a)

print("\nthe line above the list: the drafts ready, linked (owner, 10-06)")
for name, _ in screens():
    lst = page("/inbox/inbox")
    ok(f"[{name}] no waiting count above the list, and the drafts ready to send are a link to them",
       not any("waiting on" in s for s in re.findall(r'<p class="quiet sum">(.*?)</p>', lst)) and re.search(
           r'<p class="quiet sum"><a href="/inbox/waiting"><b>\d+ drafts?</b> ready to send</a></p>', lst) is not None,
       lst[:400])

print("\na draft, with Send, Edit and Discard")
for name, on in screens():
    if on:
        conv = (owner.get("/inbox/api/conversations/ig-priya").get_json() or {}).get("data") or {}
        ok(f"[{name}] the draft rides on the conversation, for the card above their composer",
           ((conv.get("metadata") or {}).get("aios") or {}).get("draft", {}).get("body")
           == "See you Saturday at 2:00, Priya!", conv)
        ok(f"[{name}] ...with Send, Edit and Discard", all(w in BUILT for w in ('"Send"', '"Edit"', "Discard",
                                                                              "/draft/discard")))
    else:
        th = page("/inbox/inbox/ig-priya")
        ok(f"[{name}] the draft is in the reply box, said to be one, with Send",
           "See you Saturday at 2:00, Priya!" in th and 'class="drafted"' in th and "/reply" in th, th[:300])

print("\nsaved replies and the signature")
for name, on in screens():
    if on:
        got = owner.get("/inbox/api/conversations/ig-priya/saved-replies").get_json() or {}
        ok(f"[{name}] saved replies, filled in for this person", any(
            r["words"].startswith("Hi Priya, we're open") for r in got.get("replies", [])), got)
        ok(f"[{name}] ...with an Undo that puts back what was in the box", "Undo: put back what was in the box"
           in BUILT)
        # THE SIGNATURE IS NOT SHOWN ON THE NEW SCREENS (owner, 2026-10-07: "the signature does not need to be
        # displayed at all. people know what their signature is"). The send path still adds it (inbox/signature.py).
        ok(f"[{name}] an email's signature is not shown before Send (owner, 10-07), and is still the box's to add",
           "Your signature goes at the end:" not in BUILT and "signature-note" not in BUILT
           and (ROOT / "marketing" / "customer_voice" / "inbox" / "signature.py").is_file())
    else:
        th = page("/inbox/inbox/ig-priya")
        ok(f"[{name}] saved replies, with an Undo", 'id="snip-pick"' in th and 'id="snip-undo"' in th)
        em = page("/inbox/inbox/mail-dana")
        ok(f"[{name}] an email says its signature before Send", "Your signature goes at the end:" in em
           and "Maya Ortiz" in em)

print("\nSTOP and opt-out")
for name, on in screens():
    if on:
        conv = (owner.get("/inbox/api/conversations/ig-grace").get_json() or {}).get("data") or {}
        ok(f"[{name}] the conversation says they opted out, and their screens draw the banner, not a composer",
           conv.get("metadata", {}).get("aios", {}).get("optedOut") is True and STOP_WORDS in BUILT
           and "composerInstead" in BUILT, conv)
        r = owner.post("/inbox/api/conversations/ig-grace/messages", json={"message": "Just checking in!"},
                       headers={"Idempotency-Key": "parity-stop"})
        ok(f"[{name}] ...and a send to them is refused, saying why", r.status_code == 422
           and (r.get_json() or {}).get("reason") == "opted_out", r.get_json())
    else:
        th = page("/inbox/inbox/ig-grace")
        ok(f"[{name}] the banner, and no reply box", STOP_WORDS in th and "/inbox/inbox/ig-grace/reply" not in th)

print("\na spent cap: words, never a 500")
_real = reply.send_reply


def _spent(**_kw):
    from core.exceptions import BudgetExceeded
    raise BudgetExceeded("zernio monthly cap reached")


reply.send_reply = _spent
try:
    for name, on in screens():
        if on:
            r = owner.post("/inbox/api/conversations/ig-priya/messages", json={"message": "See you then!"},
                           headers={"Idempotency-Key": "parity-cap"})
            body = r.get_json() or {}
            ok(f"[{name}] a person's send that hits the spend cap is told so, not a 500",
               r.status_code != 500 and "That did not send" in str(body.get("error")), (r.status_code, body))
        else:
            th = page("/inbox/inbox/ig-priya")
            nonce = (re.search(r'name="nonce" value="([^"]+)"', th) or re.search(r'name="n" value="([^"]+)"', th))
            r = owner.post("/inbox/inbox/ig-priya/reply", data={"text": "See you then!",
                                                                  **({"nonce": nonce.group(1)} if nonce else {})},
                           follow_redirects=True)
            ok(f"[{name}] a person's send that hits the spend cap is told so, not a 500",
               r.status_code != 500 and "That did not send" in r.get_data(as_text=True), r.status_code)
finally:
    reply.send_reply = _real

print("\na send from a saved reply counts toward its place (most used first)")
HOURS = next(r for r in snippets.all_for(SP) if r["title"] == "Opening hours")


def uses() -> int:
    return int(next(r for r in snippets.all_for(SP) if r["id"] == HOURS["id"])["uses"] or 0)


_sent = {"duplicate": False}
reply.send_reply = lambda **kw: {"status": "ok", "message_id": "m-parity", "idem_key": "k",
                                 "duplicate": _sent["duplicate"]}
try:
    for name, on in screens():
        def send(dup: bool):
            _sent["duplicate"] = dup
            if on:
                return owner.post("/inbox/api/conversations/ig-priya/messages", headers={"Idempotency-Key": "s"},
                                  json={"message": "Hi Priya, we're open...", "snippet": str(HOURS["id"])})
            return owner.post("/inbox/inbox/ig-priya/reply", data={"text": "Hi Priya, we're open...", "n": "s",
                                                                    "snippet": str(HOURS["id"])})
        before = uses()
        send(False)
        once = uses()
        send(True)
        ok(f"[{name}] counted once when it went, never for a repeat the ledger absorbed",
           once == before + 1 and uses() == once, (before, once, uses()))
    ok("[new] ...and their composer carries which saved reply it started from", "setExtra" in BUILT
       and '"snippet"' in BUILT)
finally:
    reply.send_reply = _real

print("\nthe row tags (what stops a reply, the window, the ad, a draft)")
for name, on in screens():
    if on:
        rows = {c["id"]: c for c in (owner.get("/inbox/api/conversations").get_json() or {})["data"]}
        tags = {k: [t["text"] for t in (v["metadata"]["aios"].get("tags") or [])] for k, v in rows.items()}
        ok(f"[{name}] each row carries the old list's tags, by its own rule", "Opted out" in tags.get("ig-grace", [])
           and "From Spring facials" in tags.get("fb-tom", []), tags)
        ok(f"[{name}] ...drawn under the message, with Draft waiting for a draft", "Draft waiting" in BUILT
           and "rowTags" in BUILT)
    else:
        lst = page("/inbox/inbox")
        ok(f"[{name}] each row carries its tags", ">Opted out<" in lst and ">From Spring facials<" in lst)

print("\nthe notification offer (#1966)")
for name, _ in screens():
    lst = page("/inbox/inbox")
    ok(f"[{name}] offered on the list once there is anyone to be told about",
       'id="ownbox-notify"' in lst and 'id="ownbox-notify-yes"' in lst, lst[:300])

print("\n48px targets and 16px fields at 390")
for name, on in screens():
    lst = page("/inbox/inbox")
    flat = lst.replace(" ", "")
    ok(f"[{name}] the filter pills are 48px targets on a phone (owner, 10-06)",
       "@media(max-width:820px){.chip:not(.mkchip){min-height:48px;" in flat)
    ok(f"[{name}] the bar's search is a 48px target with 16px type",
       re.search(r"\.barfind\{[^}]*min-height:48px", flat) is not None and "font-size:max(16px" in flat)
    if on:
        ok(f"[{name}] every control a thumb presses in their screens is 48px, every field 16px, on a mobile",
           "#ib-inbox:is(button,[role=button],[role=combobox],select,textarea," in flat
           and "{min-height:48px}" in flat and "#ib-inbox:is(input,textarea,select){font-size:max(16px,1em)}" in flat)
        ok(f"[{name}] ...and the box's own controls in them are 48px (the draft card, saved replies)",
           BUILT.count("h-12") >= 4)

print("\nlight and dark")
theme.put(OWNER, "dark")
for name, on in screens():
    lst = page("/inbox/inbox")
    ok(f"[{name}] the person's dark choice reaches the page", re.search(r'<html[^>]*data-theme="dark"', lst)
       is not None)
    if on:
        ok(f"[{name}] ...and their screens follow it", 'classList.toggle("dark",root.getAttribute("data-theme")'
           in lst and "--background:var(--ground)" in (app_ui.STATIC / "inbox-ui.css").read_text().replace(" ", ""))
theme.put(OWNER, "light")

print("\na stopped box says so (#1357)")
pause.halt("parity")
try:
    for name, _ in screens():
        ok(f"[{name}] the inbox says the box is stopped", "Your box is stopped" in page("/inbox/inbox"))
finally:
    pause.resume()

print()
print(f"{len(FAILS)} FAILED" if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
