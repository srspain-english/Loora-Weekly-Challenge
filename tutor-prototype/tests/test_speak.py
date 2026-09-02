#!/usr/bin/env python3
"""Tests for the /api/speak endpoint and the ElevenLabs call behind it.

Same approach as test_feedback.py: a real ThreadingHTTPServer on a free
local port, talked to over HTTP. The one thing mocked is the outbound call
to ElevenLabs - these tests must never spend the account's character quota,
and must pass with no ELEVENLABS_API_KEY set.

The behaviour that matters most here is the fallback: a voice failure has
to degrade to the browser's own speech engine, never take down a reply.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web  # noqa: E402

FAKE_MP3 = b"ID3\x04\x00fake mp3 payload"


def post(url: str, body: dict, opener: urllib.request.OpenerDirector):
    """POST JSON; return (status, content_type, raw_body)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(req) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


class SpeakEndpointTest(unittest.TestCase):
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
        web._TTS_CACHE.clear()
        self._orig_key = web.ELEVENLABS_API_KEY
        web.ELEVENLABS_API_KEY = "test-key"

    def tearDown(self) -> None:
        web.ELEVENLABS_API_KEY = self._orig_key
        web._TTS_CACHE.clear()

    def _auth(self) -> None:
        status, _, _ = post(f"{self.base_url}/api/auth", {"passphrase": ""}, self.opener)
        self.assertEqual(status, 200)

    def test_returns_audio_on_success(self) -> None:
        self._auth()
        with mock.patch.object(web, "synthesize_speech", return_value=FAKE_MP3):
            status, ctype, body = post(
                f"{self.base_url}/api/speak", {"text": "Hello there."}, self.opener
            )
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "audio/mpeg")
        self.assertEqual(body, FAKE_MP3)

    def test_missing_key_reports_503_so_page_falls_back(self) -> None:
        # 503 is the page's signal to use the browser voice without warning:
        # not configured is a normal state, not a malfunction.
        web.ELEVENLABS_API_KEY = ""
        self._auth()
        status, _, _ = post(f"{self.base_url}/api/speak", {"text": "Hi"}, self.opener)
        self.assertEqual(status, 503)

    def test_upstream_failure_reports_502_not_500(self) -> None:
        self._auth()
        with mock.patch.object(
            web, "synthesize_speech", side_effect=web.TTSError("bad key")
        ):
            status, _, body = post(
                f"{self.base_url}/api/speak", {"text": "Hi"}, self.opener
            )
        self.assertEqual(status, 502)
        # The upstream detail stays in the server log, not in the response.
        self.assertNotIn(b"bad key", body)

    def test_rejects_empty_text(self) -> None:
        self._auth()
        status, _, _ = post(f"{self.base_url}/api/speak", {"text": "   "}, self.opener)
        self.assertEqual(status, 400)

    def test_requires_auth_when_passphrase_set(self) -> None:
        web.ACCESS_PASSPHRASE = "sr2026"
        status, _, _ = post(f"{self.base_url}/api/speak", {"text": "Hi"}, self.opener)
        self.assertEqual(status, 401)

    def test_truncates_overlong_text_before_billing_for_it(self) -> None:
        self._auth()
        long_text = "x" * (web.MAX_SPEAK_LENGTH + 500)
        with mock.patch.object(
            web, "synthesize_speech", return_value=FAKE_MP3
        ) as synth:
            post(f"{self.base_url}/api/speak", {"text": long_text}, self.opener)
        self.assertEqual(len(synth.call_args.args[0]), web.MAX_SPEAK_LENGTH)

    def test_page_calls_the_endpoint_and_keeps_a_browser_fallback(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/") as resp:
            html = resp.read().decode("utf-8")
        self.assertIn("/api/speak", html)
        self.assertIn("speakWithBrowser", html)

    def test_page_shows_which_voice_engine_was_used(self) -> None:
        # Without this the two engines are indistinguishable to anyone not
        # reading devtools, which is how a silently exhausted quota goes
        # unnoticed.
        with urllib.request.urlopen(f"{self.base_url}/") as resp:
            html = resp.read().decode("utf-8")
        self.assertIn('id="voice-source"', html)
        self.assertIn("showVoiceSource", html)


class SynthesizeSpeechTest(unittest.TestCase):
    """The ElevenLabs call itself: caching, and turning every failure into
    TTSError so a caller can always fall back."""

    def setUp(self) -> None:
        web._TTS_CACHE.clear()
        self._orig_key = web.ELEVENLABS_API_KEY
        web.ELEVENLABS_API_KEY = "test-key"

    def tearDown(self) -> None:
        web.ELEVENLABS_API_KEY = self._orig_key
        web._TTS_CACHE.clear()

    def _fake_response(self, payload: bytes = FAKE_MP3):
        resp = mock.MagicMock()
        resp.read.return_value = payload
        resp.__enter__.return_value = resp
        return resp

    def test_repeated_text_is_served_from_cache(self) -> None:
        # Juno's greetings and prompts repeat constantly across calls; on a
        # 10k-character free tier, paying twice for the same sentence matters.
        with mock.patch.object(
            urllib.request, "urlopen", return_value=self._fake_response()
        ) as urlopen:
            first = web.synthesize_speech("Good morning!")
            second = web.synthesize_speech("Good morning!")
        self.assertEqual(first, FAKE_MP3)
        self.assertEqual(second, FAKE_MP3)
        self.assertEqual(urlopen.call_count, 1, "second call should hit the cache")

    def test_different_text_is_synthesised_separately(self) -> None:
        with mock.patch.object(
            urllib.request, "urlopen", return_value=self._fake_response()
        ) as urlopen:
            web.synthesize_speech("One")
            web.synthesize_speech("Two")
        self.assertEqual(urlopen.call_count, 2)

    def test_cache_is_bounded(self) -> None:
        with mock.patch.object(
            urllib.request, "urlopen", return_value=self._fake_response()
        ):
            for i in range(web._TTS_CACHE_MAX_ENTRIES + 25):
                web.synthesize_speech(f"phrase {i}")
        self.assertLessEqual(len(web._TTS_CACHE), web._TTS_CACHE_MAX_ENTRIES)

    def test_http_error_becomes_tts_error(self) -> None:
        err = urllib.error.HTTPError(
            url="https://api.elevenlabs.io",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )
        err.read = lambda: b'{"detail":"invalid api key"}'
        with mock.patch.object(urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(web.TTSError) as ctx:
                web.synthesize_speech("Hello")
        self.assertIn("401", str(ctx.exception))

    def test_network_failure_becomes_tts_error(self) -> None:
        with mock.patch.object(
            urllib.request, "urlopen", side_effect=OSError("connection reset")
        ):
            with self.assertRaises(web.TTSError):
                web.synthesize_speech("Hello")

    def test_empty_audio_becomes_tts_error(self) -> None:
        with mock.patch.object(
            urllib.request, "urlopen", return_value=self._fake_response(b"")
        ):
            with self.assertRaises(web.TTSError):
                web.synthesize_speech("Hello")

    def test_failure_is_not_cached(self) -> None:
        with mock.patch.object(
            urllib.request, "urlopen", side_effect=OSError("down")
        ):
            with self.assertRaises(web.TTSError):
                web.synthesize_speech("Hello")
        self.assertNotIn("Hello", web._TTS_CACHE)


if __name__ == "__main__":
    unittest.main()
