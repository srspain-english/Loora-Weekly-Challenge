#!/usr/bin/env python3
"""Tests for the Progress screen: every number must come from saved classes."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store  # noqa: E402
import tutor  # noqa: E402
import web  # noqa: E402
from helpers import ServerTestCase  # noqa: E402

TODAY = date(2026, 10, 15)


def add_class(student_id, call_id, start, end, turns=5, status="ended", what="We talked."):
    conn = store.connect()
    conn.execute(
        "INSERT INTO calls (call_id, student_id, mode, level, system, transcript, turns,"
        " status, started_at, updated_at, ended_at, report)"
        " VALUES (?, ?, 'free', 'B1', '', '[]', ?, ?, ?, ?, ?, ?)",
        (call_id, student_id, turns, status, start, end, end,
         json.dumps({"what_we_did": what}) if status == "ended" else None),
    )
    conn.commit()


class ProgressNumbersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sid = store.create_student("Numbers")

    def test_a_new_student_has_nothing_yet(self) -> None:
        p = store.progress_for(self.sid, today=TODAY)
        self.assertEqual((p["classes_total"], p["minutes_total"], p["streak_days"]), (0, 0, 0))
        self.assertEqual(len(p["week"]), 7)

    def test_minutes_days_and_streak_come_from_saved_classes(self) -> None:
        add_class(self.sid, f"{self.sid}-a", "2026-10-15T09:00:00+00:00", "2026-10-15T09:10:00+00:00")
        add_class(self.sid, f"{self.sid}-b", "2026-10-14T09:00:00+00:00", "2026-10-14T09:20:00+00:00")
        add_class(self.sid, f"{self.sid}-c", "2026-10-12T09:00:00+00:00", "2026-10-12T09:05:00+00:00")
        p = store.progress_for(self.sid, today=TODAY)
        self.assertEqual(p["classes_total"], 3)
        self.assertEqual(p["minutes_total"], 35)
        self.assertEqual(p["minutes_week"], 35)
        self.assertEqual(p["streak_days"], 2)      # the 15th and 14th, not the 12th
        self.assertEqual(p["week"][-1]["minutes"], 10)
        self.assertEqual(p["recent"][0]["date"], "2026-10-15")

    def test_a_streak_survives_until_the_day_after_the_last_class(self) -> None:
        add_class(self.sid, f"{self.sid}-a", "2026-10-14T09:00:00+00:00", "2026-10-14T09:10:00+00:00")
        self.assertEqual(store.progress_for(self.sid, today=TODAY)["streak_days"], 1)

    def test_unfinished_and_dropped_classes_do_not_count(self) -> None:
        add_class(self.sid, f"{self.sid}-a", "2026-10-15T09:00:00+00:00", "2026-10-15T09:10:00+00:00",
                  status="abandoned")
        self.assertEqual(store.progress_for(self.sid, today=TODAY)["classes_total"], 0)

    def test_a_class_left_open_for_hours_is_capped(self) -> None:
        add_class(self.sid, f"{self.sid}-a", "2026-10-15T01:00:00+00:00", "2026-10-15T09:00:00+00:00")
        self.assertEqual(store.progress_for(self.sid, today=TODAY)["minutes_total"],
                         store.MAX_MINUTES_PER_CLASS)

    def test_a_class_counts_for_the_students_own_day(self) -> None:
        # 03:00 UTC on the 15th is still the afternoon of the 14th in Honolulu
        # (UTC-10), where the browser reports getTimezoneOffset() = +600.
        add_class(self.sid, f"{self.sid}-a", "2026-10-15T03:00:00+00:00", "2026-10-15T03:10:00+00:00")
        p = store.progress_for(self.sid, tz_offset_minutes=600, today=date(2026, 10, 14))
        self.assertEqual(p["week"][-1]["date"], "2026-10-14")
        self.assertEqual(p["week"][-1]["minutes"], 10)
        self.assertEqual(p["recent"][0]["date"], "2026-10-14")
        # And for a student on UTC it is the 15th.
        p = store.progress_for(self.sid, tz_offset_minutes=0, today=TODAY)
        self.assertEqual(p["recent"][0]["date"], "2026-10-15")


class ProgressEndpointTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self._memory = tutor.MEMORY_STORE
        tutor.MEMORY_STORE = store if store.using_postgres() else None
        self.auth()

    def tearDown(self) -> None:
        tutor.MEMORY_STORE = self._memory

    def test_needs_a_signed_in_browser(self) -> None:
        stranger = self.new_browser()
        web.ACCESS_PASSPHRASE = "secret"
        try:
            status, _ = self.post("/api/progress", {}, stranger)
        finally:
            web.ACCESS_PASSPHRASE = ""
        self.assertEqual(status, 401)

    def test_returns_the_students_numbers_and_memory(self) -> None:
        sid = store.create_student("Sandra", access_code="SANDRA-7777")
        memory = tutor.load_student(sid)
        memory["vocab_acquired_log"] = ["get up to speed — learn the job", "run it by me — repeat it"]
        memory["recurring_error_patterns"] = ["since/for with durations"]
        memory["next_recommendation"] = "Interrupt politely."
        tutor.save_student(memory)
        add_class(sid, f"{sid}-a", "2026-10-15T09:00:00+00:00", "2026-10-15T09:10:00+00:00")

        status, data = self.post("/api/progress", {"access_code": "sandra-7777", "tz_offset": -120})
        self.assertEqual(status, 200)
        self.assertEqual(data["classes_total"], 1)
        self.assertEqual(data["words_learned"], 2)
        self.assertEqual(data["recent_words"], ["run it by me", "get up to speed"])
        self.assertEqual(data["working_on"], ["since/for with durations"])
        self.assertEqual(data["next_recommendation"], "Interrupt politely.")

    def test_students_only_see_their_own_numbers(self) -> None:
        a = store.create_student("Ana", access_code="ANA-1000")
        store.create_student("Bea", access_code="BEA-2000")
        add_class(a, f"{a}-a", "2026-10-15T09:00:00+00:00", "2026-10-15T09:10:00+00:00")
        _, data = self.post("/api/progress", {"access_code": "BEA-2000"})
        self.assertEqual(data["classes_total"], 0)

    def test_a_garbage_timezone_does_not_break_it(self) -> None:
        status, _ = self.post("/api/progress", {"tz_offset": "banana"})
        self.assertEqual(status, 200)

    def test_page_has_a_progress_screen(self) -> None:
        html = web.INDEX_HTML
        for piece in ('id="progress-btn"', 'id="progress-screen"', "/api/progress",
                      "function renderProgress(", "getTimezoneOffset()"):
            self.assertIn(piece, html)


if __name__ == "__main__":
    unittest.main()
