"""/settings/access says WHERE to connect, not only which key to paste.

MEASURED ON MAIN by OSDev1, 2026-09-23: the screen let a buyer add a key and listed their keys, and
showed no address and no ssh command anywhere. They pasted a key and then had to guess where it
opened. DONE-WHEN, his words: a person who has never seen this box can connect using ONLY what is
rendered on that page — no email, no docs, no asking us.

So this suite reads the page the way that person would:

  1. THE COMMAND IS THE LITERAL ONE, for this box's own name, and it sits ABOVE the paste box.
  2. THE NAME IS CHECKED AGAINST THE BOX. If it leads somewhere else (a box that moved, DNS not
     updated) the command uses this box's own address, because `ssh root@<name>` would reach a
     different machine.
  3. NOTHING UNTYPEABLE IS PRINTED. A page opened at `localhost` never offers `ssh root@localhost`,
     which on the owner's laptop is the laptop.
  4. A REFUSAL IS EXPLAINED — the two a stranger actually meets, each with what to do.
  5. THE FIRST-CONTACT QUESTION IS ANSWERABLE: the box's own host key fingerprint is printed, so
     "are you sure you want to continue connecting?" is a comparison, not a guess.
  6. THE LOOKUPS ARE BOUNDED. A resolver that hangs costs two seconds, not a spinning screen.
  7. THE SCREEN KEEPS THE VOCABULARY. The receptionist machine owns phone, ring, call, dial, line,
     voice and answer (owner, 2026-09-22); this screen used "line" twice before this change.

Run: python tests/test_the_access_page_says_where.py
"""
import base64
import http.server
import os
import pathlib
import re
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AIOS_HERMETIC_TEST"] = "1"
os.environ["AIOS_DB_PATH"] = tempfile.mkdtemp() + "/where.db"
os.environ["DISPATCH_BEARER_TOKEN"] = "bearer"
os.environ["DASH_TOKEN"] = "pw"
for _k in [k for k in list(os.environ) if k.startswith("ZERNIO_API_KEY")] + ["ANTHROPIC_API_KEY"]:
    os.environ.pop(_k, None)
_TMP = pathlib.Path(tempfile.mkdtemp())
os.environ["AIOS_AUTHORIZED_KEYS"] = str(_TMP / "root-ssh" / "authorized_keys")
os.environ["AIOS_SSH_HOST_KEY"] = str(_TMP / "ssh_host_ed25519_key.pub")

from core import box_access as A  # noqa: E402

_failed = 0


def ok(label, cond, detail=""):
    global _failed
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  — {detail}" if not cond and detail else ""))
    if not cond:
        _failed += 1


def blob(ktype: str, payload: bytes) -> str:
    t = ktype.encode()
    return base64.b64encode(len(t).to_bytes(4, "big") + t
                            + len(payload).to_bytes(4, "big") + payload).decode()


BOX_IP, ELSEWHERE = "203.0.113.7", "198.51.100.9"
HOST_ED = blob("ssh-ed25519", b"H" * 32)
(_TMP / "ssh_host_ed25519_key.pub").write_text(f"ssh-ed25519 {HOST_ED} root@acme\n")
USER_ED = blob("ssh-ed25519", b"U" * 32)

_real_resolve, _real_own_ip = A._resolve, A._own_public_ip


def world(ips, box_ip):
    """Set what DNS and the metadata service say, and forget anything cached."""
    A._cache.clear()
    A._resolve = lambda host, timeout=2.0: list(ips)
    A._own_public_ip = lambda timeout=0.5: box_ip


# ── the decision, on its own ─────────────────────────────────────────────────────────────────────
print("— where_to_connect —")

world([BOX_IP], BOX_IP)
w = A.where_to_connect("https://acme.ownbox.app")
ok("a name that leads to this box is what to type", w["command"] == "ssh root@acme.ownbox.app", w)
ok("...and it is known to be this box", w["points_here"] is True, w)

