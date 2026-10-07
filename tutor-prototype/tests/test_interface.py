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

    def test_mic_starts_on_a_real_tap(self) -> None:
        # The HTML standard only counts a finished tap (touchend) or a click
        # as the user really acting; touchstart isn't one. Starting the mic
        # from touchstart was the suspected cause of the silent red button
        # on iPhone.
        self.assertIn("micBtn.addEventListener('click'", self.html)
        self.assertNotIn("addEventListener('touchstart'", self.html)

    def test_mic_never_looks_dead(self) -> None:
        # Safari can accept start() and then report nothing, or hear the
        # words and never finish. The tap must show something at once, a
        # silent failure must become a message, and heard words must still
        # be sent when the browser never says it has finished.
        self.assertIn("Starting the microphone…", self.html)
        self.assertIn("finish('no-start')", self.html)
        self.assertIn("SILENCE_MS", self.html)
        self.assertIn("cancelListening();", self.html)

    def test_says_when_it_is_the_students_turn(self) -> None:
        self.assertIn("Your turn — tap to talk", self.html)

    def test_voice_never_reads_formatting_symbols(self) -> None:
        # Juno marks corrections with **double asterisks**, and the voice
        # read them out as "asterisk". They are stripped before speaking and
        # shown as a highlight on screen instead.
        self.assertIn("new SpeechSynthesisUtterance(speakable(text))", self.html)
        self.assertIn("text.split('**')", self.html)

    def test_mic_says_why_it_failed(self) -> None:
        # Shows the words as they are spoken, and the reason when it fails,
        # instead of failing silently.
        self.assertIn("recognition.interimResults = true", self.html)
        self.assertIn("recognition.onerror = (e) => { voiceError = e.error; }", self.html)
        self.assertIn("The microphone is blocked for this site", self.html)

    def test_mic_shows_that_it_is_recording(self) -> None:
        self.assertIn("#mic-btn.recording", self.html)

    def test_report_generation_has_a_visible_state(self) -> None:
        self.assertIn("Writing your report", self.html)

    def test_the_very_first_auth_check_shows_waking_up_too(self) -> None:
        # Reported as "Juno doesn't load" on a cold Render instance: the
        # silent empty-passphrase probe that fires on every page load, before
        # the student touches anything, was the one call in the file that
        # never passed onWaking - so a slow cold start left the static
        # "Loading…" text frozen on screen with zero feedback for up to a
        # minute, which is indistinguishable from broken. Every other call
        # already had this; this one needs it even more, since it's the
        # first thing that runs.
        self.assertIn("onWaking: () => { $('lede').textContent", self.html)

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

    def test_keeps_sr_brand_accent_and_fonts(self) -> None:
        # The class screen went dark, but the S&R orange and typefaces the
        # rest of S&R's material shares are kept.
        self.assertIn("--accent:#FF4A1C", self.html)
        self.assertIn("Space+Grotesk", self.html)
        self.assertIn("Instrument+Sans", self.html)

    def test_class_screen_has_pause_help_and_typing(self) -> None:
        for el in ('id="pause-btn"', 'id="help-btn"', 'id="end-btn"',
                   'id="type-toggle"', 'id="call-clock"', 'id="juno-line"',
                   "/api/help"):
            self.assertIn(el, self.html)


if __name__ == "__main__":
    unittest.main()
