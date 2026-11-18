#!/usr/bin/env python3
"""Tests for the level and skill estimates on the Progress screen."""

from __future__ import annotations

import json
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


def rating(fluency="B1", grammar="B1", vocabulary="B1", understanding="B1", enough=True):
    return {"skill_estimates": {"enough_evidence": enough, "fluency": fluency, "grammar": grammar,
                                "vocabulary": vocabulary, "understanding": understanding}}


class SkillSummaryTest(unittest.TestCase):
    def test_nothing_is_claimed_from_one_class(self) -> None:
        summary = store.skill_summary([rating()])
        self.assertFalse(summary["ready"])
        self.assertEqual(summary["rated_classes"], 1)

    def test_classes_without_enough_evidence_do_not_count(self) -> None:
        summary = store.skill_summary([rating(enough=False), rating(enough=False), rating()])
        self.assertEqual(summary["rated_classes"], 1)

    def test_old_reports_without_ratings_are_ignored(self) -> None:
        summary = store.skill_summary([{"what_we_did": "Old class."}, rating(), rating()])
        self.assertTrue(summary["ready"])
        self.assertEqual(summary["rated_classes"], 2)

    def test_a_malformed_rating_is_ignored_not_crashed_on(self) -> None:
        bad = {"skill_estimates": {"enough_evidence": True, "fluency": "Z9"}}
        self.assertEqual(store.skill_summary([bad, bad, rating()])["rated_classes"], 1)

    def test_a_band_counts_as_its_middle(self) -> None:
        summary = store.skill_summary([rating(), rating()])
        overall = summary["overall"]
        self.assertEqual((overall["band"], overall["next_band"]), ("B1", "B2"))
        self.assertEqual(overall["fraction"], 0.5)
        self.assertTrue(overall["plus"])

    def test_it_averages_so_a_student_moves_between_bands_gradually(self) -> None:
        mixed = rating("B2", "B1", "B1", "B2")
        summary = store.skill_summary([rating(), mixed])
        self.assertEqual(summary["overall"]["band"], "B1")
        self.assertEqual(summary["overall"]["fraction"], 0.75)
        by_key = {s["key"]: s for s in summary["skills"]}
        # Fluency was B1 then B2: the mean sits exactly on the B1/B2 boundary.
        self.assertEqual(by_key["fluency"]["band"], "B2")
        self.assertEqual(by_key["fluency"]["fraction"], 0.0)

    def test_only_the_latest_classes_count(self) -> None:
        history = [rating("A2", "A2", "A2", "A2")] * 3 + [rating("C1", "C1", "C1", "C1")] * store.RATED_WINDOW
        self.assertEqual(store.skill_summary(history)["overall"]["band"], "C1")

    def test_the_top_of_the_scale_has_no_next_band(self) -> None:
        overall = store.skill_summary([rating("C2", "C2", "C2", "C2")] * 2)["overall"]
        self.assertEqual((overall["band"], overall["next_band"]), ("C2", None))


class LevelEndpointsTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self._memory = tutor.MEMORY_STORE
        tutor.MEMORY_STORE = store if store.using_postgres() else None
        self.auth()

    def tearDown(self) -> None:
        tutor.MEMORY_STORE = self._memory

    def add(self, sid, n, report):
        conn = store.connect()
        conn.execute(
            "INSERT INTO calls (call_id, student_id, mode, level, system, transcript, turns,"
            " status, started_at, updated_at, ended_at, report)"
            " VALUES (?, ?, 'free', 'B1', '', '[]', 9, 'ended', ?, ?, ?, ?)",
            (f"{sid}-{n}", sid, f"2026-10-1{n}T09:00:00+00:00", f"2026-10-1{n}T09:10:00+00:00",
             f"2026-10-1{n}T09:10:00+00:00", json.dumps(report)))
        conn.commit()

    def test_progress_and_resume_report_the_level_once_it_is_known(self) -> None:
        sid = store.create_student("Leveled", access_code="LEVEL-1111")
        self.add(sid, 1, {"what_we_did": "One.", **rating()})
        _, early = self.post("/api/progress", {"access_code": "LEVEL-1111"})
        self.assertFalse(early["level"]["ready"])
        _, resumed = self.post("/api/resume", {"access_code": "LEVEL-1111"})
        self.assertIsNone(resumed["level"])

        self.add(sid, 2, {"what_we_did": "Two.", **rating()})
        _, later = self.post("/api/progress", {"access_code": "LEVEL-1111"})
        self.assertTrue(later["level"]["ready"])
        self.assertEqual(later["level"]["overall"]["band"], "B1")
        _, resumed = self.post("/api/resume", {"access_code": "LEVEL-1111"})
        self.assertEqual(resumed["level"], "B1+")

    def test_the_report_asks_for_ratings_and_ending_a_class_keeps_them(self) -> None:
        props = tutor.REPORT_TOOL["input_schema"]["properties"]["skill_estimates"]["properties"]
        self.assertEqual(set(props) - {"enough_evidence"}, {"fluency", "grammar", "vocabulary", "understanding"})
        self.assertIn("skill_estimates", tutor.REPORT_TOOL["input_schema"]["required"])
        self.assertNotIn("pronunciation", props)   # text only: nothing honest to rate

        def create(**kw):
            return fake_report_response() if kw.get("tools") else fake_reply("Hello. And you?")
        with mock.patch.object(web.client.messages, "create", side_effect=create):
            _, started = self.post("/api/start", {"mode": "free", "student": "Keeper"})
            self.post("/api/message", {"text": "Hello there."})
            self.post("/api/end", {"call_id": started["call_id"]})
        saved = store.get_call(started["call_id"])["report"]
        self.assertTrue(saved["skill_estimates"]["enough_evidence"])
        _, progress = self.post("/api/progress", {})
        self.assertEqual(progress["level"]["rated_classes"], 1)

    def test_page_draws_the_level_and_skills(self) -> None:
        for piece in ("function renderLevel(", "function levelPhrase(", "skill-bar", "level-wait", "levelEstimate"):
            self.assertIn(piece, web.INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
