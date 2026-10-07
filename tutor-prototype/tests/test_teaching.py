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
