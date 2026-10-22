#!/usr/bin/env python3
"""Tests for the email reminders students ask for. No real email is ever sent:
the one network call (emailer.deliver) is replaced throughout."""

from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import autoreminders  # noqa: E402
import emailer  # noqa: E402
import store  # noqa: E402
import tutor  # noqa: E402
import web  # noqa: E402
from helpers import ServerTestCase  # noqa: E402

ENV = {"RESEND_API_KEY": "test-key", "JUNO_EMAIL_FROM": "Juno <juno@example.com>",
       "JUNO_EMAIL_REPLY_TO": "teacher@example.com", "JUNO_CRON_SECRET": "cron-secret"}
WEDNESDAY_1830_MADRID = datetime(2026, 10, 14, 16, 30, tzinfo=timezone.utc)   # 18:30 in Madrid
SITE = "https://juno.example"


def add_class(student_id, n, started):
    conn = store.connect()
    conn.execute(
        "INSERT INTO calls (call_id, student_id, mode, level, system, transcript, turns,"
        " status, started_at, updated_at, ended_at, report)"
        " VALUES (?, ?, 'free', 'B1', '', '[]', 9, 'ended', ?, ?, ?, ?)",
        (f"{student_id}-{n}", student_id, started, started, started, json.dumps({"what_we_did": "x"})))
    conn.commit()


def confirmed_student(name, hour=18, tz=-120, email=None):
    sid = store.create_student(name, access_code=f"{name.upper()}-1111")
    store.save_reminder_settings(sid, email or f"{name.lower()}@example.com", hour, tz)
    store.set_email_state(sid, "on")
    return sid


class EmailTestCase:
    def setUp(self) -> None:
        self._env = mock.patch.dict(os.environ, ENV)
        self._env.start()
        self.sent = []
        self._deliver = mock.patch.object(emailer, "deliver", side_effect=self.sent.append)
        self._deliver.start()
        web.REMINDER_CONFIRM_SENT.clear()
        conn = store.connect()                      # every test starts with no students
        conn.execute("DELETE FROM calls")
        conn.execute("DELETE FROM students")
        conn.commit()

    def tearDown(self) -> None:
        self._deliver.stop()
        self._env.stop()


