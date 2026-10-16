#!/usr/bin/env python3
"""Tests for how Juno hands the turn back to the student.

Reported by a student: after a correction Juno stopped talking, and he
couldn't tell whether to repeat the sentence, answer something, or wait.
Juno now never asks for a repeat, and every reply ends with a question or
prompt for the student.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tutor  # noqa: E402


class TurnTakingTest(unittest.TestCase):
    def test_every_reply_hands_the_turn_back(self) -> None:
        for level in tutor.LEVEL_RULES:
            prompt = tutor.stable_prefix(level)
            self.assertIn("TURN-TAKING", prompt)
            self.assertIn("End every reply with one clear question or prompt", prompt)
            self.assertIn("never end on the correction", prompt)

    def test_corrections_never_ask_for_a_repeat(self) -> None:
        for level in tutor.LEVEL_RULES:
            prompt = tutor.stable_prefix(level)
            self.assertNotIn("quick repeat", prompt)
            self.assertNotIn("repeat something faster", prompt)
            self.assertIn("Never ask the student to repeat", prompt)


if __name__ == "__main__":
    unittest.main()


class EasierEnglishTest(unittest.TestCase):
    """A student who finds Juno's words too hard can ask for simpler ones."""

    def test_the_request_reaches_the_prompt_only_when_made(self) -> None:
        student = {"student_id": "x", "name": "X", "cefr_level": "B1"}
        plain = tutor.dynamic_context("B1", "free", None, student)
        easier = tutor.dynamic_context("B1", "free", None, student, easier=True)
        self.assertNotIn("EASIER ENGLISH", plain)
        self.assertIn("EASIER ENGLISH", easier)
        self.assertIn("no idioms or phrasal verbs", easier)

    def test_it_never_touches_the_cached_half_of_the_prompt(self) -> None:
        student = {"student_id": "x", "name": "X", "cefr_level": "B1"}
        a = tutor.build_system_blocks("B1", "free", None, student)
        b = tutor.build_system_blocks("B1", "free", None, student, easier=True)
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])
