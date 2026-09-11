#!/usr/bin/env python3
"""Tests for what the page itself offers the student.

The interface is one HTML string, so these check it is served with the pieces
the student-facing fixes depend on. They are shallow by nature — no browser
here — but they catch the failure that actually happens: an element renamed
or dropped while editing the page, leaving the JavaScript that drives it
silently pointing at nothing.
"""

from __future__ import annotations

import sys
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web  # noqa: E402
from helpers import ServerTestCase  # noqa: E402


class PageTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        with urllib.request.urlopen(f"{self.base_url}/") as resp:
            self.html = resp.read().decode("utf-8")

    def test_distinguishes_waking_from_offline_from_broken(self) -> None:
        # Three different situations that used to look identical to a student.
        self.assertIn("waking up", self.html)
        self.assertIn("offline", self.html)
        self.assertIn("'network'", self.html)

    def test_shows_when_the_class_was_last_saved(self) -> None:
        self.assertIn('id="chat-status"', self.html)
        self.assertIn("showSaved", self.html)
        self.assertIn("Saved ", self.html)

    def test_warns_before_leaving_a_class_in_progress(self) -> None:
        self.assertIn("beforeunload", self.html)

    def test_offers_to_recover_an_unfinished_class(self) -> None:
        self.assertIn('id="resume-notice"', self.html)
        self.assertIn("/api/resume", self.html)
        # And the three things a student might want to do with it.
        self.assertIn('id="resume-btn"', self.html)
        self.assertIn('id="resume-report-btn"', self.html)
        self.assertIn('id="resume-discard-btn"', self.html)

    def test_mic_records_by_tap_as_well_as_hold(self) -> None:
        self.assertIn("TAP_MS", self.html)
        self.assertIn("tap to stop", self.html)

    def test_mic_shows_that_it_is_recording(self) -> None:
        self.assertIn("Listening…", self.html)
        self.assertIn("#mic-btn.recording", self.html)

    def test_report_generation_has_a_visible_state(self) -> None:
        self.assertIn("Writing your report", self.html)

    def test_actions_cannot_be_fired_twice(self) -> None:
        self.assertIn("if (sending) return;", self.html)
        self.assertIn("if ($('start-btn').disabled) return;", self.html)
        self.assertIn("if ($('end-btn').disabled) return;", self.html)

    def test_messages_carry_an_idempotency_key(self) -> None:
        self.assertIn("idempotency_key", self.html)

    def test_privacy_notice_answers_the_five_questions(self) -> None:
        # What is kept, what for, who sees it, what not to type, how to delete.
        self.assertIn("What Juno saves about you", self.html)
        self.assertIn("What it keeps", self.html)
        self.assertIn("What it is for", self.html)
        self.assertIn("Who can see it", self.html)
        self.assertIn("do not type confidential information", self.html)
        self.assertIn("deleted", self.html)

    def test_offers_a_personal_code_field(self) -> None:
        self.assertIn('id="student-code"', self.html)

    def test_visual_identity_is_unchanged(self) -> None:
        # The brief was to change only what was needed. These are the tokens
        # and faces the rest of S&R's material shares.
        self.assertIn("--accent:#FF4A1C", self.html)
        self.assertIn("--bg:#FFFDF9", self.html)
        self.assertIn("Space+Grotesk", self.html)
        self.assertIn("Instrument+Sans", self.html)


if __name__ == "__main__":
    unittest.main()
