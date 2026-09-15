"""Durability tests — the Litestream replication + restore cycle, proven live.

Uses a `file`-type replica (same engine, zero credentials) so the FULL cycle —
WAL streaming → off-path replica → restore → integrity + content verification —
runs in CI with no bucket. The s3 replica in deploy/litestream.yml is the same
machinery pointed at a bucket; the restore drill on the box proves that half.

Requires the `litestream` binary (LITESTREAM_BIN env, or on PATH, or
/tmp/litestream). SKIPs cleanly when absent — config/script checks still run.

Run: python tests/test_litestream.py
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp())
os.environ["AIOS_DB_PATH"] = str(_TMP / "drill.db")

from core import state  # noqa: E402


def _binary() -> str | None:
    for cand in (os.environ.get("LITESTREAM_BIN"), shutil.which("litestream"),
                 "/tmp/litestream", "/usr/local/bin/litestream"):
        if cand and Path(cand).exists():
            return cand
    return None


def test_config_and_scripts_are_sound():
    """The committed yml parses (with the env placeholders substituted) and both
    shell scripts pass bash -n. The unit's config path matches the repo file."""
    import yaml
    raw = (ROOT / "deploy" / "litestream.yml").read_text()
    for k in ("OBJECT_STORE_BUCKET", "OBJECT_STORE_ENDPOINT",
              "OBJECT_STORE_KEY", "OBJECT_STORE_SECRET"):
        assert "${" + k + "}" in raw, f"yml must reference ${{{k}}} from .env"
        raw = raw.replace("${" + k + "}", "test-" + k.lower())
    cfg = yaml.safe_load(raw)
    db = cfg["dbs"][0]
    assert db["path"] == "/opt/aios/aios.db"
    rep = db["replicas"][0]
    assert rep["type"] == "s3" and rep["retention"] == "72h"

    unit = (ROOT / "deploy" / "aios-litestream.service").read_text()
    assert "litestream replicate -config /opt/aios/deploy/litestream.yml" in unit
    assert "EnvironmentFile=/opt/aios/.env" in unit

    for s in ("install_litestream.sh", "restore_drill.sh"):
        subprocess.run(["bash", "-n", str(ROOT / "scripts" / s)], check=True)
    inst = (ROOT / "scripts" / "install_litestream.sh").read_text()
    assert 'LITESTREAM_VERSION="0.3.13"' in inst and \
        "download/v${LITESTREAM_VERSION}" in inst, \
        "binary download must be PINNED to the versioned release URL (doctrine)"
    print("PASS — config renders, unit/installer/drill sound, version pinned")


def test_full_replicate_restore_cycle():
    """The real thing: write rows → litestream streams them to a replica →
    restore to a third path → integrity ok, rows intact, schema version intact."""
    bin_ = _binary()
    if not bin_:
        print("SKIP — litestream binary not available (set LITESTREAM_BIN)")
        return

    state.init_db()
    with state.connect() as c:
        c.execute("INSERT INTO gtm_suppression (email, reason, added_at, note) "
                  "VALUES ('optout@example.com','unsubscribe',?, 'drill')",
                  (state._now(),))
    state.record_spend(task="score", model="m", cost_usd=0.01)

    db = os.environ["AIOS_DB_PATH"]
    replica = _TMP / "replica"
    cfg = _TMP / "ls.yml"
    cfg.write_text(f"dbs:\n  - path: {db}\n    replicas:\n"
                   f"      - type: file\n        path: {replica}\n"
                   f"        sync-interval: 100ms\n")

    proc = subprocess.Popen([bin_, "replicate", "-config", str(cfg)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Write MORE rows while replication is live (the WAL-streaming case).
        time.sleep(1.5)
        state.record_spend(task="rewrite", model="m", cost_usd=0.02)
        deadline = time.time() + 15
        while time.time() < deadline and not (replica / "generations").exists():
            time.sleep(0.5)
        time.sleep(2.0)   # let the last WAL segment sync (100ms interval + margin)
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    out = _TMP / "restored.db"
    subprocess.run([bin_, "restore", "-config", str(cfg), "-o", str(out), db],
                   check=True, capture_output=True)

    conn = sqlite3.connect(out)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    n_sup = conn.execute("SELECT COUNT(*) FROM gtm_suppression").fetchone()[0]
    n_spend = conn.execute("SELECT COUNT(*) FROM spend_ledger").fetchone()[0]
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    live_ver = sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert n_sup == 1, "suppression registry (legal retention) must survive restore"
    assert n_spend == 2, f"rows written DURING replication must survive, got {n_spend}"
    assert ver == live_ver, "schema version must round-trip"
    print(f"PASS — live replicate→restore cycle: integrity ok, {n_spend} ledger rows "
          f"+ suppression intact, user_version {ver} preserved")


if __name__ == "__main__":
    test_config_and_scripts_are_sound()
    test_full_replicate_restore_cycle()
    print("\nALL LITESTREAM/DURABILITY TESTS PASS")