world([ELSEWHERE], BOX_IP)
w = A.where_to_connect("https://acme.ownbox.app")
ok("A NAME THAT LEADS ELSEWHERE IS NOT OFFERED — the box's own address is",
   w["command"] == f"ssh root@{BOX_IP}" and w["points_here"] is False, w)

world([], BOX_IP)
w = A.where_to_connect("https://acme.ownbox.app")
ok("a name that leads nowhere yet falls back to the box's own address",
   w["command"] == f"ssh root@{BOX_IP}", w)

world([BOX_IP], "")
w = A.where_to_connect("https://acme.ownbox.app")
ok("off DigitalOcean (no metadata) the name is still offered, uncompared",
   w["command"] == "ssh root@acme.ownbox.app" and w["points_here"] is None, w)

world([], "")
w = A.where_to_connect("https://acme.ownbox.app")
ok("with nothing resolvable at all, the name is still the best thing to print",
   w["command"] == "ssh root@acme.ownbox.app", w)

world([], "")
w = A.where_to_connect("", "http://localhost:8000/")
ok("NEVER ssh root@localhost — that is the owner's own laptop", w["command"] == "", w)
for bad in ("http://127.0.0.1:8000/", "http://[::1]:8000/", "http://box.localhost/", "http://0.0.0.0/"):
    A._cache.clear()
    ok(f"...nor {bad}", A.where_to_connect("", bad)["command"] == "", A.where_to_connect("", bad))

world([], BOX_IP)
w = A.where_to_connect("", "http://localhost:8000/")
ok("an unnamed box opened at localhost still offers its public address",
   w["command"] == f"ssh root@{BOX_IP}", w)

world([BOX_IP], BOX_IP)
w = A.where_to_connect("", "https://acme.ownbox.app:443/dash")
ok("the page's own address is the fallback when the box was never told its name, port dropped",
   w["host"] == "acme.ownbox.app", w)
w = A.where_to_connect("https://acme.ownbox.app", "http://localhost:8000/")
ok("the box's own name wins over the address the page happens to be open at",
   w["host"] == "acme.ownbox.app", w)
A._cache.clear()
w = A.where_to_connect("https://user:pw@acme.ownbox.app:8443/x")
ok("credentials and port in a base url never reach the command", w["command"] == "ssh root@acme.ownbox.app", w)

# CACHED, so a page refresh is not a DNS query — and a copy, so the caller cannot poison it.
world([BOX_IP], BOX_IP)
first = A.where_to_connect("https://acme.ownbox.app")
first["ips"].append("evil")
A._resolve = lambda host, timeout=2.0: (_ for _ in ()).throw(AssertionError("asked twice"))
second = A.where_to_connect("https://acme.ownbox.app")
ok("a second render inside five minutes reuses the answer", second["command"] == first["command"])
ok("...and a caller changing its copy does not change the cache", "evil" not in second["ips"], second)

ok("the host key fingerprint is the one ssh-keygen -lf prints for it",
   A.host_key_fingerprint() == A.fingerprint(HOST_ED), A.host_key_fingerprint())

# ── the real lookups, bounded ────────────────────────────────────────────────────────────────────
print("\n— the real lookups —")
A._resolve, A._own_public_ip = _real_resolve, _real_own_ip
os.environ.pop("AIOS_HERMETIC_TEST")
import socket  # noqa: E402

_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: (time.sleep(5), [])[1]
t0 = time.monotonic()
got = A._resolve("acme.ownbox.app", timeout=0.3)
took = time.monotonic() - t0
socket.getaddrinfo = _gai
ok("A RESOLVER THAT HANGS IS ABANDONED, not waited for", got == [] and took < 1.5, f"{took:.2f}s {got}")
socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (BOX_IP, 22)),
                                      (socket.AF_INET, socket.SOCK_STREAM, 6, "", (BOX_IP, 22))]
ok("a resolver's duplicate answers are listed once", A._resolve("acme.ownbox.app") == [BOX_IP],
   A._resolve("acme.ownbox.app"))
socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("no such name"))
ok("a name that does not exist is [] rather than an error", A._resolve("nope.invalid") == [])
socket.getaddrinfo = _gai


