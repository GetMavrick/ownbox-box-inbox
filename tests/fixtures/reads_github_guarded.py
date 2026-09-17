"""A fixture for test_suite_integrity's .github guard. It is not a test, and never runs.

The same read, asked first whether this is the repository. In a buyer's box there is no .github,
so the function reports that and returns instead of crashing.
"""
import pathlib


def test_listed_in_ci():
    here = pathlib.Path(__file__).resolve().parents[1]
    if not (here / ".github").is_dir():
        return None
    return "test_listed_in_ci" in (here / ".github/workflows/tests.yml").read_text()
