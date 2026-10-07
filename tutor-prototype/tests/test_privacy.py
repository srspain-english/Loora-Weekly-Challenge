#!/usr/bin/env python3
"""Tests for what leaves the server: logs, and who can read a class.

Metrics are worth having, and the temptation with metrics is to log the whole
request while you are there. That would put a second copy of every student's
conversation somewhere nobody is guarding it — Render's log viewer, retained
by default, readable by anyone with dashboard access. So what may appear in a
log is pinned down here rather than left to whoever edits the logging next.
"""

from __future__ import annotations

import io
import json
import re
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store  # noqa: E402
import web  # noqa: E402
from helpers import ServerTestCase, fake_reply  # noqa: E402

SECRET_LOOKING = "sk-ant-not-a-real-key-000000"


class LogsTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()

    def _run_class_capturing_logs(self, said: str) -> str:
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            with mock.patch.object(
                web.client.messages, "create", return_value=fake_reply("a reply")
            ):
                self.post("/api/start", {"mode": "free", "student": "Logged Student"})
                self.post("/api/message", {"text": said})
        return buffer.getvalue()

    def test_the_transcript_never_reaches_the_logs(self) -> None:
        secret_sentence = "my colleague Rocio is being made redundant on Friday"
        logs = self._run_class_capturing_logs(secret_sentence)
        self.assertNotIn(secret_sentence, logs)
        self.assertNotIn("Rocio", logs)

    def test_the_students_name_never_reaches_the_logs(self) -> None:
        logs = self._run_class_capturing_logs("hello")
        self.assertNotIn("Logged Student", logs)

    def test_metrics_carry_counts_and_the_internal_id_only(self) -> None:
        logs = self._run_class_capturing_logs("hello")
        turn_lines = [json.loads(line) for line in logs.splitlines()
                      if line.startswith('{"event": "turn"')]
        self.assertTrue(turn_lines, "no metrics were logged at all")
        for entry in turn_lines:
            self.assertTrue(entry["student_id"].startswith("stu_"))
            for field in ("input_tokens", "cache_read_tokens",
                          "cache_write_tokens", "output_tokens", "cost_usd"):
                self.assertIn(field, entry)
            self.assertNotIn("text", entry)
            self.assertNotIn("transcript", entry)
            self.assertNotIn("display_name", entry)

    def test_an_api_key_is_never_echoed_by_the_error_path(self) -> None:
        # Anything the server prints on failure goes to the same log.
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            with mock.patch.object(
                web.client.messages, "create",
                side_effect=RuntimeError(f"boom with {SECRET_LOOKING}"),
            ):
                status, data = self.post(
                    "/api/start", {"mode": "free", "student": "x"}
                )
        # The class fails, but the value must not be spread any further than
        # the exception that already carried it.
        self.assertEqual(status, 500)
        self.assertNotIn(SECRET_LOOKING, json.dumps(data))


class SourceHygieneTest(unittest.TestCase):
    """The repository itself must not carry credentials or a dead integration."""

    FILES = ["web.py", "tutor.py", "store.py", "README.md"]

    def _read(self, name: str) -> str:
        return (Path(__file__).resolve().parent.parent / name).read_text()

    def test_no_hardcoded_credentials(self) -> None:
        # Looks for a real key, not the "sk-ant-..." placeholder the docstring
        # uses to show people what to export. A test that cannot tell those
        # apart gets disabled the first time it cries wolf.
        real_key = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")
        for name in self.FILES:
            body = self._read(name)
            with self.subTest(file=name):
                self.assertIsNone(real_key.search(body),
                                  f"{name} looks like it contains a real API key")
                self.assertNotIn("xi-api-key", body)

    def test_elevenlabs_is_fully_gone(self) -> None:
        # The integration was removed; a leftover reference would suggest the
        # old key still matters somewhere.
        for name in self.FILES:
            with self.subTest(file=name):
                self.assertNotIn("ELEVENLABS_API_KEY", self._read(name))

    def test_secrets_are_read_from_the_environment(self) -> None:
        body = self._read("web.py")
        self.assertIn('os.environ.get("JUNO_ACCESS_PASSPHRASE"', body)


if __name__ == "__main__":
    unittest.main()