class _Meta(http.server.BaseHTTPRequestHandler):
    reply = BOX_IP + "\n"

    def do_GET(self):
        body = type(self).reply.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), _Meta)
threading.Thread(target=srv.serve_forever, daemon=True).start()
A._METADATA_IP = f"http://127.0.0.1:{srv.server_address[1]}/ip"
ok("the metadata service's address is read, trimmed", A._own_public_ip() == BOX_IP, A._own_public_ip())
_Meta.reply = "<html>captive portal</html>"
ok("...and anything that is not an IPv4 address is not believed", A._own_public_ip() == "")
srv.shutdown()
ok("...and a metadata service that is not there is simply ''", A._own_public_ip(timeout=0.3) == "")
os.environ["AIOS_HERMETIC_TEST"] = "1"
A._METADATA_IP = "http://169.254.169.254/unused-in-tests"
ok("under the hermetic flag neither lookup touches the network",
   A._resolve("acme.ownbox.app") == [] and A._own_public_ip() == "")

# ── the page, read as a stranger ─────────────────────────────────────────────────────────────────
print("\n— the page, read as a stranger —")
from core import state  # noqa: E402

state.init_db()
from core import dash  # noqa: E402
from core.dash import box_settings as S  # noqa: E402
from core.dispatch import app  # noqa: E402


def client_for(user_id):
    c = app.test_client()
    c.set_cookie(dash.COOKIE, dash.new_session(user_id))
    return c


def visible(html: str) -> str:
    """What a person reads: no stylesheet, no tags, entities decoded."""
    import html as _h
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return _h.unescape(re.sub(r"<[^>]+>", " ", html))


owner = client_for(state.owner_user()["id"])
os.environ["DASHBOARD_BASE_URL"] = "https://acme.ownbox.app"
A.write("")
world([BOX_IP], BOX_IP)
page = owner.get("/settings/access").get_data(as_text=True)
text = visible(page)
ok("THE LITERAL COMMAND IS ON THE PAGE", "ssh root@acme.ownbox.app" in text)
# THE ORDER FOLLOWS WHAT THE READER CAN DO, and this assertion used to say the OPPOSITE. It
# pinned "the connection comes first, a stranger needs to see where the key takes them" —
# OSDev1's own instruction on #1467. Then the owner claimed a real box, ran that command, and got
# `Permission denied (publickey)`, because with no key of his on the box the page's first offer
# was the one thing that could not work. With NO key the paste box comes first; WITH one the
# command comes first, because that is the only reason to come back. Both branches are pinned.
ok("with NO key yet, the paste box comes FIRST",
   0 <= page.find('<textarea') < page.find("ssh root@acme.ownbox.app"),
   f"textarea {page.find('<textarea')} vs command {page.find('ssh root@acme.ownbox.app')}")
ok("...selectable in one tap (the .addr block selects all)",
   re.search(r'class="addr"[^>]*>ssh root@acme\.ownbox\.app<', page) is not None)

# WITH a key already added the order flips: they came back for the command, not the form.
A.write(f"ssh-ed25519 {USER_ED} me@laptop\n")
world([BOX_IP], BOX_IP)
_with = owner.get("/settings/access").get_data(as_text=True)
ok("WITH a key on the box, the command comes FIRST — they came back for it",
   0 <= _with.find("ssh root@acme.ownbox.app") < _with.find('<textarea'),
   f"command {_with.find('ssh root@acme.ownbox.app')} vs textarea {_with.find('<textarea')}")
ok("...and it no longer says the command cannot work", "cannot work yet" not in visible(_with))
A.write("")
world([BOX_IP], BOX_IP)
ok("...and in 16px type, the size that does not zoom a mobile screen", 'font-size:16px">ssh root@' in page)
ok("the address the name leads to is shown, and said to be this box",
   BOX_IP in text and "which is this box" in text)
ok("...with the address-only command as a fallback", f"ssh root@{BOX_IP}" in text)
ok("it says where to type it, on each kind of computer",
   "Terminal" in text and "PowerShell" in text and "Linux" in text)
