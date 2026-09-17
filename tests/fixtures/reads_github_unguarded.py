"""A fixture for test_suite_integrity's .github guard. It is not a test, and never runs.

The shape that crashed eight shipped suites inside buyer boxes on 2026-09-16: a bare read of the CI
workflow, with nothing asking first whether this is the repository at all.
"""
import pathlib


def test_listed_in_ci():
    wf = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
    return "test_listed_in_ci" in wf.read_text()
