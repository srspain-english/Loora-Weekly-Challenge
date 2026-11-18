#!/usr/bin/env python3
"""Tests for the server-side usage limits.

The limit these replace was a counter in a cookie, which meant it cost a
student nothing to clear their cookies and start again, and meant a server
restart handed everyone a fresh allowance. Spend is real money, so the
limits have to live where the student cannot reach them.

The tests that matter here are the evasion ones: a new browser and a wiped
session must not reset anything.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store  # noqa: E402
import web  # noqa: E402
from helpers import ServerTestCase, fake_reply  # noqa: E402


class LimitsTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self._orig = (store.MAX_CALLS_PER_DAY, store.MAX_TURNS_PER_CALL,
                      store.DAILY_COST_CEILING_USD)

    def tearDown(self) -> None:
        (store.MAX_CALLS_PER_DAY, store.MAX_TURNS_PER_CALL,
         store.DAILY_COST_CEILING_USD) = self._orig

    def _start(self, opener=None, **body):
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply()
        ):
            return self.post("/api/start", {"mode": "free", **body}, opener)

    def _say(self, text, opener=None, **body):
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply()
        ):
            return self.post("/api/message", {"text": text, **body}, opener)

    def test_daily_call_limit_applies(self) -> None:
        store.MAX_CALLS_PER_DAY = 2
        self.auth()
        self.assertEqual(self._start(self.opener, student="lim1")[0], 200)
        self.assertEqual(self._start(self.opener)[0], 200)
        status, data = self._start(self.opener)
        self.assertEqual(status, 429)
        self.assertIn("daily limit", data["error"])

    def test_clearing_cookies_does_not_reset_the_limit(self) -> None:
        # The evasion the old cookie counter allowed. The student keeps their
        # identity through their access code, so the count follows them.
        store.MAX_CALLS_PER_DAY = 1
        store.create_student("Evasive", access_code="ev-1")

        first = self.new_browser()
        self.auth(first)
        self.assertEqual(self._start(first, access_code="ev-1")[0], 200)

        wiped = self.new_browser()  # new cookie jar: a "fresh" browser
        self.auth(wiped)
        status, data = self._start(wiped, access_code="ev-1")
        self.assertEqual(status, 429, "a new browser reset the daily limit")
        self.assertIn("daily limit", data["error"])

    def test_server_restart_does_not_reset_the_limit(self) -> None:
        # Limits live in the database, not the process, so dropping every
        # in-memory session leaves them standing.
        store.MAX_CALLS_PER_DAY = 1
        store.create_student("Persistent", access_code="pe-1")
        self.auth()
        self.assertEqual(self._start(self.opener, access_code="pe-1")[0], 200)

        web.SESSIONS.clear()  # what a restart looks like to the rest of the app
        after = self.new_browser()
        self.auth(after)
        self.assertEqual(self._start(after, access_code="pe-1")[0], 429)

    def test_turn_limit_ends_a_long_class(self) -> None:
        store.MAX_TURNS_PER_CALL = 2
        self.auth()
        self._start(self.opener, student="chatty")
        self.assertEqual(self._say("one")[0], 200)
        self.assertEqual(self._say("two")[0], 200)
        status, data = self._say("three")
        self.assertEqual(status, 429)
        self.assertIn("End call", data["error"])

    def test_global_ceiling_stops_new_classes_for_everyone(self) -> None:
        store.DAILY_COST_CEILING_USD = 0.001
        someone = store.create_student("Spender")
        store.record_usage(someone, 0.01)

        self.auth()
        status, data = self._start(self.opener, student="unlucky")
        self.assertEqual(status, 429)
        self.assertIn("everyone", data["error"])

    def test_overlong_message_is_refused_before_it_is_billed(self) -> None:
        self.auth()
        self._start(self.opener, student="verbose")
        with mock.patch.object(web.client.messages, "create") as create:
            status, _ = self.post(
                "/api/message", {"text": "x" * (store.MAX_MESSAGE_CHARS + 1)}
            )
        self.assertEqual(status, 400)
        create.assert_not_called()

    def test_limit_messages_tell_the_student_what_to_do(self) -> None:
        # A bare 429 in front of a student mid-class is a dead end; each of
        # these has to say what happened and what comes next.
        store.MAX_CALLS_PER_DAY = 1
        self.auth()
        self._start(self.opener, student="polite")
        _, data = self._start(self.opener)
        self.assertNotIn("429", data["error"])
        self.assertGreater(len(data["error"]), 30)


class CostEstimateTest(unittest.TestCase):
    def test_cache_reads_are_cheaper_than_fresh_input(self) -> None:
        fresh = mock.Mock(input_tokens=1_000_000, output_tokens=0,
                          cache_read_input_tokens=0, cache_creation_input_tokens=0)
        cached = mock.Mock(input_tokens=0, output_tokens=0,
                           cache_read_input_tokens=1_000_000,
                           cache_creation_input_tokens=0)
        self.assertLess(store.estimate_cost(cached), store.estimate_cost(fresh))

    def test_missing_usage_costs_nothing_rather_than_raising(self) -> None:
        # Metrics must never be the reason a class fails.
        self.assertEqual(store.estimate_cost(None), 0.0)


if __name__ == "__main__":
    unittest.main()
