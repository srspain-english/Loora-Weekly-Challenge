#!/usr/bin/env python3
"""Tests for a student's feedback on fluency: answers that wait for Send, too
many corrections in Business English, slow replies, and no natural end."""

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
from helpers import ServerTestCase, fake_reply  # noqa: E402

STUDENT = {"student_id": "x", "name": "X", "cefr_level": "B1"}


class OneCorrectionRuleTest(unittest.TestCase):
    def test_every_mode_and_level_gets_the_same_limit(self) -> None:
        for level in tutor.LEVEL_RULES:
            for mode, scenario in (("free", None), ("structured", None), ("business", tutor.pick_scenario(None))):
                prompt = tutor.build_system_prompt(level, mode, scenario, STUDENT)
                self.assertIn("WHAT TO CORRECT OUT LOUD", prompt, (level, mode))
                self.assertIn("At most one correction per reply", prompt)

    def test_a_scenario_note_cannot_turn_it_back_into_constant_correction(self) -> None:
        scenario = dict(tutor.pick_scenario(None), correction_instruction="correct my English throughout")
        context = tutor.dynamic_context("B1", "business", scenario, STUDENT)
        self.assertIn("never how often to interrupt", tutor.CORRECTION_LIMIT)
        self.assertIn("still follows WHAT TO CORRECT OUT LOUD", context)

    def test_no_level_asks_to_correct_everything(self) -> None:
        for level, rule in tutor.LEVEL_RULES.items():
            self.assertNotIn("Correct throughout", rule, level)

    def test_the_rule_sits_in_the_shared_cached_half(self) -> None:
        blocks = tutor.build_system_blocks("B1", "free", None, STUDENT)
        self.assertIn("WHAT TO CORRECT OUT LOUD", blocks[0]["text"])


class FasterRepliesTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self.auth()

    def test_the_conversation_uses_the_fast_model_at_low_effort(self) -> None:
        with mock.patch.object(web.client.messages, "create", return_value=fake_reply("Hi!")) as create:
            self.post("/api/start", {"mode": "free"})
            self.post("/api/message", {"text": "Hello."})
        for call in create.call_args_list:
            self.assertEqual(call.kwargs["model"], tutor.TURN_MODEL)
            self.assertEqual(call.kwargs["output_config"], {"effort": "low"})
        self.assertNotEqual(tutor.TURN_MODEL, tutor.MODEL)

    def test_if_the_fast_model_declines_the_full_model_answers(self) -> None:
        declined = fake_reply("")
        declined.stop_reason = "refusal"
        with mock.patch.object(web.client.messages, "create",
                               side_effect=[fake_reply("Hi!"), declined, fake_reply("Sure, go on.")]) as create:
            self.post("/api/start", {"mode": "free"})
            status, data = self.post("/api/message", {"text": "Hello."})
        self.assertEqual((status, data["reply"]), (200, "Sure, go on."))
        self.assertEqual(create.call_args_list[-1].kwargs["model"], tutor.MODEL)

    def test_the_report_keeps_the_full_model(self) -> None:
        self.assertIn("model=MODEL", Path(tutor.__file__).read_text().split("def generate_report")[1][:900])


class TimeUpTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self.auth()

    def test_the_last_turn_closes_and_only_that_turn(self) -> None:
        with mock.patch.object(web.client.messages, "create", return_value=fake_reply("Bye!")) as create:
            _, started = self.post("/api/start", {"mode": "free"})
            _, normal = self.post("/api/message", {"text": "One."})
            _, last = self.post("/api/message", {"text": "Two.", "wrap_up": True})
        systems = [json.dumps(c.kwargs["system"]) for c in create.call_args_list]
        self.assertNotIn("TIME IS UP", systems[1])
        self.assertIn("TIME IS UP", systems[2])
        self.assertEqual((normal["wrapped"], last["wrapped"]), (False, True))
        self.assertNotIn("TIME IS UP", store.get_call(started["call_id"])["system"])


class PageTest(unittest.TestCase):
    def test_spoken_answers_only_wait_when_the_student_chose_to_type(self) -> None:
        html = web.INDEX_HTML
        self.assertIn("if (!typingChosen) sendMessage();", html)
        self.assertNotIn("if ($('type-row').hidden) sendMessage();", html)
        self.assertIn("openTyping(); typingChosen = true;", html)

    def test_a_length_can_be_chosen_and_closes_the_class(self) -> None:
        html = web.INDEX_HTML
        self.assertIn('<select id="length">', html)
        self.assertIn('<option value="6" selected>6 minutes</option>', html)
        self.assertIn("wrap_up: wrapUp", html)
        self.assertIn("if (endAfterSpeaking) { endAfterGoodbye(); return; }", html)


if __name__ == "__main__":
    unittest.main()
