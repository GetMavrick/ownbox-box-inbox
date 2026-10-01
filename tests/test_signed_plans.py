"""Signed plans, the box's half: a plan edited on the box's disk, or copied from another box, is worth nothing.

docs/PLAN_TIER_INTEGRITY.md, step 3 contract, phase B (owner-approved 2026-09-30; the six review points
agreed by OSDev1 the same day). What is measured, with real ed25519 keys and no network:
  * INERT UNTIL ARMED: with no signers file, a push is read exactly as before and nothing new is said
  * a good signed plan is stored and becomes the plan, from its signed text, not the fields beside it
  * the plain fields beside a signed plan change nothing; the signed text edited is FORGED
  * a plan for another order, for another box's key, or signed by a key not shipped is refused
  * `host` is information only: a plan naming another address is still this box's (take-home)
  * the signature covers the exact bytes: the same plan re-spaced is refused
  * once a signed plan is held, an unsigned push no longer changes it
  * an older seq is still refused, and an edited plain seq cannot make Ownbox's next plan look stale
  * the same seq re-confirms (a first signature, a new key) only when it says exactly the plan held
  * a trial ends at `until` by itself, as the offline fallback
  * UNVERIFIABLE (no ssh-keygen) never stores a plan and never drops the one held (rule 2)
  * the check-in reply delivers a signed plan like a push; junk and stale replies change nothing
  * the check-in says `plan.verified` once armed, and nothing new before

Run: python tests/test_signed_plans.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
T = pathlib.Path(tempfile.mkdtemp())
ORDER = "cs_test_signed_A"
os.environ.update(AIOS_HERMETIC_TEST="1", AIOS_DB_PATH=str(T / "box.db"), DISPATCH_BEARER_TOKEN="bearer",
                  DEPLOY_TOKEN="deploy-narrow", AIOS_PROVISION_JSON=str(T / "provision.json"),
                  AIOS_PLAN_SIGNERS=str(T / "ownbox_plan_signers"), AIOS_UPDATE_KEY_PUB=str(T / "box.pub"))
(T / "provision.json").write_text(json.dumps({"tier": "ownbox", "order": ORDER, "host": "acme.ownbox.app"}))

from core import claim, plan_signing, state, tiers  # noqa: E402

claim.PROVISION_JSON = str(T / "provision.json")
state.init_db()
from core import checkin  # noqa: E402
from core.dispatch import app  # noqa: E402

_failed = 0
H = {"Authorization": "Bearer deploy-narrow"}
cl = app.test_client()


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def keygen(name: str) -> pathlib.Path:
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(T / name)], check=True)
    return T / name


OWNBOX = keygen("ownbox_plan_key")          # the provisioner's key (phase A makes the real one)
STRANGER = keygen("stranger_key")           # a key no release ships
BOX = keygen("box_update_key")               # this box's own check-in key
OTHER_BOX = keygen("other_box_key")
(T / "box.pub").write_text((T / "box_update_key.pub").read_text())


def fingerprint(pub: pathlib.Path) -> str:
    return subprocess.run(["ssh-keygen", "-l", "-E", "sha256", "-f", str(pub)], capture_output=True,
                          text=True, check=True).stdout.split()[1]


MY_KEY, OTHER_KEY = fingerprint(T / "box_update_key.pub"), fingerprint(T / "other_box_key.pub")


def sign(text: str, key: pathlib.Path = OWNBOX) -> str:
    msg = T / "msg"
    msg.write_bytes(text.encode("utf-8"))
    (T / "msg.sig").unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", "ownbox-plan", str(msg)],
                   check=True, capture_output=True)
    return (T / "msg.sig").read_text()


def plan_text(seq, tier="pro", *, order=ORDER, box_key=None, until=None, add=(), host="acme.ownbox.app"):
    return json.dumps({"v": 1, "order_id": order, "host": host, "box_key": box_key or MY_KEY, "seq": seq,
                       "tier": tier, "add": list(add), "until": until,
                       "issued_at": "2026-09-30T23:30:00Z"}, sort_keys=True)


def signed(seq, tier="pro", *, key=OWNBOX, **kw):
    text = plan_text(seq, tier, **kw)
    return {"text": text, "sig": sign(text, key)}


def push(body):
    return cl.post("/deploy/plan", json=body, headers=H)


def arm(on=True):
    p = T / "ownbox_plan_signers"
    if on:
        p.write_text(f'ownbox-plans namespaces="ownbox-plan" {(T / "ownbox_plan_key.pub").read_text().strip()}\n')
    else:
        p.unlink(missing_ok=True)
    plan_signing.reset()


def fresh():
    with state.connect() as c:
        c.execute("DELETE FROM box_settings WHERE machine = 'core' AND key = 'plan'")
    plan_signing.reset()


def row() -> dict:
    with state.connect() as c:
        r = c.execute("SELECT value FROM box_settings WHERE machine='core' AND key='plan' AND user_id=''").fetchone()
    return json.loads(r["value"]) if r else {}


def write_row(value: dict):
    with state.connect() as c:
        c.execute("UPDATE box_settings SET value = ? WHERE machine='core' AND key='plan' AND user_id=''",
                  (json.dumps(value),))
    plan_signing.reset()


print("inert until armed")
arm(False)
fresh()
r = push({"seq": 1, "tier": "pro", "signed": signed(1, "ownbox")})
ok("with no signers shipped, a push is read exactly as before: its plain fields, the signed part ignored",
   r.status_code == 200 and tiers.current()["tier"] == "pro", str(r.get_json()))
ok("...nothing is stored that did not used to be", "signed" not in row(), str(row()))
ok("...and the check-in says nothing new", tiers.verified() is None and "verified" not in checkin._plan())
ok("...and a reply carrying a plan is ignored, as every reply was",
   checkin._take_plan(json.dumps({"plan": signed(2, "ownbox")}).encode()) is None
   and tiers.current()["tier"] == "pro")

print("armed: a good signed plan")
arm()
fresh()
r = push({"seq": 5, "tier": "pro", "signed": signed(5, "pro")})
ok("a plan Ownbox signed for this box is stored and becomes the plan",
   r.status_code == 200 and tiers.current()["tier"] == "pro" and tiers.current()["seq"] == 5, str(r.get_json()))
ok("...kept as {text, sig}, so it can be checked again on every read", set(row().get("signed", {})) == {"text", "sig"})
ok("...and the check-in says it is verified", tiers.verified() is True and checkin._plan().get("verified") is True)
fresh()
r = push({"seq": 1, "tier": "pro", "signed": signed(6, "ownbox")})
ok("THE SIGNED TEXT DECIDES, not the fields sent beside it (pro beside, ownbox signed)",
   r.status_code == 200 and tiers.current()["tier"] == "ownbox" and tiers.current()["seq"] == 6, str(r.get_json()))

print("armed: the box's own disk")
v = row()
write_row({**v, "tier": "pro", "add": ["coworkers"]})
ok("EDITING THE PLAIN FIELDS OF A SIGNED PLAN CHANGES NOTHING: still Base, no Coworkers",
   tiers.current()["tier"] == "ownbox" and not tiers.allows("coworkers"), str(tiers.current()))
write_row({**v, "signed": {"text": v["signed"]["text"].replace('"ownbox"', '"pro"'), "sig": v["signed"]["sig"]}})
ok("EDITING THE SIGNED TEXT MAKES IT FORGED: the box falls back to what it was built as, not to Pro",
   tiers.current()["tier"] == "ownbox" and tiers.current()["source"] == "provision.json", str(tiers.current()))
ok("...and the check-in says it is not verified, so Ownbox re-sends", tiers.verified() is False)

print("armed: plans that are not this box's")
fresh()
push({"signed": signed(3, "ownbox")})
for body, label in (({"signed": signed(4, key=STRANGER)}, "signed by a key no release shipped"),
                    ({"signed": signed(4, order="cs_someone_else")}, "for another order"),
                    ({"signed": signed(4, box_key=OTHER_KEY)}, "for another box's key (a Pro plan copied across)"),
                    ({"signed": {"text": "not json", "sig": sign("not json")}}, "whose signed text is not a plan"),
                    ({"signed": "pro"}, "that is not {text, sig} at all")):
    r = push(body)
    ok(f"a plan {label} is refused and never stored", r.status_code == 400 and tiers.current()["seq"] == 3
       and tiers.current()["tier"] == "ownbox", str(r.get_json()))
good = signed(4)
respaced = {"text": good["text"].replace(", ", ",  "), "sig": good["sig"]}
ok("THE EXACT BYTES: the same plan re-spaced, with its signature, is refused",
   push({"signed": respaced}).status_code == 400 and tiers.current()["seq"] == 3)
r = push({"signed": signed(4, host="acme-moved.example.com")})
ok("HOST IS INFORMATION ONLY: a plan naming another address is still this box's (take-home)",
   r.status_code == 200 and tiers.current()["tier"] == "pro", str(r.get_json()))

print("armed: holding a signed plan")
r = push({"seq": 9, "tier": "ownbox"})
ok("an unsigned push no longer changes it", r.status_code == 400 and tiers.current()["tier"] == "pro"
   and "only a plan signed by Ownbox" in r.get_json()["message"], str(r.get_json()))
r = push({"signed": signed(4, "ownbox")})
ok("the same seq saying a different plan is refused as stale, and the reply carries the plan held",
   r.status_code == 409 and r.get_json()["plan"]["tier"] == "pro")
r = push({"signed": signed(4)})
ok("...while the same seq saying the same plan is a re-confirmation, not an error",
   r.status_code == 200 and tiers.current()["tier"] == "pro" and tiers.current()["seq"] == 4, str(r.get_json()))
v = row()
write_row({**v, "seq": 999})
r = push({"signed": signed(5, "ownbox")})
ok("AN EDITED PLAIN SEQ CANNOT MAKE OWNBOX'S NEXT PLAN LOOK STALE: seq 5 lands over a plain 999",
   r.status_code == 200 and tiers.current()["seq"] == 5 and tiers.current()["tier"] == "ownbox", str(r.get_json()))

print("armed: a trial")
past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
future = (datetime.now(timezone.utc) + timedelta(days=13)).isoformat()
push({"signed": signed(6, "pro", until=future)})
ok("a trial plan is Pro until its end", tiers.current()["tier"] == "pro" and tiers.allows("coworkers"))
push({"signed": signed(7, "pro", until=past, add=["coworkers"])})
ok("PAST ITS END THE BOX HOLDS BASE BY ITSELF (the offline fallback; Ownbox also sends Base at seq + 1)",
   tiers.current()["tier"] == "ownbox" and tiers.current()["seq"] == 7, str(tiers.current()))
ok("...and add-ons it was sold stay: only the tier ends", tiers.allows("coworkers"))

print("armed: when the question cannot be answered (rule 2)")
push({"signed": signed(8, "pro")})
real_run = plan_signing.subprocess.run


def no_ssh_keygen(*a, **kw):
    if a and a[0] and a[0][0] == "ssh-keygen" and "-Y" in a[0]:
        raise FileNotFoundError("ssh-keygen")
    return real_run(*a, **kw)


nine = signed(9, "ownbox")                      # signed before ssh-keygen "goes missing"
plan_signing.subprocess.run = no_ssh_keygen
plan_signing.reset()
r = push({"signed": nine})
ok("UNVERIFIABLE never stores a plan", r.status_code == 400 and "could not be checked" in r.get_json()["message"],
   str(r.get_json()))
ok("...AND NEVER DROPS THE ONE HELD: a paying Pro box stays Pro", tiers.current()["tier"] == "pro"
   and tiers.current()["seq"] == 8, str(tiers.current()))
plan_signing.subprocess.run = real_run
plan_signing.reset()

print("the check-in reply")
checkin._take_plan(json.dumps({"plan": signed(10, "ownbox")}).encode())
ok("a reply carrying a signed plan re-confirms it like a push", tiers.current()["seq"] == 10
   and tiers.current()["tier"] == "ownbox")
checkin._take_plan(json.dumps({"plan": signed(10, "pro")}).encode())
ok("a stale one changes nothing", tiers.current()["tier"] == "ownbox")
for junk in (b"", b"not json", b'{"plan": "pro"}', json.dumps({"plan": signed(11, key=STRANGER)}).encode(),
             b"x" * (checkin.REPLY_MAX + 5)):
    checkin._take_plan(junk)
ok("junk, a forged plan and an oversized reply change nothing and never raise", tiers.current()["seq"] == 10)


class Capture:
    def __init__(self, reply=b""):
        self.reply = reply

    def __call__(self, body):
        return 200, "", self.reply


(T / "update_key").write_text((T / "box_update_key").read_text())
os.chmod(T / "update_key", 0o600)
checkin.KEY = T / "update_key"
checkin.LAST = T / "checkin_last.json"
ok("a real check-in run takes the plan from its reply",
   checkin.run(post=Capture(json.dumps({"plan": signed(12, "pro")}).encode())) == "sent"
   and tiers.current()["seq"] == 12 and tiers.current()["tier"] == "pro", str(tiers.current()))
ok("...and a sender that returns only (status, why), as before, still works", checkin.run(post=lambda b: (204, "")) == "sent")
ok("the check-in it sent says the plan is verified", checkin.last()["payload"]["plan"].get("verified") is True,
   str(checkin.last().get("payload", {}).get("plan")))

print("the reply re-confirms at the SAME seq (OSDev1, phase A)")
arm(False)
fresh()
push({"seq": 20, "tier": "pro"})                                  # a plan from before signing
arm()
ok("a box holding an unsigned plan is not yet verified", tiers.verified() is False)
checkin._take_plan(json.dumps({"plan": signed(20, "pro")}).encode())
ok("THE REPLY AT THE SAME SEQ, SAYING THE SAME PLAN, GIVES IT ITS FIRST SIGNATURE",
   tiers.verified() is True and tiers.current()["seq"] == 20 and tiers.current()["tier"] == "pro"
   and "signed" in row(), str(row()))
r = push({"signed": signed(20, "ownbox")})
ok("an equal seq can never swap a plan: the same seq saying Base is refused as stale",
   r.status_code == 409 and tiers.current()["tier"] == "pro", str(r.get_json()))
r = push({"signed": signed(20, "pro", add=["coworkers"])})
ok("...nor add a feature at the same seq", r.status_code == 409 and not tiers.current()["add"])

print("key rotation: re-sign first, then retire the old key (contract point 1)")
NEW = keygen("ownbox_plan_key_2")
signers = T / "ownbox_plan_signers"
signers.write_text(signers.read_text()
                   + f'ownbox-plans namespaces="ownbox-plan" {(T / "ownbox_plan_key_2.pub").read_text().strip()}\n')
plan_signing.reset()
checkin._take_plan(json.dumps({"plan": signed(20, "pro", key=NEW)}).encode())
ok("the next check-in re-signs the held plan with the new key, same seq", tiers.verified() is True
   and tiers.current()["seq"] == 20 and plan_signing.verify(row()["signed"])[0] == plan_signing.OK)
signers.write_text(f'ownbox-plans namespaces="ownbox-plan" {(T / "ownbox_plan_key_2.pub").read_text().strip()}\n')
plan_signing.reset()
ok("THE OLD KEY RETIRES AND NOBODY IS DOWNGRADED: still Pro, still verified",
   tiers.current()["tier"] == "pro" and tiers.verified() is True, str(tiers.current()))

arm(False)
print("\n" + ("ALL OK" if not _failed else f"{_failed} FAILED"))
sys.exit(1 if _failed else 0)
