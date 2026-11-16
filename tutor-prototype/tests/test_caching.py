#!/usr/bin/env python3
"""Stable prompt order, growing history and OpenAI cache configuration.

All responses are mocked. Cache savings are measured by usage, never assumed.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tutor  # noqa: E402
import web  # noqa: E402
from helpers import ServerTestCase  # noqa: E402

# Retain a conservative prompt-size regression floor. This is not a promise
# of cache eligibility; actual discounts come from reported token usage.
MIN_CACHEABLE_TOKENS = 512
CHARS_PER_TOKEN = 4  # deliberately rough; only used for a floor, never a ceiling

STUDENT = {
    "name": "Alex",
    "cefr_level": "B1",
    "grammar_covered": [],
    "recurring_errors": [],
    "sessions": [],
}


from helpers import fake_reply, fake_report_response


class CacheableSystemTest(unittest.TestCase):
    def test_only_the_shared_prefix_is_marked_cacheable(self) -> None:
        scenario = tutor.pick_scenario("on_the_ball")
        blocks = tutor.build_system_blocks("B1", "business", scenario, STUDENT)
        self.assertEqual(len(blocks), 2)
        self.assertNotIn("cache_control", blocks[0])
        self.assertNotIn("cache_control", blocks[1])

    def test_the_cached_block_carries_no_student_data(self) -> None:
        # The reason for splitting the prompt at all: nothing identifying a
        # student belongs in a block marked for caching.
        student = dict(STUDENT, name="Encarnacion Zubizarreta",
                       vocab_acquired_log=["bottleneck — a slow point"])
        prefix = tutor.stable_prefix("B1")
        self.assertNotIn("Encarnacion", prefix)
        self.assertNotIn("bottleneck", prefix)

    def test_two_students_share_the_cached_prefix_byte_for_byte(self) -> None:
        # And the payoff: one cache entry serves every student at a level,
        # instead of one entry each.
        a = tutor.build_system_blocks("B1", "free", None, dict(STUDENT, name="Ana"))
        b = tutor.build_system_blocks("B1", "free", None, dict(STUDENT, name="Bea"))
        self.assertEqual(a[0]["text"], b[0]["text"])
        self.assertNotEqual(a[1]["text"], b[1]["text"])

    def test_the_cached_prefix_alone_clears_the_minimum(self) -> None:
        # It is cached on its own now, so it has to clear 512 tokens on its
        # own too - the student's half no longer pads it out.
        for level in ("A2", "B1", "B2", "C1"):
            with self.subTest(level=level):
                self.assertGreater(
                    len(tutor.stable_prefix(level)) // CHARS_PER_TOKEN,
                    MIN_CACHEABLE_TOKENS,
                )

    def test_system_prompt_is_byte_identical_across_turns(self) -> None:
        # The silent killer: anything varying in here - a timestamp, a turn
        # counter, an unsorted dict - sits at the front of the prefix and
        # invalidates the whole cache on every single turn.
        scenario = tutor.pick_scenario("on_the_ball")
        for mode in ("free", "business", "structured"):
            with self.subTest(mode=mode):
                a = tutor.build_system_prompt("B1", mode, scenario, STUDENT)
                b = tutor.build_system_prompt("B1", mode, scenario, STUDENT)
                self.assertEqual(a, b)

    def test_system_prompt_clears_the_minimum_cacheable_prefix(self) -> None:
        # Below the minimum the API just doesn't cache, with no error. The
        # char-based estimate understates the real token count for English
        # prose, so clearing this bound is a genuine floor, not a guess.
        scenario = tutor.pick_scenario("on_the_ball")
        for mode in ("free", "business", "structured"):
            with self.subTest(mode=mode):
                prompt = tutor.build_system_prompt("B1", mode, scenario, STUDENT)
                self.assertGreater(
                    len(prompt) // CHARS_PER_TOKEN,
                    MIN_CACHEABLE_TOKENS,
                    f"{mode} prompt may be too short for the model to cache",
                )


class CallLoopSendsCacheMarkersTest(ServerTestCase, unittest.TestCase):
    """The requests the server actually builds, start and message alike."""

    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()

    def _post(self, path: str, body: dict):
        return self.post(path, body)[1]

    def _assert_cached(self, kwargs):
        self.assertIsInstance(kwargs["instructions"], str)
        self.assertNotIn("cache_control", kwargs)
        self.assertEqual(kwargs["prompt_cache_options"], {"ttl": "30m"})
        self.assertFalse(kwargs["store"])

    def test_opening_turn_is_cached(self) -> None:
        self._post("/api/auth", {"passphrase": ""})
        with mock.patch.object(
            web.client.responses, "create", return_value=fake_reply()
        ) as create:
            self._post("/api/start", {"student": "demo", "mode": "business"})
        self._assert_cached(create.call_args.kwargs)

    def test_every_later_turn_is_cached_too(self) -> None:
        self._post("/api/auth", {"passphrase": ""})
        with mock.patch.object(
            web.client.responses, "create", return_value=fake_reply()
        ) as create:
            self._post("/api/start", {"student": "demo", "mode": "business"})
            for i in range(3):
                self._post("/api/message", {"text": f"turn {i}"})
                self._assert_cached(create.call_args.kwargs)

    def test_system_prompt_is_reused_verbatim_across_turns(self) -> None:
        # Rebuilding it per turn would be the easy way to reintroduce drift.
        self._post("/api/auth", {"passphrase": ""})
        with mock.patch.object(
            web.client.responses, "create", return_value=fake_reply()
        ) as create:
            self._post("/api/start", {"student": "demo", "mode": "business"})
            sent = [create.call_args.kwargs["instructions"]]
            for i in range(3):
                self._post("/api/message", {"text": f"turn {i}"})
                sent.append(create.call_args.kwargs["instructions"])
        self.assertEqual(len(set(sent)), 1, "system prompt changed mid-call")

    def test_transcript_only_grows(self) -> None:
        # Caching matches on prefix, so editing or dropping earlier turns
        # would invalidate everything from the edit onward.
        self._post("/api/auth", {"passphrase": ""})
        with mock.patch.object(
            web.client.responses, "create", return_value=fake_reply()
        ) as create:
            self._post("/api/start", {"student": "demo", "mode": "business"})
            previous = list(create.call_args.kwargs["input"])
            for i in range(3):
                self._post("/api/message", {"text": f"turn {i}"})
                current = create.call_args.kwargs["input"]
                self.assertEqual(
                    current[: len(previous)], previous, "earlier turns were rewritten"
                )
                self.assertGreater(len(current), len(previous))
                previous = list(current)


class ReportIsNotCachedTest(ServerTestCase, unittest.TestCase):
    def test_report_call_carries_no_cache_marker(self) -> None:
        # A single request: a cache write here is paid for and never read.
        import ai
        client = ai.Connection()
        with mock.patch.object(client.responses, "create", return_value=fake_report_response()) as create:
            tutor.generate_report(client, [{"role": "user", "content": "hi"}])
        kwargs = create.call_args.kwargs
        self.assertNotIn("cache_control", kwargs)
        self.assertIsInstance(kwargs["instructions"], str)
        self.assertTrue(kwargs["text"]["format"]["strict"])


if __name__ == "__main__":
    unittest.main()
