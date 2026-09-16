"""A fixture for tests/test_suite_integrity.py's export-driving guard. It is not a test.

It RUNS the exporter the way a real driver does, so the guard stripping comments and docstrings can be
proven to still see a real driver. It is never executed — the guard only reads it.
"""
import subprocess


def build(out):
    return subprocess.run(["bash", "scripts/export_box.sh", "customer_voice", out], check=True)
