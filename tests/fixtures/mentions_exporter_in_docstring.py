"""A fixture for tests/test_suite_integrity.py's export-driving guard. It is not a test.

Its only reference to the exporter is in this docstring: scripts/export_box.sh builds a per-box-type
image, and a suite that explains that must not be told to join the skip set. On 2026-09-16 two suites
were, over exactly this, and obeying would have removed each from every box it has to run in.
"""


def explain():
    """And once more in a function docstring: export_box.sh is only described here, never run."""
    return 1
