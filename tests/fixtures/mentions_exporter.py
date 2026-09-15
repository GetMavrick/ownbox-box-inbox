"""A fixture for tests/test_suite_integrity.py's export-driving guard. It is not a test.

This file's ONLY reference to the exporter is the comment below. The guard must NOT count it
as a driver: a suite is a driver when it RUNS the script, not when it explains it. Before
2026-09-15 the guard was a substring match and a comment like this one failed CI.
"""
# scripts/export_box.sh leaves some scripts out of a box that has no lane for them.
VALUE = 1
