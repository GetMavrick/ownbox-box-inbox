"""The 8 AM ideas drop — systemd timer entry point (aios-morning.timer).

Enqueues one scrape sweep per day; the worker scrapes the watched creators,
scores + rewrites the winners, and posts the morning's draft scripts (titles +
no-login review links) into the reel channel. Idempotent by date: a timer retry,
a manual run and a box reboot replay (Persistent=true) all collapse onto the
same job.

Channel comes from REEL_SLACK_CHANNEL_ID in /opt/aios/.env; without it the sweep
still runs (drafts land on the dashboard) but nothing posts to Slack.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import settings  # noqa: E402
from core.queue import queue  # noqa: E402


def main() -> None:
    channel = (settings.reel_slack_channel_id or "").strip() or None
    job, created = queue.enqueue(
        idempotency_key=f"scrape:morning:{date.today().isoformat()}",
        intent="reel", raw_text="reel scrape now",
        agent_name="morning-timer", slack_channel_id=channel)
    print(f"morning scrape {'enqueued' if created else 'already queued today'}: "
          f"{job['id']}" + ("" if channel else "  (no REEL_SLACK_CHANNEL_ID — "
                            "drafts will appear on the dashboard only)"))


if __name__ == "__main__":
    main()
