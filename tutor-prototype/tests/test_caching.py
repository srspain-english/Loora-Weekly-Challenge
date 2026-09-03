#!/usr/bin/env python3
"""Tests for prompt caching on the turn-by-turn call loop.

Caching is worth roughly 70% of what a call costs to run, and every way it
breaks is silent: no error, no behaviour change, just a bill several times
larger than it should be. Nothing in a normal test run would notice. So the
conditions it depends on are asserted here instead:

- the requests actually carry the cache markers (someone editing the call
  sites is the likeliest way this gets dropped);
- the system prompt is byte-identical across turns, since caching matches on
  prefix and interpolating a date or a turn counter into it would cost the
  whole saving;
- the prompt clears the model's minimum cacheable prefix, below which the
  API silently declines to cache at all;
- the end-of-call report is deliberately *not* cached - one request, never
  read back, and its tools change the prefix anyway.

No network: the Anthropic client is mocked, so this runs with no API key.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tutor  # noqa: E402
import web  # noqa: E402

# Opus 5 will not create a cache entry for a prefix shorter than this, and
# says nothing when it declines. Other models in the same family sit as high
# as 4096, so this is also the number to revisit if MODEL ever changes.
MIN_CACHEABLE_TOKENS = 512
CHARS_PER_TOKEN = 4  # deliberately rough; only used for a floor, never a ceiling

STUDENT = {
    "name": "Alex",
    "cefr_level": "B1",
    "grammar_covered": [],
    "recurring_errors": [],
    "sessions": [],
}


def fake_reply(text: str = "Hello there."):
    block = mock.Mock()
    block.type = "text"
    block.text = text
    response = mock.Mock()
    response.content = [block]
    return response


class CacheableSystemTest(unittest.TestCase):
    def test_marks_the_prompt_as_cacheable(self) -> None:
        blocks = tutor.cacheable_system("some system prompt")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "text")
        self.assertEqual(blocks[0]["text"], "some system prompt")
        self.assertEqual(blocks[0]["cache_control"], {"type": "ephemeral"})

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


class CallLoopSendsCacheMarkersTest(unittest.TestCase):
    """The requests the server actually builds, start and message alike."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()

    def _post(self, path: str, body: dict):
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(req) as resp:
            return json.loads(resp.read())

    def _assert_cached(self, kwargs: dict) -> None:
        system = kwargs["system"]
        self.assertIsInstance(system, list, "system must be blocks to carry a marker")
        self.assertEqual(system[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(
            kwargs.get("cache_control"),
            {"type": "ephemeral"},
            "top-level marker missing - the growing transcript would go uncached",
        )

    def test_opening_turn_is_cached(self) -> None:
        self._post("/api/auth", {"passphrase": ""})
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply()
        ) as create:
            self._post("/api/start", {"student": "demo", "mode": "business"})
        self._assert_cached(create.call_args.kwargs)

    def test_every_later_turn_is_cached_too(self) -> None:
        self._post("/api/auth", {"passphrase": ""})
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply()
        ) as create:
            self._post("/api/start", {"student": "demo", "mode": "business"})
            for i in range(3):
                self._post("/api/message", {"text": f"turn {i}"})
                self._assert_cached(create.call_args.kwargs)

    def test_system_prompt_is_reused_verbatim_across_turns(self) -> None:
        # Rebuilding it per turn would be the easy way to reintroduce drift.
        self._post("/api/auth", {"passphrase": ""})
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply()
        ) as create:
            self._post("/api/start", {"student": "demo", "mode": "business"})
            sent = [create.call_args.kwargs["system"][0]["text"]]
            for i in range(3):
                self._post("/api/message", {"text": f"turn {i}"})
                sent.append(create.call_args.kwargs["system"][0]["text"])
        self.assertEqual(len(set(sent)), 1, "system prompt changed mid-call")

    def test_transcript_only_grows(self) -> None:
        # Caching matches on prefix, so editing or dropping earlier turns
        # would invalidate everything from the edit onward.
        self._post("/api/auth", {"passphrase": ""})
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply()
        ) as create:
            self._post("/api/start", {"student": "demo", "mode": "business"})
            previous = list(create.call_args.kwargs["messages"])
            for i in range(3):
                self._post("/api/message", {"text": f"turn {i}"})
                current = create.call_args.kwargs["messages"]
                self.assertEqual(
                    current[: len(previous)], previous, "earlier turns were rewritten"
                )
                self.assertGreater(len(current), len(previous))
                previous = list(current)


class ReportIsNotCachedTest(unittest.TestCase):
    def test_report_call_carries_no_cache_marker(self) -> None:
        # A single request: a cache write here is paid for and never read.
        client = mock.Mock()
        tool_block = mock.Mock()
        tool_block.type = "tool_use"
        tool_block.input = {"what_we_did": "practised"}
        client.messages.create.return_value = mock.Mock(content=[tool_block])

        tutor.generate_report(client, [{"role": "user", "content": "hi"}])

        kwargs = client.messages.create.call_args.kwargs
        self.assertNotIn("cache_control", kwargs)
        self.assertIsInstance(kwargs["system"], str)


if __name__ == "__main__":
    unittest.main()
