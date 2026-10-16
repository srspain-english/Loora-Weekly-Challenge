#!/usr/bin/env python3
"""Tests for personal codes, the teacher page, and memory continuity.

Reported by a student: coming back days later, Juno greeted her as new.
Memory is saved when a class is reported on, so a class that was only
closed (never ended) lost what it taught. These cover the fixes: personal
codes that work on any device, unfinished classes folded into memory, and
a "last time" summary on the way in.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store  # noqa: E402
import tutor  # noqa: E402
import web  # noqa: E402
from helpers import ServerTestCase, fake_reply, fake_report_response  # noqa: E402


def create(**kw):
    return fake_report_response() if kw.get("tools") else fake_reply("Hello. And you?")


class CodesTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self._admin = web.ADMIN_CODE
        web.ADMIN_CODE = "teacher-secret"
        self._memory = tutor.MEMORY_STORE
        tutor.MEMORY_STORE = store if store.using_postgres() else None
        self.auth()

    def tearDown(self) -> None:
        web.ADMIN_CODE = self._admin
        tutor.MEMORY_STORE = self._memory

    def admin(self, **body):
        return self.post("/api/admin", {"admin_code": "teacher-secret", **body})

    def test_teacher_page_is_off_unless_a_code_is_set(self) -> None:
        web.ADMIN_CODE = ""
        status, _ = self.post("/api/admin", {"admin_code": "", "action": "list"})
        self.assertEqual(status, 404)

    def test_wrong_teacher_code_is_refused(self) -> None:
        status, _ = self.post("/api/admin", {"admin_code": "nope", "action": "list"})
        self.assertEqual(status, 401)

    def test_creates_a_readable_code_for_a_new_student(self) -> None:
        status, data = self.admin(action="create", name="Sandra")
        self.assertEqual(status, 200)
        self.assertRegex(data["access_code"], r"^SANDRA-\d{4}$")
        _, listing = self.admin(action="list")
        self.assertIn("Sandra", [s["display_name"] for s in listing["students"]])

    def test_gives_a_code_to_an_existing_student_keeping_their_memory(self) -> None:
        sid = store.create_student("Pilar")
        memory = tutor.load_student(sid)
        memory["next_recommendation"] = "Past simple."
        tutor.save_student(memory)

        status, data = self.admin(action="assign", student_id=sid)
        self.assertEqual(status, 200)
        found = store.student_by_access_code(data["access_code"])
        self.assertEqual(found["student_id"], sid)
        self.assertEqual(tutor.load_student(sid)["next_recommendation"], "Past simple.")

    def test_a_code_works_in_any_letter_case_on_any_device(self) -> None:
        _, data = self.admin(action="create", name="Sandra")
        sid = store.student_by_access_code(data["access_code"])["student_id"]
        memory = tutor.load_student(sid)
        memory["session_history"] = [{"date": "2026-10-01", "mode": "free",
                                      "what_we_did": "Talked about the new job.",
                                      "next_recommendation": "Review the past simple."}]
        tutor.save_student(memory)

        phone = self.new_browser()          # a different device
        self.auth(phone)
        status, resumed = self.post("/api/resume",
                                    {"access_code": data["access_code"].lower()}, phone)
        self.assertEqual(status, 200)
        self.assertEqual(resumed["last_session"]["next_recommendation"],
                         "Review the past simple.")

    def test_an_abandoned_class_is_folded_into_memory_on_the_next_start(self) -> None:
        sid = store.create_student("Sandra", access_code="SANDRA-1111")
        with mock.patch.object(web.client.messages, "create", side_effect=create):
            _, first = self.post("/api/start", {"mode": "free", "access_code": "SANDRA-1111"})
            self.post("/api/message", {"text": "I am working here since two weeks."})
            self.post("/api/message", {"text": "Everybody is very nice with me."})
            # The student closes the tab without pressing End, and comes back.
            web.SESSIONS.clear()
            back = self.new_browser()
            self.auth(back)
            _, second = self.post("/api/start",
                                  {"mode": "free", "access_code": "SANDRA-1111"}, back)

        self.assertEqual(store.get_call(first["call_id"])["status"], "ended")
        history = tutor.load_student(sid)["session_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["next_recommendation"], "Keep going.")
        self.assertNotEqual(second["call_id"], first["call_id"])

    def test_an_easier_english_request_is_kept_in_that_classes_prompt(self) -> None:
        store.create_student("Diego", access_code="DIEGO-3333")
        with mock.patch.object(web.client.messages, "create", side_effect=create):
            _, plain = self.post("/api/start", {"mode": "free", "access_code": "DIEGO-3333"})
            _, easy = self.post("/api/start", {"mode": "free", "access_code": "DIEGO-3333", "easier": True})
        self.assertNotIn("EASIER ENGLISH", store.get_call(plain["call_id"])["system"])
        self.assertIn("EASIER ENGLISH", store.get_call(easy["call_id"])["system"])

    def test_a_class_with_nothing_in_it_is_just_dropped(self) -> None:
        sid = store.create_student("Pilar", access_code="PILAR-2222")
        with mock.patch.object(web.client.messages, "create", side_effect=create):
            _, first = self.post("/api/start", {"mode": "free", "access_code": "PILAR-2222"})
            web.SESSIONS.clear()
            back = self.new_browser()
            self.auth(back)
            self.post("/api/start", {"mode": "free", "access_code": "PILAR-2222"}, back)
        self.assertEqual(store.get_call(first["call_id"])["status"], "abandoned")
        self.assertEqual(tutor.load_student(sid).get("session_history"), [])


class OpeningRuleTest(unittest.TestCase):
    def test_juno_opens_by_saying_what_was_worked_on_last_time(self) -> None:
        for level in tutor.LEVEL_RULES:
            self.assertIn("Last time we worked", tutor.stable_prefix(level))


if __name__ == "__main__":
    unittest.main()
