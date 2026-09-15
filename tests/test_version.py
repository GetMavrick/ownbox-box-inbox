"""Which commit is the box running — measured, not inferred. No network, no git binary.

THE FAILURE THIS PREVENTS is not a crash. All week "how far behind is the box?" was answered by
reading the last DEVSTATE post and reporting it as the box's state — which is a report of
somebody's last report, wrong the moment anyone deploys without posting, and indistinguishable
from a genuine "not deployed yet". A fix that was live got re-deployed; a fix that wasn't got
waved through. Both look the same from the wall and neither looks the same from `/health`.
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AIOS_HERMETIC_TEST", "1")

from core import version  # noqa: E402

_fails = []
SHA = "a" * 40
OTHER = "b" * 40


def ok(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        _fails.append(label)


def _repo(head: str, files: dict) -> str:
    """A minimal `.git` on disk. Faster than a real checkout and it exercises the parser, which
    is the part that can be wrong."""
    d = pathlib.Path(tempfile.mkdtemp()) / "r"
    (d / ".git").mkdir(parents=True)
    (d / ".git" / "HEAD").write_text(head)
    for name, body in files.items():
        p = d / ".git" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return str(d)


def test_it_reads_the_three_shapes_HEAD_actually_takes():
    ok("a loose ref resolves",
       version.head_sha(_repo("ref: refs/heads/main\n",
                              {"refs/heads/main": SHA + "\n"})) == SHA)
    ok("a detached HEAD is already the sha",
       version.head_sha(_repo(SHA + "\n", {})) == SHA)
    # `git gc` packs refs away on its own schedule, and a parser that only looks in `refs/`
    # returns None for a perfectly healthy repo the first time it runs.
    ok("a PACKED ref resolves too — gc runs unattended",
       version.head_sha(_repo("ref: refs/heads/main\n",
                              {"packed-refs": f"# pack-refs with: peeled\n{SHA} refs/heads/main\n"
                                              f"{OTHER} refs/heads/other\n"})) == SHA)
    ok("…and it picks the right ref out of several", version.head_sha(
        _repo("ref: refs/heads/other\n",
              {"packed-refs": f"{SHA} refs/heads/main\n{OTHER} refs/heads/other\n"})) == OTHER)


def test_a_checkout_with_no_git_is_unknown_not_an_exception():
    """A clone deployed from a tarball has no `.git`, which is a legitimate way to run. An
    unknown SHA is a much smaller problem than a liveness probe that 500s."""
    ok("no .git at all", version.head_sha(str(pathlib.Path(tempfile.mkdtemp()))) is None)
    ok("a HEAD naming a ref that does not exist", version.head_sha(
        _repo("ref: refs/heads/gone\n", {})) is None)
    ok("an empty HEAD", version.head_sha(_repo("", {})) is None)


def test_the_pulled_but_not_restarted_case_is_the_whole_point():
    """`git pull` moves the working tree and NOT the running process. The deploy looks done,
    the files are current, and the worker executes last week's code until something restarts it.
    That gap is invisible today and it is the one this endpoint exists to name."""
    real = version.RUNNING_SHA
    try:
        d = _repo("ref: refs/heads/main\n", {"refs/heads/main": SHA + "\n"})
        version.RUNNING_SHA = SHA
        ok("running == on disk -> no restart needed",
           version.status(d)["restart_needed"] is False)

        pathlib.Path(d, ".git", "refs", "heads", "main").write_text(OTHER + "\n")
        st = version.status(d)
        ok("after a pull, on_disk moves and commit does NOT",
           st["commit"] == SHA and st["on_disk"] == OTHER)
        ok("and THAT is what restart_needed reports", st["restart_needed"] is True)

        # An unknown SHA must not be reported as a mismatch — "I cannot tell" and "these differ"
        # are different answers and only one of them should send someone to restart a service.
        version.RUNNING_SHA = None
        ok("unknown is never a false alarm", version.status(d)["restart_needed"] is False)
    finally:
        version.RUNNING_SHA = real


def test_the_running_sha_is_frozen_at_import():
    """If this were re-read per request it would report the PULLED commit while the process
    still executed the old one — the exact lie the endpoint exists to stop telling."""
    src = pathlib.Path(version.__file__).read_text()
    ok("RUNNING_SHA is module-level, evaluated once",
       "\nRUNNING_SHA = head_sha()" in src)
    ok("and `status` reads it rather than re-resolving",
       '"commit": RUNNING_SHA' in src)


def test_it_needs_no_git_binary_and_no_network():
    """`/health` is hit by the watchdog on a timer and must never be the thing that breaks. A
    subprocess per probe is both slower and one more way to fail."""
    # Grep for USAGE, not for the word. The docstring explains why there is no subprocess here,
    # and an assertion that cannot tell an explanation from a call is one that gets weakened the
    # first time somebody writes a comment.
    src = pathlib.Path(version.__file__).read_text()
    ok("no subprocess call",
       "import subprocess" not in src and "subprocess." not in src and "os.system(" not in src)
    ok("no network call", "import requests" not in src and "urllib.request" not in src)


if __name__ == "__main__":
    print("VERSION — which commit is this box running")
    test_it_reads_the_three_shapes_HEAD_actually_takes()
    test_a_checkout_with_no_git_is_unknown_not_an_exception()
    test_the_pulled_but_not_restarted_case_is_the_whole_point()
    test_the_running_sha_is_frozen_at_import()
    test_it_needs_no_git_binary_and_no_network()
    if _fails:
        print(f"\n{len(_fails)} FAILED: " + ", ".join(_fails))
        sys.exit(1)
    print("\nALL VERSION TESTS PASS")
