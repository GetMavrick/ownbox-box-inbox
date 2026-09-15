#!/usr/bin/env python3
"""doctor.py — one screen: what this box is, what is configured, what is missing and what
that blocks, what runs on a schedule and whether it is on, and the exact command to fix each
gap. Run it whenever anything feels wrong; it is also the first thing support asks for.

It exits 0 — it is a report. `--strict` exits 1 when a required item is missing, for scripts.
It reasons from the box's own checkers (gtm_golive_check, preflight_written, launch_check),
run as subprocesses, so it can never disagree with them.
"""
import os
import re
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")          # no network, no keys needed to LOOK
sys.path.insert(0, str(ROOT))

GLYPH_OK, GLYPH_NO, GLYPH_WARN = "  ✓", "  ✗", "  ·"
_missing_required = []


def head(t):
    print(f"\n{t}\n{'─' * len(t)}")


def line(ok, label, fix="", required=True):
    g = GLYPH_OK if ok else (GLYPH_NO if required else GLYPH_WARN)
    print(f"{g} {label}" + (f"\n        fix: {fix}" if (fix and not ok) else ""))
    if not ok and required:
        _missing_required.append(label)


def env_value(k):
    v = os.environ.get(k)
    if v is None and (ROOT / ".env").exists():
        for l in (ROOT / ".env").read_text().splitlines():
            if l.startswith(k + "="):
                v = l.split("=", 1)[1].strip()
    return v or ""


def serves_dash() -> bool:
    """Does THIS box actually answer on /dash?

    Not "is the dash code present" — `core/dash` is a shell library and ships in every box
    since #755, but a Lead box mounts only the public unsubscribe page, so a $99 Lead buyer
    has no login page at all. Measured on a fresh export 2026-09-05: the doctor told that
    buyer about a dashboard login THREE times on their first screen, for a page they cannot
    open. When Phase 3 mounts a host dash the lines come back on their own.

    STATIC, and FAIL LOUD. The first version imported the app and asked its url_map, with a
    bare except — so on any box where Flask was not importable the answer silently became
    "no dash" and a security warning about a password crossing the wire just disappeared.
    A dropped warning is the worse error, so anything this cannot read counts as YES: read
    the modules the box will actually mount (config `web_modules`) and look for a /dash route.
    """
    try:
        from core.config import get_config
        mods = (get_config() or {}).get("web_modules") or []
    except Exception:                       # cannot read the config at all
        return True                         # → warn; never hide it on uncertainty
    if not mods:
        return False                        # nothing mounted: there is no web surface here
    for dotted in mods:
        f = ROOT / (str(dotted).replace(".", "/") + ".py")
        if not f.exists():
            return True                     # a module we cannot read might serve it
        try:
            src = f.read_text()
        except OSError:
            return True
        if '"/dash' in src or "'/dash" in src:
            return True
    return False

