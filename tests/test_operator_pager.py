"""Only the production box may page the owner's phone.

THE DAY THIS EXISTS FOR (2026-08-06): the owner had been DM'd
`claude_code FAILED: CLAUDE_CODE_OAUTH_TOKEN not set in /opt/aios/.env` dozens of times a
day for two months — referencing a path that does not exist off the box — while the box's
own `claude_code` alert row sat OK, last fired 2026-07-25. The box was never the sender.

Alert dedup lives in SQLite, so it is PER-DATABASE. Any other copy of this code starts with
a fresh db, reads `prev == OK` every pass, and fires a brand-new failure edge forever.

Run: python tests/test_operator_pager.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")

from core import slack  # noqa: E402

_fails = []
SENT = []


def ok(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        _fails.append(label)


def _fake_post(channel, text, **kw):
    SENT.append((channel, text))
    return True


def _with_env(**kv):
    """Set/clear env for one check; returns a restore callable."""
    old = {k: os.environ.get(k) for k in kv}

    def restore():
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return restore


def test_a_dev_sandbox_never_reaches_the_phone():
    """The whole point. This checkout is NOT /opt/aios, so it must stay silent."""
    SENT.clear()
    slack._post = _fake_post
    r = _with_env(AIOS_OPERATOR_ALERTS=None, AIOS_HERMETIC_TEST=None,
                  GITHUB_ACTIONS=None, CI=None)
    try:
        allowed, why = slack._may_page_operator()
        ok("an off-box checkout is not allowed to page", not allowed)
        ok("and it says why, so this is diagnosable", "production checkout" in why)
        sent = slack.send_dm("U123", "would have paged")
        ok("send_dm makes NO Slack call off-box", SENT == [])
        ok("and reports success — nothing needed sending, not a failure", sent is True)
    finally:
        r()


def test_a_hermetic_test_never_reaches_the_phone():
    SENT.clear()
    slack._post = _fake_post
    r = _with_env(AIOS_HERMETIC_TEST="1", AIOS_OPERATOR_ALERTS=None)
    try:
        allowed, why = slack._may_page_operator()
        ok("a hermetic test cannot page the operator", not allowed and why == "hermetic test")
        slack.send_dm("U123", "test run")
        ok("no Slack call from a test run", SENT == [])
    finally:
        r()


def test_ci_never_reaches_the_phone():
    SENT.clear()
    slack._post = _fake_post
    r = _with_env(AIOS_HERMETIC_TEST=None, GITHUB_ACTIONS="true", AIOS_OPERATOR_ALERTS=None)
    try:
        allowed, why = slack._may_page_operator()
        ok("a CI runner cannot page the operator", not allowed and why == "CI runner")
        slack.send_dm("U123", "ci run")
        ok("no Slack call from CI", SENT == [])
    finally:
        r()


def test_the_override_still_works_for_a_deliberate_off_box_test():
    SENT.clear()
    slack._post = _fake_post
    r = _with_env(AIOS_OPERATOR_ALERTS="1", AIOS_HERMETIC_TEST="1", CI="true")
    try:
        allowed, _ = slack._may_page_operator()
        ok("AIOS_OPERATOR_ALERTS=1 force-enables, beating every suppressor", allowed)
        slack.send_dm("U123", "deliberate")
        ok("and the send actually happens", len(SENT) == 1)
    finally:
        r()


def test_production_is_the_DEFAULT_so_a_real_outage_is_never_silenced():
    """FAIL-SAFE, not fail-closed. If the guard cannot tell where it is, it PAGES.
    A missed marker must never cost a real outage its alert."""
    SENT.clear()
    slack._post = _fake_post
    r = _with_env(AIOS_HERMETIC_TEST=None, GITHUB_ACTIONS=None, CI=None,
                  AIOS_OPERATOR_ALERTS=None)
    real_root = None
    try:
        import core.config as cfg
        real_root = cfg.ROOT
        cfg.ROOT = "/opt/aios"                       # pretend we ARE the box
        allowed, _ = slack._may_page_operator()
        ok("the production checkout DOES page", allowed)
        slack.send_dm("U123", "real outage")
        ok("and the real page goes out", len(SENT) == 1)
    finally:
        if real_root is not None:
            import core.config as cfg2
            cfg2.ROOT = real_root
        r()


if __name__ == "__main__":
    test_a_dev_sandbox_never_reaches_the_phone()
    test_a_hermetic_test_never_reaches_the_phone()
    test_ci_never_reaches_the_phone()
    test_the_override_still_works_for_a_deliberate_off_box_test()
    test_production_is_the_DEFAULT_so_a_real_outage_is_never_silenced()
    print(f"\n{'ALL OPERATOR-PAGER TESTS PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
    raise SystemExit(1 if _fails else 0)
