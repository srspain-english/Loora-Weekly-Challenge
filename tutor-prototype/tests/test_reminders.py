#!/usr/bin/env python3
"""Tests for the teacher's reminder helper: what is suggested, to whom, and when."""

from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reminders  # noqa: E402
import store  # noqa: E402
import web  # noqa: E402
from helpers import ServerTestCase  # noqa: E402

WEDNESDAY, SATURDAY = "2026-10-14", "2026-10-17"


def facts(**kw):
    base = dict(display_name="Sandra", access_code="SANDRA-4821", classes=4, streak_days=0,
                days_since_last=1, reminded_ago_days=None, paused=False, today=WEDNESDAY)
    base.update(kw)
    return base


class WhatToSayTest(unittest.TestCase):
    def kind(self, **kw):
        return reminders.suggest(facts(**kw), "https://juno.example")["kind"]

    def test_nothing_is_sent_to_someone_who_practised_today(self) -> None:
        self.assertEqual(self.kind(days_since_last=0), "done")

    def test_at_most_one_reminder_a_day(self) -> None:
        self.assertEqual(self.kind(reminded_ago_days=0), "wait")

    def test_paused_students_are_left_alone(self) -> None:
        self.assertEqual(self.kind(paused=True, days_since_last=9), "paused")

    def test_a_student_who_never_started_is_welcomed_with_their_code(self) -> None:
        r = reminders.suggest(facts(classes=0, days_since_last=None), "https://juno.example")
        self.assertEqual(r["kind"], "welcome")
        self.assertIn("SANDRA-4821", r["message"])
        self.assertIn("https://juno.example", r["message"])

    def test_a_streak_at_risk_names_the_streak_and_offers_a_way_out(self) -> None:
        r = reminders.suggest(facts(streak_days=5), "")
        self.assertEqual(r["kind"], "streak")
        self.assertIn("5 días seguidos", r["message"])
        self.assertIn("dime", r["message"])

    def test_reminders_get_further_apart_the_longer_someone_is_away(self) -> None:
        self.assertEqual(self.kind(days_since_last=2), "nudge")
        self.assertEqual(self.kind(days_since_last=3), "back_3")
        self.assertEqual(self.kind(days_since_last=4, reminded_ago_days=1), "wait")
        self.assertEqual(self.kind(days_since_last=7), "back_7")
        self.assertEqual(self.kind(days_since_last=9, reminded_ago_days=2), "wait")
        self.assertEqual(self.kind(days_since_last=14), "closing")

    def test_the_last_message_closes_the_door_and_is_not_repeated(self) -> None:
        closing = reminders.suggest(facts(days_since_last=20), "https://juno.example")
        self.assertIn("el último por ahora", closing["message"])
        self.assertEqual(self.kind(days_since_last=25, reminded_ago_days=6), "quiet")

    def test_weekends_are_marked_optional(self) -> None:
        self.assertFalse(reminders.suggest(facts(), "")["soft"])
        self.assertTrue(reminders.suggest(facts(today=SATURDAY), "")["soft"])

    def test_every_message_is_sober_and_in_the_teachers_voice(self) -> None:
        for days in (None, 1, 2, 3, 7, 14):
            for streak in (0, 4):
                r = reminders.suggest(facts(days_since_last=days, streak_days=streak,
                                            classes=0 if days is None else 4), "https://juno.example")
                text = r["message"]
                if not text:
                    continue
                self.assertNotIn("!", text)
                self.assertNotIn("¡", text)
                self.assertNotIn("—", text)
                self.assertFalse(re.search("[\U0001F300-\U0001FAFF☀-➿]", text), text)
                self.assertNotIn("\n-", text)
                self.assertTrue(text.startswith("Hola Sandra"))
                self.assertLess(len(text), 330)


class PhoneNumbersTest(unittest.TestCase):
    def test_numbers_are_tidied_into_one_format(self) -> None:
        self.assertEqual(store.normalise_phone("+34 600-000-000"), "+34600000000")
        self.assertEqual(store.normalise_phone("0034 600 000 000"), "+34600000000")
        self.assertEqual(store.normalise_phone("  "), "")

    def test_a_number_without_a_country_code_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            store.normalise_phone("600 000 000")

    def test_nonsense_is_refused(self) -> None:
        for bad in ("+12", "+" + "1" * 20):
            with self.assertRaises(ValueError):
                store.normalise_phone(bad)


