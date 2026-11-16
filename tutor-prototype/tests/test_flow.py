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
        with mock.patch.object(web.client.responses, "create", return_value=fake_reply("Hi!")) as create:
            self.post("/api/start", {"mode": "free"})
            self.post("/api/message", {"text": "Hello."})
        for call in create.call_args_list:
            self.assertEqual(call.kwargs["model"], tutor.TURN_MODEL)
            self.assertEqual(call.kwargs["reasoning"], {"effort": "none"})
        self.assertNotEqual(tutor.TURN_MODEL, tutor.MODEL)

    def test_a_refusal_is_not_retried_on_another_model(self):
        declined = fake_reply("")
        declined.stop_reason = "refusal"
        with mock.patch.object(web.client.responses, "create", side_effect=[fake_reply("Hi!"), declined]) as create:
            self.post("/api/start", {"mode": "free"})
            _, data = self.post("/api/message", {"text": "Hello."})
        self.assertEqual(data["type"], "error")
        self.assertEqual(create.call_count, 2)

    def test_the_report_keeps_the_full_model(self) -> None:
        self.assertIn("model=MODEL", Path(tutor.__file__).read_text().split("def generate_report")[1].split("def print_report")[0])


class TimeUpTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self.auth()

    def test_the_last_turn_closes_and_only_that_turn(self) -> None:
        with mock.patch.object(web.client.responses, "create", return_value=fake_reply("Bye!")) as create:
            _, started = self.post("/api/start", {"mode": "free"})
            _, normal = self.post("/api/message", {"text": "One."})
            _, last = self.post("/api/message", {"text": "Two.", "wrap_up": True})
        systems = [json.dumps(c.kwargs["instructions"]) for c in create.call_args_list]
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


class NoThinkingStepTest(ServerTestCase, unittest.TestCase):
    """The "Juno is thinking..." pause: the conversation answers directly."""

    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self.auth()

    def talk(self, side_effect=None):
        import contextlib
        import io
        log = io.StringIO()
        with mock.patch.object(web.client.responses, "create",
                               side_effect=side_effect or (lambda **kw: fake_reply("Hi!"))) as create, \
                contextlib.redirect_stderr(log):
            self.post("/api/start", {"mode": "free"})
            self.post("/api/message", {"text": "Hello."})
        return create, log.getvalue()

    def test_conversation_turns_skip_the_thinking_step(self) -> None:
        create, _ = self.talk()
        for call in create.call_args_list:
            self.assertEqual(call.kwargs["reasoning"], {"effort": "none"})

    def test_it_can_be_switched_back_without_changing_code(self) -> None:
        with mock.patch.object(tutor, "TURN_EFFORT", "low"):
            create, _ = self.talk()
        for call in create.call_args_list:
            self.assertEqual(call.kwargs["reasoning"], {"effort": "low"})

    def test_default_models_are_configurable_and_openai_only(self):
        self.assertEqual(tutor.TURN_MODEL, "gpt-6-luna")
        self.assertEqual(tutor.MODEL, "gpt-6.1-terra")

    def test_each_answer_logs_where_the_seconds_went_and_nothing_said(self) -> None:
        _, log = self.talk()
        line = next(l for l in log.splitlines() if "turn timing:" in l)
        self.assertRegex(line, r"database \d+\.\d\d s · AI \d+\.\d\d s · total \d+\.\d\d s")
        self.assertIn("thinking off", line)
        self.assertNotIn("Hello", log)
        self.assertIn("opening timing: AI", log)

    def test_reasoning_is_explicit_in_every_conversation_request(self):
        create, _ = self.talk()
        for call in create.call_args_list:
            self.assertEqual(call.kwargs["reasoning"]["effort"], tutor.TURN_EFFORT)


class SmarterWaitTest(unittest.TestCase):
    """About 4 of the 7 seconds a student measured were Juno waiting on purpose."""

    def test_a_finished_sentence_goes_sooner_on_normal(self) -> None:
        html = web.INDEX_HTML
        self.assertIn("const QUICK_MS = 1500;", html)
        self.assertIn("if (setting === 'normal' && lastFinal && soundsFinished(heard)) return QUICK_MS;", html)
        self.assertIn("lastFinal = !!(latest && latest.isFinal);", html)

    def test_a_sentence_left_hanging_still_gets_the_full_wait(self) -> None:
        html = web.INDEX_HTML
        for word in ("'and'", "'because'", "'that'", "'think'", "'um'"):
            self.assertIn(word, html.split("const UNFINISHED")[1].split(");")[0])

    def test_students_who_need_time_keep_their_choices(self) -> None:
        html = web.INDEX_HTML
        self.assertIn('<option value="normal">Normal</option>', html)
        self.assertIn("long: 8000, tap: Infinity", html)


class LibraryVersionTest(unittest.TestCase):
    def test_the_openai_library_is_pinned(self) -> None:
        reqs = (Path(web.__file__).parent / "requirements.txt").read_text()
        self.assertIn("openai==3.51.0", reqs)
        self.assertNotIn("anthropic", reqs)


class StructuredHasNoLimitTest(ServerTestCase, unittest.TestCase):
    """The teacher's decision: Structured class runs to the end; Free talk and
    Business English keep the 6-minute limit."""

    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self.auth()

    def test_the_page_never_times_a_structured_class(self) -> None:
        html = web.INDEX_HTML
        self.assertIn("classLimitMs = mode === 'structured' ? 0 :", html)
        self.assertIn("$('length-row').hidden = $('mode').value === 'structured';", html)

    def test_the_server_ignores_a_wrap_up_in_a_structured_class(self) -> None:
        with mock.patch.object(web.client.responses, "create", return_value=fake_reply("Next part.")) as create:
            self.post("/api/start", {"mode": "structured"})
            _, data = self.post("/api/message", {"text": "Done.", "wrap_up": True})
        self.assertFalse(data["wrapped"])
        self.assertNotIn("TIME IS UP", json.dumps(create.call_args_list[-1].kwargs["instructions"]))

    def test_free_and_business_still_close_on_time(self) -> None:
        with mock.patch.object(web.client.responses, "create", return_value=fake_reply("Bye!")):
            self.post("/api/start", {"mode": "business"})
            _, data = self.post("/api/message", {"text": "Done.", "wrap_up": True})
        self.assertTrue(data["wrapped"])
