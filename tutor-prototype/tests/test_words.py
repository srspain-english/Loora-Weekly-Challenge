#!/usr/bin/env python3
"""Tests for what students asked for in class: tap a word to see what it
means, hear the suggested answers in Help me, and an access code that does
not care about capitals or spaces."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web  # noqa: E402
from helpers import ServerTestCase, fake_reply  # noqa: E402


def model_says(text, stop_reason="end_turn"):
    response = fake_reply(text)
    response.stop_reason = stop_reason
    return response


class InClassTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        web.DEFINE_CACHE.clear()
        self.auth()

    def start(self):
        with mock.patch.object(web.client.messages, "create", return_value=fake_reply("Does it make sense?")):
            self.post("/api/start", {"mode": "free", "level": "A2"})

    def define(self, word="sense", sentence="Does it make sense?", reply="tener sentido\nIt makes sense to me."):
        with mock.patch.object(web.client.beta.messages, "create", return_value=model_says(reply)) as create:
            status, body = self.post("/api/define", {"word": word, "sentence": sentence})
        return status, body, create

    def test_a_tapped_word_comes_back_with_its_meaning_and_an_example(self) -> None:
        self.start()
        status, body, create = self.define()
        self.assertEqual(status, 200)
        self.assertEqual(body, {"word": "sense", "meaning": "tener sentido", "example": "It makes sense to me."})
        sent = create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Does it make sense?", sent)       # meaning in context, not a dictionary list

    def test_the_same_word_in_the_same_sentence_is_not_paid_for_twice(self) -> None:
        self.start()
        self.define()
        _, body, create = self.define()
        create.assert_not_called()
        self.assertEqual(body["meaning"], "tener sentido")

    def test_only_during_a_class_and_only_real_words(self) -> None:
        self.assertEqual(self.define()[0], 400)
        self.start()
        status, _, create = self.define(word="!!!")
        self.assertEqual(status, 400)
        create.assert_not_called()

    def test_lookups_are_capped(self) -> None:
        self.start()
        with mock.patch.object(web, "MAX_LOOKUPS_PER_DAY", 2):
            self.assertEqual(self.define(word="make")[0], 200)
            self.assertEqual(self.define(word="does")[0], 200)
            status, body, create = self.define(word="it")
        self.assertEqual(status, 429)
        create.assert_not_called()

    def test_a_refusal_is_a_plain_message(self) -> None:
        self.start()
        with mock.patch.object(web.client.beta.messages, "create", return_value=model_says("", "refusal")):
            status, body = self.post("/api/define", {"word": "sense", "sentence": "x"})
        self.assertEqual(status, 502)
        self.assertIn("palabra", body["error"])

    def test_help_answers_come_back_separately_so_they_can_be_played(self) -> None:
        self.start()
        text = ("Juno te pregunta si tiene sentido.\n"
                ">> Yes, it makes sense. || Sí, tiene sentido.\n"
                ">> Sorry, can you say it again? || Perdona, ¿puedes repetirlo?")
        with mock.patch.object(web.client.beta.messages, "create", return_value=model_says(text)):
            status, body = self.post("/api/help", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["explanation"], "Juno te pregunta si tiene sentido.")
        self.assertEqual(body["answers"][0], {"en": "Yes, it makes sense.", "es": "Sí, tiene sentido."})
        self.assertEqual(len(body["answers"]), 2)
        self.assertEqual(body["help"], text)              # older pages still show it


class AccessCodeTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self._code = web.ACCESS_PASSPHRASE
        web.ACCESS_PASSPHRASE = "sr2026"

    def tearDown(self) -> None:
        web.ACCESS_PASSPHRASE = self._code

    def test_capitals_and_spaces_do_not_matter(self) -> None:
        for typed in ("sr2026", "SR2026", "Sr 2026", " sr2026 "):
            status, _ = self.post("/api/auth", {"passphrase": typed}, self.new_browser())
            self.assertEqual(status, 200, typed)

    def test_a_wrong_code_is_still_wrong(self) -> None:
        for typed in ("sr2027", "", "sr202"):
            status, _ = self.post("/api/auth", {"passphrase": typed}, self.new_browser())
            self.assertEqual(status, 401, typed)


class PageTest(unittest.TestCase):
    def test_page_has_tappable_words_listen_buttons_and_named_levels(self) -> None:
        html = web.INDEX_HTML
        for piece in ("function appendWords(", 'id="word-card"', "/api/define", "playButton(a.en",
                      "function sayAloud(", "Toca cualquier palabra", "B1 · Intermedio", "A2 · Básico"):
            self.assertIn(piece, html)


if __name__ == "__main__":
    unittest.main()
