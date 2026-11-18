#!/usr/bin/env python3
"""The daily run: email every student who asked for reminders and is due one.

A free outside timer calls this about once an hour. For each student who
confirmed their email, in THEIR OWN local time:

  - it only sends within three hours after the hour they chose, so a late or
    missed timer still catches them that day;
  - it applies the same rules as the teacher's reminder helper (none if they
    practised today or were already reminded today, spaced out the longer
    they are away, a last kind message, a per-student pause);
  - it never sends at weekends: those stay for the teacher to decide by hand.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import emailer
import reminders
import store

CATCH_UP_HOURS = 2


def links(site_url: str, token: str) -> tuple[str, str]:
    base = site_url.rstrip("/")
    return f"{base}/r/confirm?t={token}", f"{base}/r/stop?t={token}"


def run(site_url: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    summary = {"checked": 0, "sent": 0, "skipped": {}}

    def skip(reason: str) -> None:
        summary["skipped"][reason] = summary["skipped"].get(reason, 0) + 1

    for row in store.students_with_email_on():
        summary["checked"] += 1
        tz = row["tz_offset"] or 0
        local = now - timedelta(minutes=tz)
        hour = row["remind_hour"]
        if hour is None or not hour <= local.hour <= min(23, hour + CATCH_UP_HOURS):
            skip("not their hour")
            continue
        if local.weekday() >= 5:
            skip("weekend")
            continue
        facts = store._facts_for_row(row, tz, today=local.date())
        suggestion = reminders.suggest(facts, site_url)
        if suggestion["kind"] not in reminders.KINDS_THAT_SEND:
            skip(suggestion["kind"])
            continue
        token = store.get_student(row["student_id"])["reminder_token"]
        _, stop_url = links(site_url, token)
        subject, text = emailer.reminder(suggestion["kind"], suggestion["message"], site_url, stop_url)
        try:
            emailer.send(row["email"], subject, text, unsubscribe_url=stop_url)
        except (emailer.SendFailed, emailer.NotConfigured) as e:
            print(f"[juno] reminder email not sent: {e}", file=sys.stderr, flush=True)
            skip("send failed")
            continue
        store.mark_reminded(row["student_id"], at=now)
        summary["sent"] += 1
    return summary
