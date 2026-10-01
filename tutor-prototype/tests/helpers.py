#!/usr/bin/env python3
"""Shared scaffolding for the server tests.

Two things every test here needs and must not get wrong:

The database is a temporary one per test class. A test that wrote to the real
data/juno.db would quietly corrupt a running pilot's limits and saved classes,
which is exactly the kind of damage a test suite must never do.

Student memory files also go to a temporary directory, for the same reason —
tutor.save_student writes real JSON to disk at the end of every call.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store  # noqa: E402
import tutor  # noqa: E402
import web  # noqa: E402


def fake_reply(text: str = "Hello there.", *, cache_read: int = 0,
               cache_write: int = 0):
    """A stand-in for one Anthropic response, with a usable usage block."""
    block = mock.Mock()
    block.type = "text"
    block.text = text
    response = mock.Mock()
    response.content = [block]
    response.usage = mock.Mock(
        input_tokens=120,
        output_tokens=40,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )
    return response


def fake_report_response(report: dict | None = None):
    """A stand-in for the end-of-call report tool call."""
    block = mock.Mock()
    block.type = "tool_use"
    block.input = report or {
        "what_we_did": "Practised the present perfect.",
        "corrections": [],
        "word_traps": [],
        "pronunciation": [],
        "vocabulary_learned": [],
        "what_went_well": ["Full sentences throughout."],
        "homework": [],
        "updated_recurring_error_patterns": [],
        "next_recommendation": "Keep going.",
        "grammar_point_taught": "",
    }
    response = mock.Mock()
    response.content = [block]
    response.usage = mock.Mock(
        input_tokens=800, output_tokens=300,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    return response


class ServerTestCase:
    """Mixin: a real HTTP server on a free port, over a throwaway database."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        store.reset_for_tests(cls.tmp / "juno.db")
        cls._orig_students_dir = tutor.STUDENTS_DIR
        tutor.STUDENTS_DIR = cls.tmp / "students"
        tutor.STUDENTS_DIR.mkdir(parents=True, exist_ok=True)

        cls.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        tutor.STUDENTS_DIR = cls._orig_students_dir
        store.reset_for_tests()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def new_browser(self):
        """A fresh cookie jar — a different device, as far as the server sees."""
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def post(self, path: str, body: dict, opener=None):
        opener = opener or self.opener
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with opener.open(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def auth(self, opener=None):
        status, _ = self.post("/api/auth", {"passphrase": ""}, opener)
        assert status == 200, "auth failed in test setup"