class DailyRunTest(EmailTestCase, ServerTestCase, unittest.TestCase):
    def run_at(self, now):
        return autoreminders.run(SITE, now=now)

    def test_a_student_who_has_gone_quiet_gets_one_email_at_their_hour(self) -> None:
        sid = confirmed_student("Alba")
        add_class(sid, 0, "2026-10-11T09:00:00+00:00")        # three days ago
        summary = self.run_at(WEDNESDAY_1830_MADRID)
        self.assertEqual(summary["sent"], 1)
        mail = self.sent[0]
        self.assertEqual(mail["to"], ["alba@example.com"])
        self.assertEqual(mail["from"], "Juno <juno@example.com>")
        self.assertEqual(mail["reply_to"], "teacher@example.com")
        self.assertIn("hace 3 días", mail["text"])
        self.assertIn("/r/stop?t=", mail["text"])
        self.assertIn("List-Unsubscribe", mail["headers"])
        self.assertEqual(mail["headers"]["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")

    def test_never_twice_in_a_day_even_if_the_timer_fires_again(self) -> None:
        sid = confirmed_student("Alba")
        add_class(sid, 0, "2026-10-11T09:00:00+00:00")
        self.run_at(WEDNESDAY_1830_MADRID)
        again = self.run_at(WEDNESDAY_1830_MADRID.replace(minute=45))
        self.assertEqual((len(self.sent), again["sent"]), (1, 0))
        self.assertEqual(again["skipped"], {"wait": 1})

    def test_it_uses_the_students_own_clock(self) -> None:
        sid = confirmed_student("Alba", hour=18, tz=-120)
        add_class(sid, 0, "2026-10-11T09:00:00+00:00")
        before = WEDNESDAY_1830_MADRID.replace(hour=14)       # 16:30 in Madrid
        self.assertEqual(self.run_at(before)["skipped"], {"not their hour": 1})
        # the same moment is 09:30 for a student in Bogota (UTC-5, offset +300)
        bogota = confirmed_student("Carla", hour=9, tz=300)
        add_class(bogota, 0, "2026-10-11T09:00:00+00:00")
        self.assertEqual(self.run_at(before)["sent"], 1)
        self.assertEqual(self.sent[0]["to"], ["carla@example.com"])

    def test_a_late_timer_still_catches_them_that_day_but_not_all_night(self) -> None:
        sid = confirmed_student("Alba", hour=18)
        add_class(sid, 0, "2026-10-11T09:00:00+00:00")
        too_late = WEDNESDAY_1830_MADRID.replace(hour=19, minute=30)   # 21:30 in Madrid
        self.assertEqual(self.run_at(too_late)["sent"], 0)
        just_in_time = WEDNESDAY_1830_MADRID.replace(hour=18, minute=30)  # 20:30 in Madrid
        self.assertEqual(self.run_at(just_in_time)["sent"], 1)

    def test_nothing_at_weekends_and_nothing_to_someone_who_practised(self) -> None:
        sid = confirmed_student("Alba")
        add_class(sid, 0, "2026-10-11T09:00:00+00:00")
        saturday = datetime(2026, 10, 17, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(self.run_at(saturday)["skipped"], {"weekend": 1})
        busy = confirmed_student("Berta")
        add_class(busy, 0, "2026-10-14T08:00:00+00:00")                # earlier today
        self.assertEqual(self.run_at(WEDNESDAY_1830_MADRID)["skipped"].get("done"), 1)

    def test_paused_pending_and_stopped_students_get_nothing(self) -> None:
        paused = confirmed_student("Alba")
        store.set_reminders_paused(paused, True)
        pending = store.create_student("Berta", access_code="BERTA-1111")
        store.save_reminder_settings(pending, "berta@example.com", 18, -120)    # never confirmed
        stopped = confirmed_student("Carla")
        store.set_email_state(stopped, "stopped")
        for sid in (paused, pending, stopped):
            add_class(sid, 0, "2026-10-11T09:00:00+00:00")
        summary = self.run_at(WEDNESDAY_1830_MADRID)
        self.assertEqual((summary["checked"], summary["sent"]), (1, 0))
        self.assertEqual(self.sent, [])

    def test_a_failed_send_is_not_counted_as_a_reminder(self) -> None:
        sid = confirmed_student("Alba")
        add_class(sid, 0, "2026-10-11T09:00:00+00:00")
        with mock.patch.object(emailer, "deliver", side_effect=emailer.SendFailed("down")):
            summary = self.run_at(WEDNESDAY_1830_MADRID)
        self.assertEqual(summary["skipped"], {"send failed": 1})
        self.assertEqual(summary["sent"], 0)
        self.assertEqual(self.run_at(WEDNESDAY_1830_MADRID)["sent"], 1)    # tried again, and it worked

    def test_someone_who_never_starts_is_emailed_twice_and_then_left_alone(self) -> None:
        confirmed_student("Alba")
        self.assertEqual(self.run_at(WEDNESDAY_1830_MADRID)["sent"], 1)            # welcome
        later = datetime(2026, 10, 22, 16, 30, tzinfo=timezone.utc)                 # eight days on
        self.assertEqual(self.run_at(later)["sent"], 1)                             # kind last message
        self.assertIn("el último por ahora", self.sent[1]["text"])
        much_later = datetime(2026, 11, 4, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(self.run_at(much_later)["sent"], 0)
        self.assertEqual(len(self.sent), 2)


class StudentSignUpTest(EmailTestCase, ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        EmailTestCase.setUp(self)
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        self._memory = tutor.MEMORY_STORE
        tutor.MEMORY_STORE = store if store.using_postgres() else None
        self.auth()
        store.create_student("Marina", access_code="MARINA-4242")

    def tearDown(self) -> None:
        tutor.MEMORY_STORE = self._memory
        EmailTestCase.tearDown(self)

    def save(self, **body):
        return self.post("/api/reminders", {"access_code": "MARINA-4242", "email": "Marina@Example.com",
                                            "hour": 18, "tz_offset": -120, **body})

    def link(self, mail, kind):
        text = mail["text"]
        start = text.index(f"/r/{kind}?t=")
        return text[start:].split()[0]

    def fetch(self, path, method="GET"):
        request = urllib.request.Request(f"{self.base_url}{path}", method=method, data=b"List-Unsubscribe=One-Click" if method == "POST" else None)
        try:
            with urllib.request.urlopen(request) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_the_card_only_appears_once_email_is_set_up(self) -> None:
        _, on = self.post("/api/resume", {"access_code": "MARINA-4242"})
        self.assertTrue(on["reminders"]["available"])
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": ""}):
            _, off = self.post("/api/resume", {"access_code": "MARINA-4242"})
            self.assertFalse(off["reminders"]["available"])
            self.assertEqual(self.save()[0], 404)

    def test_asking_sends_a_confirmation_and_nothing_else_until_it_is_pressed(self) -> None:
        status, data = self.save()
        self.assertEqual((status, data["state"], data["email"]), (200, "pending", "marina@example.com"))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["to"], ["marina@example.com"])
        self.assertIn("18:00", self.sent[0]["text"])
        self.assertEqual(autoreminders.run(SITE, now=WEDNESDAY_1830_MADRID)["checked"], 0)

        code, page = self.fetch(self.link(self.sent[0], "confirm"))
        self.assertEqual(code, 200)
        self.assertIn("Recordatorios activados", page)
        self.assertEqual(store.reminder_settings(store.student_by_access_code("MARINA-4242")["student_id"])["state"], "on")

    def test_the_unsubscribe_link_and_the_one_click_post_both_work(self) -> None:
        self.save()
        self.fetch(self.link(self.sent[0], "confirm"))
        sid = store.student_by_access_code("MARINA-4242")["student_id"]
        stop = self.link(self.sent[0], "stop")
        self.assertEqual(self.fetch(stop, "POST")[0], 200)
        self.assertEqual(store.reminder_settings(sid)["state"], "stopped")
        self.fetch(self.link(self.sent[0], "confirm"))
        self.assertEqual(self.fetch(stop)[0], 200)
        self.assertEqual(store.reminder_settings(sid)["state"], "stopped")

    def test_a_made_up_link_does_nothing(self) -> None:
        self.assertEqual(self.fetch("/r/confirm?t=" + "x" * 30)[0], 404)
        self.assertEqual(self.fetch("/r/stop?t=short")[0], 404)
        self.assertEqual(self.fetch("/r/confirm")[0], 404)

    def test_bad_input_is_refused_before_anything_is_sent(self) -> None:
        self.assertEqual(self.save(email="not-an-email")[0], 400)
        self.assertEqual(self.save(hour=31)[0], 400)
        self.assertEqual(self.save(hour="banana")[0], 400)
        self.assertEqual(self.sent, [])

    def test_the_form_cannot_be_used_to_flood_an_inbox(self) -> None:
        self.assertEqual(self.save()[0], 200)
        self.assertEqual(self.save()[0], 429)
        self.assertEqual(len(self.sent), 1)

    def test_changing_only_the_hour_keeps_reminders_on(self) -> None:
        self.save()
        self.fetch(self.link(self.sent[0], "confirm"))
        web.REMINDER_CONFIRM_SENT.clear()
        _, data = self.save(hour=20)
        self.assertEqual((data["state"], data["hour"]), ("on", 20))
        self.assertEqual(len(self.sent), 1)

    def test_a_new_address_has_to_be_confirmed_again(self) -> None:
        self.save()
        self.fetch(self.link(self.sent[0], "confirm"))
        web.REMINDER_CONFIRM_SENT.clear()
        _, data = self.save(email="other@example.com")
        self.assertEqual(data["state"], "pending")
        self.assertEqual(self.sent[-1]["to"], ["other@example.com"])

    def test_the_student_can_stop_from_the_page(self) -> None:
        self.save()
        _, data = self.post("/api/reminders", {"access_code": "MARINA-4242", "action": "stop"})
        self.assertEqual(data["state"], "stopped")

    def test_a_service_outage_is_a_plain_message_and_nothing_is_left_half_done(self) -> None:
        with mock.patch.object(emailer, "deliver", side_effect=emailer.SendFailed("down")):
            status, data = self.save()
        self.assertEqual(status, 503)
        self.assertIn("try again later", data["error"])

    def test_the_email_escapes_what_students_type(self) -> None:
        store.create_student("<b>Hax</b>", access_code="HAX-1111")
        self.post("/api/reminders", {"access_code": "HAX-1111", "email": "hax@example.com", "hour": 18})
        self.assertNotIn("<b>Hax</b>", self.sent[-1]["html"])
        self.assertIn("&lt;b&gt;Hax", self.sent[-1]["html"])


class TimerEndpointTest(EmailTestCase, ServerTestCase, unittest.TestCase):
    def call(self, secret=None):
        request = urllib.request.Request(f"{self.base_url}/api/cron/reminders", method="POST", data=b"{}",
                                         headers={"Content-Type": "application/json", **({"X-Cron-Secret": secret} if secret is not None else {})})
        try:
            with urllib.request.urlopen(request) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_it_needs_the_secret(self) -> None:
        self.assertEqual(self.call()[0], 401)
        self.assertEqual(self.call("wrong")[0], 401)
        status, data = self.call("cron-secret")
        self.assertEqual(status, 200)
        self.assertEqual(set(data), {"classes_closed", "checked", "sent", "skipped"})

    def test_it_does_not_exist_without_a_secret(self) -> None:
        with mock.patch.dict(os.environ, {"JUNO_CRON_SECRET": ""}):
            self.assertEqual(self.call("anything")[0], 404)

    def test_without_email_it_still_tidies_classes_but_sends_nothing(self) -> None:
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": ""}):
            status, data = self.call("cron-secret")
        self.assertEqual(status, 200)
        self.assertEqual(set(data), {"classes_closed"})
        self.assertEqual(self.sent, [])


class SenderTest(unittest.TestCase):
    def test_it_is_off_until_both_settings_exist(self) -> None:
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": "", "JUNO_EMAIL_FROM": ""}):
            self.assertFalse(emailer.configured())
            with self.assertRaises(emailer.NotConfigured):
                emailer.send("a@example.com", "x", "y")
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": "k", "JUNO_EMAIL_FROM": "Juno <j@example.com>"}):
            self.assertTrue(emailer.configured())

    def test_links_in_the_plain_text_become_clickable(self) -> None:
        self.assertIn('<a href="https://juno.example/x">', emailer._html("Mira https://juno.example/x ahora"))


if __name__ == "__main__":
    unittest.main()
