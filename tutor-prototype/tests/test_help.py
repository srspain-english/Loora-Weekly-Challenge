#!/usr/bin/env python3
"""Tests for "Help me": Spanish help during a class.

Help is an aside, not a turn. It must never change the class transcript
(and so the report), and since every press is a paid request, it is capped.
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


def fake_help(text="Juno te pregunta qué has hecho hoy.", stop_reason="end_turn"):
    response = fake_reply(text)
    response.stop_reason = stop_reason
    return response


class HelpTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self.auth()

    def _start(self):
        with mock.patch.object(web.client.messages, "create",
                               return_value=fake_reply("How was your day?")):
            _, started = self.post("/api/start", {"mode": "free", "level": "A2"})
        return started["call_id"]

    def _help(self, response=None):
        with mock.patch.object(web.client.beta.messages, "create",
                               return_value=response or fake_help()) as create:
            status, body = self.post("/api/help", {})
        return status, body, create

    def test_needs_a_class_in_progress(self) -> None:
        status, body, create = self._help()
        self.assertEqual(status, 400)
        create.assert_not_called()

    def test_returns_spanish_help_without_touching_the_class(self) -> None:
        call_id = self._start()
        before = store.get_call(call_id)

        status, body, create = self._help()

        self.assertEqual(status, 200)
        self.assertEqual(body["help"], "Juno te pregunta qué has hecho hoy.")
        after = store.get_call(call_id)
        self.assertEqual(after["transcript"], before["transcript"])
        self.assertEqual(after["turns"], before["turns"])
        sent = create.call_args.kwargs
        self.assertIn("A2", sent["system"])
        self.assertIn("How was your day?", sent["messages"][0]["content"])

    def test_a_refusal_gets_a_friendly_answer(self) -> None:
        self._start()
        status, body, _ = self._help(fake_help("", stop_reason="refusal"))
        self.assertEqual(status, 200)
        self.assertIn("frase sencilla", body["help"])

    def test_is_capped_per_class(self) -> None:
        self._start()
        for _ in range(web.MAX_HELP_PER_CALL):
            status, _, _ = self._help()
            self.assertEqual(status, 200)
        status, body, create = self._help()
        self.assertEqual(status, 429)
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
