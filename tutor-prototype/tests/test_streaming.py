#!/usr/bin/env python3
"""Tests for speaking Juno's reply as it streams in, rather than waiting for
the whole thing: the sentence-boundary splitter, and the server's turn
endpoint under both JUNO_STREAM_REPLIES settings.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tutor  # noqa: E402
import web  # noqa: E402
from helpers import ServerTestCase, fake_reply  # noqa: E402


class SplitReadySentencesTest(unittest.TestCase):
    def test_two_finished_sentences_are_both_returned(self) -> None:
        ready, rest = tutor.split_ready_sentences("Hi there. How are you? I'm ")
        self.assertEqual(ready, ["Hi there.", "How are you?"])
        self.assertEqual(rest, " I'm ")

    def test_a_decimal_number_is_not_mistaken_for_a_sentence_end(self) -> None:
        ready, rest = tutor.split_ready_sentences("It costs 3.5 dollars")
        self.assertEqual(ready, [])
        self.assertEqual(rest, "It costs 3.5 dollars")

    def test_trailing_punctuation_at_the_end_of_the_buffer_waits(self) -> None:
        # "Mr." might still turn into "Mr. Smith" once more text streams in,
        # so a sentence-ender with nothing after it yet is deferred.
        ready, rest = tutor.split_ready_sentences("Good morning, Mr.")
        self.assertEqual(ready, [])
        self.assertEqual(rest, "Good morning, Mr.")

    def test_empty_buffer_returns_nothing(self) -> None:
        self.assertEqual(tutor.split_ready_sentences(""), ([], ""))

    def test_a_quote_or_bracket_after_the_punctuation_still_counts(self) -> None:
        # The ending is confirmed by what follows the quote, not just the
        # punctuation mark itself - the trailing quote is left for the next
        # sentence to pick up along with its leading space.
        ready, rest = tutor.split_ready_sentences('She said "Hi." Then left.')
        self.assertEqual(ready, ['She said "Hi.'])
        self.assertEqual(rest, '" Then left.')


class StreamingTurnTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self._orig_stream = web.STREAM_REPLIES
        web.STREAM_REPLIES = True
        self.auth()

    def tearDown(self) -> None:
        web.STREAM_REPLIES = self._orig_stream

    def test_a_streamed_turn_still_looks_like_an_ordinary_reply(self) -> None:
        with mock.patch.object(web.client.messages, "create",
                               return_value=fake_reply("Good morning. How are you today?")):
            self.post("/api/start", {"mode": "free"})
            status, data = self.post("/api/message", {"text": "Hello."})
        self.assertEqual(status, 200)
        self.assertEqual(data["reply"], "Good morning. How are you today?")
        self.assertIn("saved_at", data)

    def test_the_content_type_is_ndjson_while_streaming_is_on(self) -> None:
        with mock.patch.object(web.client.messages, "create", return_value=fake_reply("Hi!")):
            self.post("/api/start", {"mode": "free"})
            req = __import__("urllib.request", fromlist=["Request"]).Request(
                f"{self.base_url}/api/message",
                data=json.dumps({"text": "Hello."}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.opener.open(req) as resp:
                self.assertIn("ndjson", resp.headers.get("Content-Type", ""))

    def test_an_error_mid_stream_is_reported_cleanly(self) -> None:
        with mock.patch.object(web.client.messages, "create", return_value=fake_reply("Hi!")):
            self.post("/api/start", {"mode": "free"})
        with mock.patch.object(web.client.messages, "create", side_effect=RuntimeError("boom")):
            status, data = self.post("/api/message", {"text": "Hello."})
        self.assertEqual(status, 200)  # headers were already sent
        self.assertEqual(data["type"], "error")
        self.assertIn("your class is saved", data["error"])

    def test_turning_streaming_off_falls_back_to_a_single_reply(self) -> None:
        web.STREAM_REPLIES = False
        with mock.patch.object(web.client.messages, "create", return_value=fake_reply("Hi!")):
            self.post("/api/start", {"mode": "free"})
            status, data = self.post("/api/message", {"text": "Hello."})
        self.assertEqual(status, 200)
        self.assertEqual(data["reply"], "Hi!")


if __name__ == "__main__":
    unittest.main()
