#!/usr/bin/env python3
"""Tests for saving a class as it happens, and getting it back afterwards.

Before this, a class existed only in the server process's memory until the
student pressed End call. Closing the tab, losing the connection, or Render
idling the service out threw away the whole conversation and its report —
the single worst thing this app could do to someone who had just spent
fifteen minutes working.

Also covered here: a retried request must not append the same turn twice,
and a failure of the cache must not take the class down with it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402
import store  # noqa: E402
import web  # noqa: E402
from helpers import ServerTestCase, fake_reply, fake_report_response  # noqa: E402


class RecoveryTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()

    def _start(self, opener=None, **body):
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply()
        ):
            return self.post("/api/start", {"mode": "free", **body}, opener)

    def _say(self, text, opener=None, **body):
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply(f"re: {text}")
        ):
            return self.post("/api/message", {"text": text, **body}, opener)

    def test_every_turn_is_on_disk_before_the_student_gets_a_reply(self) -> None:
        self.auth()
        _, started = self._start(self.opener, student="saver")
        call_id = started["call_id"]

        self._say("first thing")
        saved = store.get_call(call_id)
        self.assertEqual(saved["turns"], 1)
        self.assertIn(
            "first thing",
            [m["content"] for m in saved["transcript"] if m["role"] == "user"],
        )

    def test_a_reply_says_when_it_was_saved(self) -> None:
        # So the page can show it, rather than the student having to trust us.
        self.auth()
        self._start(self.opener, student="saver2")
        _, data = self._say("hello")
        self.assertIn("saved_at", data)

    def test_a_closed_tab_leaves_a_class_to_come_back_to(self) -> None:
        store.create_student("Returner", access_code="ret-1")
        self.auth()
        _, started = self._start(self.opener, access_code="ret-1")
        self._say("something I said")

        # Tab closed, server restarted: nothing left in memory.
        web.SESSIONS.clear()
        later = self.new_browser()
        self.auth(later)

        status, data = self.post("/api/resume", {"access_code": "ret-1"}, later)
        self.assertEqual(status, 200)
        self.assertIsNotNone(data["open_call"], "the unfinished class was lost")
        self.assertEqual(data["open_call"]["call_id"], started["call_id"])
        said = [m["content"] for m in data["open_call"]["transcript"]
                if m["role"] == "user"]
        self.assertIn("something I said", said)

    def test_a_recovered_class_can_still_be_reported_on(self) -> None:
        # End call was never pressed; the report must still be reachable.
        store.create_student("Reporter", access_code="rep-1")
        self.auth()
        _, started = self._start(self.opener, access_code="rep-1")
        self._say("we practised the present perfect")

        web.SESSIONS.clear()
        later = self.new_browser()
        self.auth(later)
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_report_response()
        ):
            status, data = self.post(
                "/api/end", {"call_id": started["call_id"]}, later
            )
        self.assertEqual(status, 200)
        self.assertIn("what_we_did", data["report"])

    def test_finished_class_is_not_offered_for_resume(self) -> None:
        store.create_student("Done", access_code="done-1")
        self.auth()
        _, started = self._start(self.opener, access_code="done-1")
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_report_response()
        ):
            self.post("/api/end", {"call_id": started["call_id"]})
        _, data = self.post("/api/resume", {"access_code": "done-1"})
        self.assertIsNone(data["open_call"])

    def test_asking_to_end_a_finished_class_returns_its_report(self) -> None:
        # A double-clicked End call, or a retry after a dropped response.
        store.create_student("Twice", access_code="twice-1")
        self.auth()
        _, started = self._start(self.opener, access_code="twice-1")
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_report_response()
        ) as create:
            self.post("/api/end", {"call_id": started["call_id"]})
            status, data = self.post("/api/end", {"call_id": started["call_id"]})
        self.assertEqual(status, 200)
        self.assertIn("what_we_did", data["report"])
        self.assertEqual(create.call_count, 1, "the report was generated twice")


class IdempotencyTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()

    def test_a_retried_message_is_not_added_twice(self) -> None:
        # A flaky connection, or a double-tapped Send: the student sees one
        # answer, the transcript gains one turn, and the model is called once.
        self.auth()
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply()
        ):
            _, started = self.post("/api/start", {"mode": "free", "student": "retry"})

        body = {"text": "did this arrive?", "idempotency_key": "abc-123"}
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply("once")
        ) as create:
            status_a, first = self.post("/api/message", body)
            status_b, second = self.post("/api/message", body)

        self.assertEqual((status_a, status_b), (200, 200))
        self.assertEqual(first["reply"], second["reply"])
        self.assertEqual(create.call_count, 1, "the retry was billed again")

        call = store.get_call(started["call_id"])
        said = [m["content"] for m in call["transcript"] if m["role"] == "user"]
        self.assertEqual(said.count("did this arrive?"), 1,
                         "the retry duplicated the turn in the transcript")

    def test_different_keys_are_different_messages(self) -> None:
        self.auth()
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply()
        ):
            self.post("/api/start", {"mode": "free", "student": "distinct"})
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply()
        ) as create:
            self.post("/api/message", {"text": "one", "idempotency_key": "k1"})
            self.post("/api/message", {"text": "two", "idempotency_key": "k2"})
        self.assertEqual(create.call_count, 2)


class CacheFallbackTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()

    def test_class_continues_uncached_when_the_cached_shape_is_rejected(self) -> None:
        # Caching is an optimisation. If the API stops accepting the cached
        # request shape, the student must still get their class.
        self.auth()
        rejection = anthropic.BadRequestError(
            "cache_control not supported",
            response=mock.Mock(status_code=400, headers={}),
            body=None,
        )
        with mock.patch.object(
            web.client.messages, "create",
            side_effect=[rejection, fake_reply("still here")],
        ) as create:
            status, data = self.post("/api/start", {"mode": "free", "student": "fb"})

        self.assertEqual(status, 200)
        self.assertEqual(data["reply"], "still here")
        self.assertEqual(create.call_count, 2, "no uncached retry was attempted")

        retry_kwargs = create.call_args_list[1].kwargs
        self.assertIsInstance(retry_kwargs["system"], str,
                              "the retry should send a plain uncached prompt")
        self.assertNotIn("cache_control", retry_kwargs)


if __name__ == "__main__":
    unittest.main()