def main(argv) -> int:
    strict = "--strict" in argv
    has_lead = (ROOT / "marketing/lead_machine/handler.py").exists()
    has_content = (ROOT / "marketing/content_machine/written").exists()
    product = "AIOS" if (has_lead and has_content) else ("the Lead Machine" if has_lead else "the Content Machine")

    head(f"THIS BOX — {product}")
    for f, why in (("CLAUDE.md", "the rules"), ("DEVSTATE.md", "the wall"), ("core/brain.py", "the shared brain"),
                   ("my/settings.yaml", "your overlay"), ("licence.json", "your licence"), (".env", "your keys")):
        line((ROOT / f).exists(), f"{f:<20} {why}",
             fix={"licence.json": "bash scripts/install.sh", ".env": "cp .env.example .env", "my/settings.yaml": "bash scripts/install.sh"}.get(f, ""),
             required=f in (".env", "licence.json"))
    ver = (ROOT / "core/FOUNDATION_VERSION")
    print(f"    foundation version: {ver.read_text().strip() if ver.exists() else 'pre-1 (no core/FOUNDATION_VERSION yet)'}")

    head("KEYS")
    line(bool(env_value("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY — the brain thinks on this", "console.anthropic.com → API keys, then put it in .env")
    # An empty bearer refuses BOTH front doors — /dispatch AND the dashboard login — and used
    # to do it with nothing on screen saying why, on every fresh box. install.sh mints one now;
    # this line is what catches a box installed before it did, or an .env edited since.
    _dash = serves_dash()
    # THE DEMO ZONE, if this box has one: *.<zone> answers, certificates on demand, and /tls/ask is
    # the gate. Static and honest — no network: the gate is asked for a label no pack claims (must
    # say no) and for the first industry a pack does claim (must say yes).
    try:
        from core import dash as _dash_mod, packs as _packs
        _zone = _dash_mod.demo_zone()
        if _zone:
            _claimed = next((str(m.get("industry", "")).lower() for m in _packs.discover() if m.get("industry")), "")
            _no = not _dash_mod.ask_tls(f"probe-no-such-industry.{_zone}")
            _yes = bool(_claimed) and _dash_mod.ask_tls(f"{_claimed}.{_zone}")
            print(f"  {'✓' if (_no and (_yes or not _claimed)) else '✗'} demo zone *.{_zone} — /tls/ask refuses an unknown label"
                  + (f" and admits {_claimed}.{_zone}" if _claimed else " (no pack claims an industry yet)")
                  + "; needs the one wildcard DNS record → this box (owner, once)")
    except Exception as _e:  # noqa: BLE001
        print(f"  ✗ demo zone check could not run: {str(_e)[:80]}")
    line(bool(env_value("DISPATCH_BEARER_TOKEN")),
         "DISPATCH_BEARER_TOKEN — /dispatch" + (" and the dashboard login" if _dash else ""),
         "bash scripts/install.sh (generates one)")
    if _dash and env_value("DASH_TOKEN"):
        print("    dashboard login uses DASH_TOKEN — the bearer is refused there (correct once anyone but you logs in)")
    elif _dash:
        # "FINE FOR ONE OPERATOR" STOPS BEING TRUE THE MOMENT THERE IS A SECOND PERSON, and
        # since per-person login there can be. Now that the box can answer "how many people
        # sign in here", this is a measured FAILURE on a box with employees rather than a note
        # the reader has to apply to themselves.
        try:
            from core import state as _st
            _people = _st.count_active_users()
        except Exception:  # noqa: BLE001 — a box too old to have the table has no employees
            _people = 0
        if _people:
            print(f"    ✗ dashboard login accepts the dispatch bearer AND this box has {_people} "
                  "person(s) who can sign in — that password is the key to the whole box. "
                  "Set DASH_TOKEN in .env and restart; new people cannot be added until you do")
        else:
            print("    dashboard login accepts the dispatch bearer — fine for one operator; set DASH_TOKEN in .env before you show it to anyone else")
    # WARN ABOUT A LOGIN PAGE ONLY IF THIS BOX SERVES ONE. #758 generalised this from
    # "content boxes" to "every box" because core/dash now ships everywhere — but shipping the
    # SHELL is not serving a PAGE: a Lead box registers only the public unsubscribe route, so
    # its buyer was warned three times about a dashboard they cannot open. serves_dash() asks
    # the app's url_map instead, so the lines appear the moment Phase 3 mounts a host dash and
    # never before. On a box that does serve one this is the second line about
    # DASHBOARD_BASE_URL and that is correct — the one above blocks sends, this one is about a
    # password crossing the wire. Different failures, both true.
    if _dash and not env_value("DASHBOARD_BASE_URL").startswith("https://"):
        # The session cookie is set secure=request.is_secure, so on a bare droplet with no
        # domain the login POST and the cookie both travel in the clear, Secure flag off.
        line(False, "the dashboard has no https base — login and session cookie go in the clear",
             "put it behind TLS (deploy/Caddyfile does this) and set DASHBOARD_BASE_URL=https://…", required=False)
    line(bool(env_value("UNSUB_SIGNING_KEY")), "UNSUB_SIGNING_KEY — opt-out links are signed with it", "bash scripts/install.sh (generates one)", required=has_lead)
    if has_lead:
        line(bool(env_value("GTM_SENDER_NAME")), "GTM_SENDER_NAME — every send refuses without it", "set it in .env")
        line(env_value("DASHBOARD_BASE_URL").startswith("https://"), "DASHBOARD_BASE_URL — https, or no send (unsubscribe links)", "DASHBOARD_BASE_URL=https://your-box.example.com in .env")
        line(bool(env_value("AIOS_PHYSICAL_ADDRESS")), "AIOS_PHYSICAL_ADDRESS — the footer the law requires", "set it in .env")
    if has_content:
        line(bool(env_value("ZERNIO_API_KEY")), "ZERNIO_API_KEY — publishing", "zernio.com → API key, then .env", required=False)
        brief = ROOT / "my" / "brand-brief-default.md"
        line(brief.exists() and "REPLACE WITH YOUR BRAND" not in brief.read_text(), "my/brand-brief-default.md — your voice (the Written lane refuses without it)", "edit my/brand-brief-default.md")
        # faster-whisper is an OPTIONAL extra and its absence degrades silently: reels
        # still render, captioned on proportional timing instead of anchored to the audio
        # (right words, looser sync), and nothing outside the log ever said so. A warning,
        # not a blocker — a box without it works, it just captions less precisely.
        #
        # WHY THIS PROBES THE PACKAGE INSTEAD OF CALLING THE RENDERER'S OWN
        # `captions.whisper_available()`, which is the same question: the exporter drops any
        # script that names a lane its box does not carry — an indented, guarded import
        # counts, deliberately ("safety over cleverness"). Importing the reel lane here
        # deleted scripts/doctor.py from the whole LEAD box, where this branch never even
        # runs. So the doctor asks the one fact both answers reduce to, and
        # test_caption_timing_visible asserts the two agree. `find_spec` differs from the
        # renderer's real import only for an installed-but-broken package; the renderer,
        # which is about to use it, is the one that must import for real.
        try:
            import importlib.util
            line(importlib.util.find_spec("faster_whisper") is not None,
                 "faster-whisper — anchored caption timing (without it: proportional)",
                 'pip install -e ".[render]"   (or bash scripts/provision_render.sh)',
                 required=False)
        except Exception as _e:  # noqa: BLE001 — a doctor that crashes is no doctor
            line(False, f"could not check faster-whisper: {str(_e)[:60]}", required=False)
        # THE CDN BASE EVERY STAMPED URL INHERITS. object_store.public_url() reads this at
        # call time, so it is baked into every finished video and every re-hosted article
        # image AT THE MOMENT OF UPLOAD — and then it lives in the CMS forever. That makes a
        # stale value invisible in the worst way: nothing fails, nothing warns, and the box
        # quietly publishes a retired domain until somebody reads a URL by eye.
        #
        # Live cost of exactly that (2026-09-09): the box kept stamping a retired media host
        # after the domain moved, and guide images ended up split across THREE hosts before
        # anyone noticed. The doctor never showed the value, so there was nothing to notice.
        #
        # SHOWN, never judged against a name: the right host is the buyer's own, so a doctor
        # that asserted ours would be wrong on every clone. The one real failure is EMPTY
        # while the bucket is configured — public_url() then returns a ROOT-RELATIVE path
        # ("/articles/x.png"), which is a broken link the moment anything but the box renders it.
        _store_on = all(env_value(k) for k in ("OBJECT_STORE_BUCKET", "OBJECT_STORE_ENDPOINT",
                                               "OBJECT_STORE_KEY"))
        _base = env_value("OBJECT_STORE_PUBLIC_URL")
        if _store_on or _base:
            line(bool(_base),
                 "OBJECT_STORE_PUBLIC_URL — stamped into every video and re-hosted image"
                 + (f": {_base}" if _base else " (EMPTY: urls become root-relative and break off-box)"),
                 "OBJECT_STORE_PUBLIC_URL=https://<your-media-host> in .env", required=False)
            print("        it is read at UPLOAD time and kept forever — change it here and old"
                  " urls keep the old host until they are re-pointed")

    head("WHAT RUNS, AND WHETHER IT IS ON")
    try:
        from core import schedule
        for row in schedule.render(schedule.snapshot()):    # ONE registry query — the GUI reads the same
            print(row)                                       # (`line` is the doctor's own helper)
        print("    timers ship OFF. Turning one on is a deliberate act — config/aios.config.yaml, or my/settings.yaml.")
    except Exception as e:  # a doctor that crashes is no doctor
        print(f"    could not load the schedule: {e}")

    head("THE MACHINE'S OWN CHECKS")
    vpy = str(ROOT / (".venv/bin/python" if (ROOT / ".venv/bin/python").exists() else ".venv/Scripts/python.exe"))
    if not pathlib.Path(vpy).exists():
        vpy = sys.executable
    checks = []
    if has_lead: checks.append(("scripts/gtm_golive_check.py", "the Lead Machine"))
    if has_content: checks.append(("scripts/preflight_written.py", "the Written lane"))
    checks.append(("scripts/launch_check.py", "every lane"))
    for script, what in checks:
        if not (ROOT / script).exists():
            continue
        r = subprocess.run([vpy, str(ROOT / script)], capture_output=True, text=True, cwd=ROOT, env={**os.environ, "AIOS_HERMETIC_TEST": "1"})
        out = (r.stdout + r.stderr)
        bad = [l.strip() for l in out.splitlines() if re.search(r"✗|FAIL|missing|refus", l) and not re.search(r"✓|PASS\b|VERDICT|══", l)]
        print(f"  {script} ({what}): {'clean' if r.returncode == 0 else f'{len(bad)} item(s)'}")
        for l in bad[:8]:
            print(f"        {l[:110]}")
        if len(bad) > 8: print(f"        … and {len(bad) - 8} more — run it directly for the full list")

    head("DATABASE")
    from core.config import settings
    db = pathlib.Path(getattr(settings, "db_path", ROOT / "aios.db"))
    if db.exists():
        import sqlite3
        c = sqlite3.connect(db); v = c.execute("pragma user_version").fetchone()[0]; n = c.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]; c.close()
        print(f"    {db.name}: {db.stat().st_size // 1024} KB, schema v{v}, {n} tables")
    else:
        line(False, f"{db} does not exist", ".venv/bin/python -c \"from core import state; state.init_db()\"")

    head("VERDICT")
    if _missing_required:
        print(f"  {len(_missing_required)} required item(s) missing — each has its fix above. Nothing sends or posts until they are set; that is by design.")
    else:
        print("  configured. Start it: .venv/bin/python -m core.worker   (or on a server: systemctl start aios-worker)")
    return 1 if (strict and _missing_required) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
