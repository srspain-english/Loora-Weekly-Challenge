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
import os
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


from types import SimpleNamespace
import ai


class FakeResponse(SimpleNamespace):
    @property
    def stop_reason(self):
        return "refusal" if not self.output_text else "end_turn"

    @stop_reason.setter
    def stop_reason(self, value):
        if value == "refusal":
            self.output = [SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal", refusal="No")])]
            self.output_text = ""

    def __iter__(self):
        # Several chunks exercise the real stream consumer. Dedicated tests
        # below cover failures, refusal, ordering and incomplete completion.
        for chunk in self.chunks:
            yield SimpleNamespace(type="response.output_text.delta", delta=chunk)
        yield SimpleNamespace(type="response.completed", response=self)

    def close(self):
        pass


def fake_reply(text="Hello there.", *, cache_read=0, cache_write=0):
    usage = SimpleNamespace(input_tokens=120 + cache_read + cache_write, output_tokens=40,
        input_tokens_details=SimpleNamespace(cached_tokens=cache_read, cache_write_tokens=cache_write))
    return FakeResponse(status="completed", output_text=text,
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=text)])],
        usage=usage, chunks=[text[:5], text[5:]])


def fake_report_response(report=None):
    response = fake_reply(json.dumps(report if report is not None else {
        "what_we_did": "Practised the present perfect.", "corrections": [], "word_traps": [],
        "pronunciation": [], "vocabulary_learned": [], "what_went_well": ["Full sentences throughout."],
        "homework": [], "updated_recurring_error_patterns": [], "next_recommendation": "Keep going.",
        "grammar_point_taught": "",
        "skill_estimates": {"enough_evidence": True, "fluency": "B1", "grammar": "B1",
                            "vocabulary": "B1", "understanding": "B2"}}))
    return response


class ServerTestCase:
    """Mixin: a real HTTP server on a free port, over a throwaway database."""

    @classmethod
    def setUpClass(cls) -> None:
        if store.using_postgres() and store.DATABASE_URL != os.environ.get("JUNO_TEST_DATABASE_URL"):
            raise RuntimeError("Tests require an explicitly disposable database")
        cls.tmp = Path(tempfile.mkdtemp())
        store.reset_for_tests(cls.tmp / "juno.db")
        cls._orig_students_dir = tutor.STUDENTS_DIR
        tutor.STUDENTS_DIR = cls.tmp / "students"
        tutor.STUDENTS_DIR.mkdir(parents=True, exist_ok=True)

        cls.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

        cls._network_patch = mock.patch.object(ai.Responses, "create",
            side_effect=AssertionError("Unmocked OpenAI request forbidden in tests"))
        cls._network_patch.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._network_patch.stop()
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
                return resp.status, self._parse_body(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, self._parse_body(e.read())

    @staticmethod
    def _parse_body(raw: bytes):
        """A reply is one JSON object, or (for a streamed turn) a line of
        JSON per event. Tests only care about the end state, so fold a
        stream down to its last event (the "final" or "error" one) and
        hand back the same shape a one-shot reply always had."""
        lines = [ln for ln in raw.decode("utf-8").splitlines() if ln.strip()]
        if len(lines) <= 1:
            return json.loads(raw)
        return json.loads(lines[-1])

    def auth(self, opener=None):
        status, _ = self.post("/api/auth", {"passphrase": ""}, opener)
        assert status == 200, "auth failed in test setup"
