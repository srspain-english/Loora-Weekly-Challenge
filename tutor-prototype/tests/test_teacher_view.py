#!/usr/bin/env python3
"""Tests for what the teacher sees: last connection, unfinished classes, and
each student's progress; and for closing classes a student never ended."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store  # noqa: E402
import tutor  # noqa: E402
import web  # noqa: E402
from helpers import ServerTestCase, fake_report_response  # noqa: E402


def ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def open_class(student_id, call_id, idle_hours, turns=4):
    transcript = [{"role": "user", "content": "(connected)"}, {"role": "assistant", "content": "Hi."},
                  {"role": "user", "content": "I am working here since two weeks."},
                  {"role": "assistant", "content": "Since when?"}]
    conn = store.connect()
    conn.execute(
        "INSERT INTO calls (call_id, student_id, mode, level, system, transcript, turns,"
        " status, started_at, updated_at) VALUES (?, ?, 'free', 'B1', '[]', ?, ?, 'open', ?, ?)",
        (call_id, student_id, json.dumps(transcript), turns, ago(idle_hours + 0.3), ago(idle_hours)))
    conn.commit()


class TeacherViewTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        self._admin = web.ADMIN_CODE
        web.ADMIN_CODE = "teacher-secret"
        self._memory = tutor.MEMORY_STORE
        tutor.MEMORY_STORE = store if store.using_postgres() else None
        conn = store.connect()
        conn.execute("DELETE FROM calls")
        conn.execute("DELETE FROM students")
        conn.commit()

    def tearDown(self) -> None:
        web.ADMIN_CODE = self._admin
        tutor.MEMORY_STORE = self._memory

    def admin(self, **body):
        return self.post("/api/admin", {"admin_code": "teacher-secret", "tz_offset": 0, **body})

    def card(self, name):
        _, data = self.admin(action="list")
        return next(s for s in data["students"] if s["display_name"] == name)

    def test_the_last_connection_is_shown_even_without_a_finished_class(self) -> None:
        sid = store.create_student("Ines", access_code="INES-1111")
        open_class(sid, f"{sid}-a", idle_hours=1)
        ines = self.card("Ines")
        self.assertEqual(ines["last_seen_days"], 0)
        self.assertEqual(ines["classes"], 0)
        self.assertEqual(ines["unfinished_classes"], 1)

    def test_the_teacher_can_open_a_students_progress(self) -> None:
        sid = store.create_student("Ines", access_code="INES-1111")
        memory = tutor.load_student(sid)
        memory["recurring_error_patterns"] = ["since/for"]
        tutor.save_student(memory)
        status, data = self.admin(action="progress", student_id=sid)
        self.assertEqual(status, 200)
        self.assertEqual(data["name"], "Ines")
        self.assertEqual(data["working_on"], ["since/for"])
        self.assertIn("level", data)
        self.assertEqual(self.admin(action="progress", student_id="nope")[0], 404)

    def test_a_class_left_open_is_closed_with_its_notes_by_the_hourly_check(self) -> None:
        sid = store.create_student("Ines", access_code="INES-1111")
        open_class(sid, f"{sid}-old", idle_hours=5)
        open_class(sid, f"{sid}-empty", idle_hours=5, turns=1)
        open_class(sid, f"{sid}-now", idle_hours=0.5)          # still going: left alone
        with mock.patch.object(web.client.messages, "create", return_value=fake_report_response()):
            status, data = self.admin(action="run_reminders")
        self.assertEqual(status, 200)
        self.assertEqual(data["classes_closed"], {"reported": 1, "dropped": 1, "failed": 0})
        self.assertEqual(store.get_call(f"{sid}-old")["status"], "ended")
        self.assertEqual(store.get_call(f"{sid}-empty")["status"], "abandoned")
        self.assertEqual(store.get_call(f"{sid}-now")["status"], "open")
        self.assertEqual(self.card("Ines")["classes"], 1)
        self.assertEqual(tutor.load_student(sid)["next_recommendation"], "Keep going.")

    def test_a_failed_report_does_not_stop_the_others(self) -> None:
        a = store.create_student("Ana", access_code="ANA-1111")
        b = store.create_student("Bea", access_code="BEA-1111")
        open_class(a, f"{a}-x", idle_hours=6)
        open_class(b, f"{b}-y", idle_hours=4)
        replies = [RuntimeError("model busy"), fake_report_response()]
        with mock.patch.object(web.client.messages, "create", side_effect=replies):
            result = web.close_stale_classes()
        self.assertEqual(result, {"reported": 1, "dropped": 0, "failed": 1})
        self.assertEqual(store.get_call(f"{a}-x")["status"], "open")      # tried again next hour


if __name__ == "__main__":
    unittest.main()