@unittest.skipIf(store.using_postgres(), "the old-database upgrade is checked on SQLite")
class UpgradeTest(unittest.TestCase):
    def test_a_database_made_before_reminders_gains_the_new_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(str(Path(tmp) / "old.db"))
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE students (student_id TEXT PRIMARY KEY, display_name TEXT NOT NULL)")
            conn.execute("INSERT INTO students VALUES ('s1', 'Old Student')")
            store._ensure_columns(conn)
            row = conn.execute("SELECT * FROM students").fetchone()
            self.assertEqual((row["phone"], row["last_reminded_at"], row["reminders_paused"]),
                             (None, None, 0))
            store._ensure_columns(conn)   # and running it twice is harmless


class TeacherPageActionsTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        self._admin = web.ADMIN_CODE
        web.ADMIN_CODE = "teacher-secret"

    def tearDown(self) -> None:
        web.ADMIN_CODE = self._admin

    def admin(self, **body):
        return self.post("/api/admin", {"admin_code": "teacher-secret", **body})

    def mine(self, name):
        _, data = self.admin(action="list", tz_offset=0)
        return next(s for s in data["students"] if s["display_name"] == name)

    def test_create_with_a_number_and_it_shows_up_with_a_suggestion(self) -> None:
        status, _ = self.admin(action="create", name="Marta", phone="+34 611 222 333")
        self.assertEqual(status, 200)
        marta = self.mine("Marta")
        self.assertEqual(marta["phone"], "+34611222333")
        self.assertEqual(marta["reminder"]["kind"], "welcome")
        self.assertIn(marta["access_code"], marta["reminder"]["message"])

    def test_a_bad_number_is_refused_and_nothing_is_created(self) -> None:
        status, data = self.admin(action="create", name="Nadie", phone="600 000 000")
        self.assertEqual(status, 400)
        self.assertIn("country code", data["error"])
        _, listing = self.admin(action="list", tz_offset=0)
        self.assertNotIn("Nadie", [s["display_name"] for s in listing["students"]])

    def test_saving_and_clearing_a_number(self) -> None:
        sid = store.create_student("Lucas", access_code="LUCAS-1234")
        self.assertEqual(self.admin(action="set_phone", student_id=sid, phone="+57 300 111 2222")[1]["phone"],
                         "+573001112222")
        self.assertEqual(self.mine("Lucas")["phone"], "+573001112222")
        self.admin(action="set_phone", student_id=sid, phone="")
        self.assertIsNone(self.mine("Lucas")["phone"])
        self.assertEqual(self.admin(action="set_phone", student_id=sid, phone="123")[0], 400)

    def test_sending_a_reminder_stops_a_second_one_the_same_day(self) -> None:
        sid = store.create_student("Irene", access_code="IRENE-5555")
        self.assertEqual(self.mine("Irene")["reminder"]["kind"], "welcome")
        self.admin(action="mark_reminded", student_id=sid)
        self.assertEqual(self.mine("Irene")["reminder"]["kind"], "wait")

    def test_pausing_stops_suggestions(self) -> None:
        sid = store.create_student("Paula", access_code="PAULA-6666")
        self.admin(action="set_paused", student_id=sid, paused=True)
        self.assertEqual(self.mine("Paula")["reminder"]["kind"], "paused")
        self.admin(action="set_paused", student_id=sid, paused=False)
        self.assertEqual(self.mine("Paula")["reminder"]["kind"], "welcome")

    def test_students_to_message_come_first(self) -> None:
        a = store.create_student("Zeta", access_code="ZETA-7777")
        store.create_student("Alfa", access_code="ALFA-8888")
        self.admin(action="set_paused", student_id=a, paused=True)
        _, data = self.admin(action="list", tz_offset=0)
        kinds = [s["reminder"]["kind"] for s in data["students"]]
        self.assertEqual(kinds, sorted(kinds, key=lambda k: k not in reminders.KINDS_THAT_SEND))

    def test_visitors_who_never_gave_a_name_are_not_listed(self) -> None:
        store.create_student("")        # the placeholder a nameless visitor leaves behind
        _, data = self.admin(action="list", tz_offset=0)
        self.assertNotIn("Student", [s["display_name"] for s in data["students"]])

    def test_the_link_in_reminders_points_at_this_site(self) -> None:
        _, data = self.admin(action="list", tz_offset=0)
        self.assertTrue(data["site_url"].startswith("http://127.0.0.1"))

    def test_an_unknown_student_is_a_404(self) -> None:
        self.assertEqual(self.admin(action="mark_reminded", student_id="nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