ok("a box with no key yet says to add one FIRST, pointing UP at the form above it",
   "Add your key above first" in text and "cannot work yet" in text)
ok("the first-contact question is explained", "continue connecting" in text and "yes" in text)
ok("...and the fingerprint to compare is this box's own host key", A.fingerprint(HOST_ED) in text)
ok("REFUSED IS EXPLAINED: a key refusal says which key and how to check",
   "Permission denied (publickey)" in text and "ssh-keygen -lf" in text)
ok("...and a network refusal is told apart from a key refusal",
   "Connection refused" in text and "no key was checked" in text)
ok("...and a name that has not reached their network has a way round",
   "Could not resolve hostname" in text)

A.write(f"ssh-ed25519 {USER_ED} me@laptop\n")
text = visible(owner.get("/settings/access").get_data(as_text=True))
ok("once a key is on the box the add-it-first warning goes", "First add your key below" not in text)

world([ELSEWHERE], BOX_IP)
text = visible(owner.get("/settings/access").get_data(as_text=True))
ok("A NAME POINTED ELSEWHERE IS CALLED OUT, and the command uses this box's address",
   "which is not this box" in text and f"ssh root@{BOX_IP}" in text
   and "ssh root@acme.ownbox.app" not in text)

os.environ["DASHBOARD_BASE_URL"] = ""
world([], "")
page = owner.get("/settings/access", base_url="http://localhost:8000").get_data(as_text=True)
text = visible(page)
ok("opened at localhost with no name and no metadata: nothing untypeable is printed",
   "ssh root@" not in text, text[text.find("Where you connect"):][:300])
ok("...and it says how to make the command appear", "come back to this page" in text)

(_TMP / "ssh_host_ed25519_key.pub").unlink()
os.environ["DASHBOARD_BASE_URL"] = "https://acme.ownbox.app"
world([BOX_IP], BOX_IP)
r = owner.get("/settings/access")
ok("a box with no readable host key still renders, just without the fingerprint",
   r.status_code == 200 and "ssh root@acme.ownbox.app" in visible(r.get_data(as_text=True)))

member = client_for(state.add_user("sam@acme.co", name="Sam", role="member")["id"])
ok("a member still gets no address and no command — the page is the owner's",
   member.get("/settings/access").status_code == 403
   and "ssh root@" not in member.get("/settings/access").get_data(as_text=True))

# ── the vocabulary, whole word, in what a buyer reads ────────────────────────────────────────────
print("\n— the vocabulary —")
RESERVED = re.compile(r"\b(phone|phones|ring|rings|call|calls|called|calling|dial|line|lines|voice|"
                      r"answer|answers|answered)\b", re.I)
(_TMP / "ssh_host_ed25519_key.pub").write_text(f"ssh-ed25519 {HOST_ED} root@acme\n")
screens = []
for ips, box_ip, keys in (([BOX_IP], BOX_IP, ""), ([ELSEWHERE], BOX_IP, ""), ([], BOX_IP, "k"),
                          ([], "", ""), ([BOX_IP], "", "k")):
    world(ips, box_ip)
    A.write(f"ssh-ed25519 {USER_ED} me@laptop\n" if keys else "")
    screens.append(visible(owner.get("/settings/access").get_data(as_text=True)))
os.environ["DASHBOARD_BASE_URL"] = ""
world([], "")
screens.append(visible(S._connect_card(A.where_to_connect("", "http://localhost"), False, "")))
hits = sorted({m.group(0) for s in screens for m in RESERVED.finditer(s)})
# "CALLED OUT" above is this suite's own label, never rendered; everything checked here is rendered.
ok("no reserved noun appears anywhere a buyer reads on this screen, in any branch", not hits, str(hits))

A._resolve, A._own_public_ip = _real_resolve, _real_own_ip
print("\nALL WHERE-TO-CONNECT CHECKS PASS" if not _failed else f"\n{_failed} FAILED")
sys.exit(1 if _failed else 0)
