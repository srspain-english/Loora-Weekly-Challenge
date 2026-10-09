"""HTTP authorization and actual JavaScript rendering checks; synthetic data only."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from http.cookiejar import Cookie
from unittest import mock
from uuid import uuid4

import store
import tutor
import web
from helpers import ServerTestCase, fake_reply, fake_report_response


class StudentAccessTest(ServerTestCase, unittest.TestCase):
    def setUp(self):
        web.ACCESS_PASSPHRASE = "synthetic-shared-gate"
        web.SESSIONS.clear()
        self.opener = self.new_browser()

    def sign_in(self, browser=None):
        status, _ = self.post("/api/auth", {"passphrase": "synthetic-shared-gate"}, browser)
        self.assertEqual(status, 200)

    def student(self, name):
        code = "synthetic-" + uuid4().hex
        student_id = store.create_student(name, access_code=code)
        browser = self.new_browser()
        self.sign_in(browser)
        self.identify(browser, access_code=code)
        return browser, student_id, code

    def start(self, browser):
        with mock.patch.object(web.client.messages, "create", return_value=fake_reply("Synthetic opening")):
            status, result = self.post("/api/start", {"mode": "free"}, browser)
        self.assertEqual(status, 200, result)
        return result["call_id"]

    def test_shared_gate_without_student_sign_in_cannot_access_student_endpoints(self):
        self.sign_in()
        for path in ("start", "resume", "end", "message", "help", "abandon", "feedback", "signout"):
            with self.subTest(path=path):
                status, _ = self.post("/api/" + path, {"text": "Synthetic text"})
                self.assertEqual(status, 401)

    def test_identity_selection_requires_the_shared_gate(self):
        status, _ = self.post("/api/identity", {"new_student": True, "student": "Synthetic guest"})
        self.assertEqual(status, 401)

    def test_foreign_open_class_cannot_be_reported_or_modified(self):
        owner, student_id, _ = self.student("Synthetic A")
        other, _, _ = self.student("Synthetic B")
        call_id = self.start(owner)
        before = store.get_call(call_id)
        with mock.patch.object(web.client.messages, "create") as model, \
                mock.patch.object(tutor, "save_student") as save_memory:
            status, result = self.post("/api/end", {"call_id": call_id}, other)
        self.assertEqual(status, 404)
        self.assertNotIn("report", result)
        model.assert_not_called()
        save_memory.assert_not_called()
        self.assertEqual(store.get_call(call_id), before)
        self.assertFalse((tutor.STUDENTS_DIR / f"{student_id}.json").exists())

    def test_foreign_finished_recap_is_not_returned(self):
        owner, _, _ = self.student("Synthetic A")
        other, _, _ = self.student("Synthetic B")
        call_id = self.start(owner)
        store.finish_call(call_id, {"what_we_did": "Synthetic A private recap"})
        with mock.patch.object(web.client.messages, "create") as model:
            status, result = self.post("/api/end", {"call_id": call_id}, other)
        self.assertEqual(status, 404)
        self.assertNotIn("Synthetic A private recap", json.dumps(result))
        model.assert_not_called()

    def test_owner_can_generate_and_reread_a_report_after_signing_in_again(self):
        owner, _, code = self.student("Synthetic owner")
        call_id = self.start(owner)
        with mock.patch.object(web.client.messages, "create", return_value=fake_report_response()) as model:
            status, first = self.post("/api/end", {"call_id": call_id}, owner)
            fresh = self.new_browser()
            self.sign_in(fresh)
            self.identify(fresh, access_code=code)
            status_again, second = self.post("/api/end", {"call_id": call_id}, fresh)
        self.assertEqual((status, status_again), (200, 200))
        self.assertEqual(first, second)
        self.assertEqual(model.call_count, 1)

    def test_foreign_and_missing_class_ids_have_the_same_error(self):
        owner, _, _ = self.student("Synthetic A")
        other, _, _ = self.student("Synthetic B")
        call_id = self.start(owner)
        foreign = self.post("/api/end", {"call_id": call_id}, other)
        missing = self.post("/api/end", {"call_id": "call_synthetic_missing"}, other)
        self.assertEqual(foreign, missing)

    def test_foreign_message_help_and_abandon_are_denied_before_side_effects(self):
        owner, _, _ = self.student("Synthetic A")
        other, _, _ = self.student("Synthetic B")
        call_id = self.start(owner)
        before = store.get_call(call_id)
        with mock.patch.object(web.client.messages, "create") as model, \
                mock.patch.object(web.client.beta.messages, "create") as help_model:
            for path in ("message", "help", "abandon"):
                with self.subTest(path=path):
                    status, _ = self.post("/api/" + path, {"call_id": call_id, "text": "Synthetic text"}, other)
                    self.assertEqual(status, 404)
        model.assert_not_called()
        help_model.assert_not_called()
        self.assertEqual(store.get_call(call_id), before)

    def test_foreign_session_call_pointer_is_not_trusted(self):
        owner, _, _ = self.student("Synthetic A")
        other, _, _ = self.student("Synthetic B")
        call_id = self.start(owner)
        session = next(s for s in web.SESSIONS.values() if s.get("identity_token") == other.identity_token)
        session["call_id"] = call_id
        for path in ("message", "help", "abandon", "end"):
            with self.subTest(path=path):
                self.assertEqual(self.post("/api/" + path, {"text": "Synthetic text"}, other)[0], 404)

    def test_foreign_cached_reply_cannot_be_replayed_or_overwritten(self):
        owner, _, _ = self.student("Synthetic A")
        other, _, _ = self.student("Synthetic B")
        a, b = self.start(owner), self.start(other)
        key = "synthetic-cache-" + uuid4().hex
        private = {"reply": "Synthetic A cached reply"}
        store.remember_response(key, a, private)
        with mock.patch.object(web.client.messages, "create") as model:
            status, result = self.post("/api/message", {"call_id": b, "text": "Synthetic text", "idempotency_key": key}, other)
        self.assertEqual(status, 409)
        self.assertNotIn(private["reply"], json.dumps(result))
        self.assertEqual(store.replayed_response(key, a), private)
        self.assertEqual(store.get_call(b)["turns"], 0)
        model.assert_not_called()

    def test_owner_cannot_replay_a_key_for_a_different_own_class(self):
        owner, _, _ = self.student("Synthetic owner")
        a, b = self.start(owner), self.start(owner)
        key = "synthetic-cache-" + uuid4().hex
        store.remember_response(key, a, {"reply": "Synthetic earlier class"})
        self.assertEqual(self.post("/api/message", {"call_id": b, "idempotency_key": key, "text": "Synthetic"}, owner)[0], 409)

    def test_owner_can_replay_a_cached_reply_without_a_model_call(self):
        owner, _, _ = self.student("Synthetic owner")
        call_id = self.start(owner)
        key = "synthetic-cache-" + uuid4().hex
        payload = {"reply": "Synthetic saved answer"}
        store.remember_response(key, call_id, payload)
        store.finish_call(call_id, {"what_we_did": "Synthetic report"})
        with mock.patch.object(web.client.messages, "create") as model:
            status, result = self.post("/api/message", {"call_id": call_id, "idempotency_key": key}, owner)
        self.assertEqual((status, result), (200, payload))
        model.assert_not_called()

    def test_cache_storage_refuses_a_cross_class_overwrite(self):
        key = "synthetic-cache-" + uuid4().hex
        store.remember_response(key, "synthetic-call-A", {"reply": "Synthetic A"})
        with self.assertRaises(store.ReplayConflict):
            store.remember_response(key, "synthetic-call-B", {"reply": "Synthetic B"})
        self.assertEqual(store.replayed_response(key, "synthetic-call-A"), {"reply": "Synthetic A"})

    def test_resume_uses_the_selected_identity_not_supplied_student_ids(self):
        a, a_id, _ = self.student("Synthetic A")
        b, _, _ = self.student("Synthetic B")
        a_call, b_call = self.start(a), self.start(b)
        status, result = self.post("/api/resume", {"student_id": a_id, "call_id": a_call}, b)
        self.assertEqual(status, 200)
        self.assertEqual(result["open_call"]["call_id"], b_call)

    def test_invalid_code_does_not_fall_back_to_an_existing_identity(self):
        browser, student_id, _ = self.student("Synthetic owner")
        call_id = self.start(browser)
        count = store.connect().execute("SELECT COUNT(*) FROM students").fetchone()[0]
        token = browser.identity_token
        status, result = self.post("/api/identity", {"access_code": "invalid-synthetic-code", "student": "Synthetic other", "new_student": True}, browser)
        self.assertEqual(status, 400)
        self.assertIn("Invalid personal code", result["error"])
        self.assertEqual(browser.identity_token, token)
        self.assertEqual(store.get_student(student_id)["display_name"], "Synthetic owner")
        self.assertEqual(store.connect().execute("SELECT COUNT(*) FROM students").fetchone()[0], count)
        self.assertEqual(self.post("/api/resume", {}, browser)[1]["open_call"]["call_id"], call_id)

    def test_empty_code_requires_an_explicit_new_student_action(self):
        self.sign_in()
        for body in ({"student": "Synthetic"}, {"access_code": "   "}, {"new_student": True}):
            with self.subTest(body=body):
                self.assertEqual(self.post("/api/identity", body)[0], 400)

    def test_malformed_codes_do_not_create_or_select_a_student(self):
        self.sign_in()
        count = store.connect().execute("SELECT COUNT(*) FROM students").fetchone()[0]
        for code in (123, [], {}):
            with self.subTest(code=code):
                status, result = self.post("/api/identity", {"access_code": code,
                                          "student": "Synthetic", "new_student": True})
                self.assertEqual(status, 400)
                self.assertIn("Invalid personal code", result["error"])
        self.assertEqual(store.connect().execute("SELECT COUNT(*) FROM students").fetchone()[0], count)

    def test_resource_endpoints_do_not_silently_accept_personal_codes(self):
        browser, _, _ = self.student("Synthetic owner")
        self.start(browser)
        for path in ("start", "resume", "message", "end", "help", "abandon", "feedback"):
            with self.subTest(path=path):
                status, result = self.post("/api/" + path,
                                          {"access_code": "invalid-synthetic-code", "text": "Synthetic"}, browser)
                self.assertEqual(status, 400)
                self.assertIn("Sign in with personal code", result["error"])

    def test_shared_browser_new_student_rotates_identity_even_for_the_same_name(self):
        self.sign_in()
        self.identify(student="Synthetic same name")
        old_token = self.opener.identity_token
        a = self.start(self.opener)
        self.identify(student="Synthetic same name")
        new_token = self.opener.identity_token
        b = self.start(self.opener)
        self.assertNotEqual(store.get_call(a)["student_id"], store.get_call(b)["student_id"])
        self.opener.identity_token = old_token
        for path in ("resume", "message", "end", "help", "abandon", "feedback", "start"):
            with self.subTest(path=path):
                self.assertEqual(self.post("/api/" + path, {"text": "Synthetic", "call_id": b})[0], 401)
        self.opener.identity_token = new_token
        self.assertEqual(self.post("/api/resume", {})[1]["open_call"]["call_id"], b)

    def test_a_token_from_a_different_browser_session_is_rejected(self):
        a, _, _ = self.student("Synthetic A")
        b, _, _ = self.student("Synthetic B")
        b.identity_token = a.identity_token
        self.assertEqual(self.post("/api/resume", {}, b)[0], 401)

    def test_legacy_student_cookie_does_not_authenticate_or_recover_another_student(self):
        owner, student_id, _ = self.student("Synthetic owner")
        self.start(owner)
        self.sign_in()
        jar = next(h.cookiejar for h in self.opener.handlers if hasattr(h, "cookiejar"))
        jar.set_cookie(Cookie(0, "juno_student", student_id, None, False, "127.0.0.1", False,
                              False, "/", True, False, None, True, None, None, {}))
        self.assertEqual(self.post("/api/resume", {})[0], 401)
        self.identify(student="Synthetic new guest")
        self.assertIsNone(self.post("/api/resume", {})[1]["open_call"])

    def test_signout_invalidates_identity_and_the_old_tab_token(self):
        owner, _, _ = self.student("Synthetic owner")
        token = owner.identity_token
        self.assertEqual(self.post("/api/signout", {}, owner)[0], 200)
        owner.identity_token = token
        self.assertEqual(self.post("/api/resume", {}, owner)[0], 401)

    def test_feedback_uses_verified_identity_instead_of_a_supplied_name(self):
        owner, _, _ = self.student("Synthetic owner")
        status, _ = self.post("/api/feedback", {"text": "Synthetic feedback", "student": "Synthetic impersonated"}, owner)
        self.assertEqual(status, 200)
        entry = json.loads(web.FEEDBACK_PATH.read_text().splitlines()[-1])
        self.assertEqual(entry["student"], "Synthetic owner")


class ReportRenderingTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required to execute report JavaScript")
    def test_all_report_text_fields_render_as_text_not_executable_markup(self):
        payload = '</p><img src=x onerror="globalThis.hacked=true"><svg onload="alert(1)"></svg>&\"\''
        report = {
            "what_we_did": payload,
            "corrections": [{k: payload for k in ("tier", "said", "better", "note")}],
            "word_traps": [{k: payload for k in ("you_said", "problem", "we_say")}],
            "pronunciation": [{k: payload for k in ("word", "ipa", "watch_for")}],
            "vocabulary_learned": [{k: payload for k in ("term", "meaning")}],
            "what_went_well": [payload], "homework": [payload], "next_recommendation": payload,
        }
        script = web.INDEX_HTML.split("function renderRecap(r) {", 1)[1].split("$('end-btn').onclick", 1)[0]
        program = "const el = {innerHTML: ''}; const $ = () => el;\nfunction renderRecap(r) {" + script
        program += "\nrenderRecap(" + json.dumps(report) + "); process.stdout.write(el.innerHTML);"
        result = subprocess.run([shutil.which("node")], input=program, text=True,
                                capture_output=True, check=True, timeout=10,
                                env={**os.environ, "NODE_DISABLE_COMPILE_CACHE": "1"})

        class ParsedReport(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.tags, self.text = [], []

            def handle_starttag(self, tag, attrs):
                self.tags.append((tag, attrs))

            def handle_data(self, data):
                self.text.append(data)

        parsed = ParsedReport()
        parsed.feed(result.stdout)
        self.assertTrue(parsed.tags)
        for tag, attrs in parsed.tags:
            self.assertIn(tag, {"h3", "p", "table", "tr", "td", "b", "br", "span", "ul", "li"})
            self.assertFalse(any(name.startswith("on") for name, _ in attrs))
        self.assertEqual("".join(parsed.text).count(payload), 16)
        self.assertIn("&lt;img", result.stdout)
        self.assertIn("&quot;", result.stdout)
        self.assertIn("&#39;", result.stdout)


if __name__ == "__main__":
    unittest.main()
