"""AIOS kernel (`core`): shared infrastructure for every department.

Nothing here is business logic — it is the foundation modules plug into:
brain (reasoning), cost_guard (spend ceiling), state (SQLite), queue, dispatch
(the one ingress), slack (notifier), watchdog (health).
"""
