#!/usr/bin/env python3
"""Tests for the /api/feedback endpoint in web.py.

Runs the real ThreadingHTTPServer on a free local port and talks to it
over HTTP, same as a browser would - no mocking of the server internals.
Doesn't touch the Anthropic API (the feedback endpoint never calls it),
so this runs without an ANTHROPIC_API_KEY set.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web  # noqa: E402


def request(url: str, body: dict, opener: urllib.request.OpenerDirector):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class FeedbackEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_dir = Path(tempfile.mkdtemp())
        cls._orig_feedback_path = web.FEEDBACK_PATH
        web.FEEDBACK_PATH = cls.tmp_dir / "feedback.jsonl"

        cls.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        web.FEEDBACK_PATH = cls._orig_feedback_path
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def setUp(self) -> None:
        # Fresh cookie jar per test so each gets its own server-side session.
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        if web.FEEDBACK_PATH.exists():
            web.FEEDBACK_PATH.unlink()

    def _auth(self) -> None:
        status, _ = request(f"{self.base_url}/api/auth", {"passphrase": ""}, self.opener)
        self.assertEqual(status, 200)

    def test_rejects_empty_feedback(self) -> None:
        self._auth()
        status, data = request(f"{self.base_url}/api/feedback", {"text": "   "}, self.opener)
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        self.assertFalse(web.FEEDBACK_PATH.exists())

    def test_requires_auth_when_passphrase_set(self) -> None:
        web.ACCESS_PASSPHRASE = "sr2026"
        status, data = request(
            f"{self.base_url}/api/feedback", {"text": "Great class!"}, self.opener
        )
        self.assertEqual(status, 401)
        self.assertIn("error", data)

    def test_accepts_and_persists_feedback(self) -> None:
        self._auth()
        status, data = request(
            f"{self.base_url}/api/feedback",
            {"text": "  Loved the structured mode!  ", "student": "alex", "mode": "structured"},
            self.opener,
        )
        self.assertEqual(status, 200)
        self.assertEqual(data, {"ok": True})

        self.assertTrue(web.FEEDBACK_PATH.exists())
        lines = web.FEEDBACK_PATH.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["text"], "Loved the structured mode!")
        self.assertEqual(entry["student"], "alex")
        self.assertEqual(entry["mode"], "structured")
        self.assertIn("submitted_at", entry)

    def test_truncates_overlong_feedback(self) -> None:
        self._auth()
        long_text = "x" * (web.MAX_FEEDBACK_LENGTH + 500)
        status, _ = request(f"{self.base_url}/api/feedback", {"text": long_text}, self.opener)
        self.assertEqual(status, 200)
        entry = json.loads(web.FEEDBACK_PATH.read_text().strip())
        self.assertEqual(len(entry["text"]), web.MAX_FEEDBACK_LENGTH)

    def test_feedback_button_present_in_page(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/") as resp:
            html = resp.read().decode("utf-8")
        self.assertIn('id="feedback-btn"', html)
        self.assertIn('id="feedback-panel"', html)
        self.assertIn("/api/feedback", html)


if __name__ == "__main__":
    unittest.main()
