"""Real Chromium checks of explicit selection, stale tabs, and report escaping."""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

import store
import web
from helpers import ServerTestCase, fake_reply, fake_report_response


@unittest.skipUnless(sync_playwright and Path('/usr/bin/chromium').is_file(),
                     "Python Playwright and system Chromium are required")
class BrowserSecurityTest(ServerTestCase, unittest.TestCase):
    def setUp(self):
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()
        self.codes = {name: "synthetic-" + uuid4().hex for name in ('A', 'B')}
        self.students = {name: store.create_student('Synthetic ' + name, access_code=code)
                         for name, code in self.codes.items()}
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True)
        self.context = self.browser.new_context()
        # No fonts, cloud services, microphone permissions, or other external requests.
        self.context.route('**/*', lambda route: route.continue_()
                           if route.request.url.startswith(self.base_url + '/') else route.abort())
        self.page_errors = []
        self.addCleanup(self.playwright.stop)
        self.addCleanup(self.browser.close)

    def page(self):
        page = self.context.new_page()
        page.on('pageerror', lambda error: self.page_errors.append(str(error)))
        page.goto(self.base_url, wait_until='load')
        page.locator('#identity-btn').wait_for(state='visible')
        # These tests exercise access security, not speech services.
        page.evaluate("() => { document.getElementById('handsfree-toggle').checked = false; document.getElementById('speak-toggle').checked = false; }")
        return page

    def select(self, page, name):
        page.locator('#student-code').fill(self.codes[name])
        page.locator('#identity-btn').click()
        page.wait_for_function("(name) => document.getElementById('identity-status').textContent.includes('Selected student: ' + name)",
                               arg='Synthetic ' + name)
        self.assertEqual(page.locator('#student').input_value(), 'Synthetic ' + name)

    def test_reload_requires_selection_and_invalid_code_never_shows_old_history(self):
        page = self.page()
        self.select(page, 'A')
        with mock.patch.object(web.client.messages, 'create', return_value=fake_reply('Synthetic A private opening')):
            page.locator('#start-btn').click()
            page.locator('#chat-screen').wait_for(state='visible')
        page.reload(wait_until='load')
        page.locator('#identity-btn').wait_for(state='visible')
        self.assertTrue(page.locator('#start-btn').is_disabled())
        self.assertFalse(page.locator('#resume-notice').is_visible())
        page.locator('#student-code').fill('invalid-synthetic-code')
        page.locator('#identity-btn').click()
        page.wait_for_function("document.getElementById('start-error').textContent.includes('Invalid personal code')")
        self.assertTrue(page.locator('#start-btn').is_disabled())
        self.assertFalse(page.locator('#resume-notice').is_visible())
        self.assertNotIn('Synthetic A private opening', page.locator('body').inner_text())
        self.assertEqual(self.page_errors, [])

    def test_switching_students_clears_visible_data_and_invalidates_the_other_tab(self):
        a = self.page()
        self.select(a, 'A')
        with mock.patch.object(web.client.messages, 'create', return_value=fake_reply('Synthetic A private opening')):
            a.locator('#start-btn').click()
            a.locator('#chat-screen').wait_for(state='visible')
        b = self.page()
        self.select(b, 'B')
        a.wait_for_function('studentIdentityToken === null')
        self.assertEqual(a.locator('#chat-log').text_content(), '')
        self.assertEqual(a.locator('#juno-line').text_content(), '')
        self.assertFalse(a.locator('#chat-screen').is_visible())
        result = a.evaluate("async () => { try { await api('/api/resume', {}); return 'unexpected success'; } catch (e) { return e.message; } }")
        self.assertIn('Select a student', result)
        self.assertFalse(b.locator('#resume-notice').is_visible())
        with mock.patch.object(web.client.messages, 'create', return_value=fake_reply('Synthetic B opening')):
            b.locator('#start-btn').click()
            b.locator('#chat-screen').wait_for(state='visible')
        b_call = b.evaluate('currentCallId')
        self.assertEqual(store.get_call(b_call)['student_id'], self.students['B'])
        with mock.patch.object(web.client.messages, 'create', return_value=fake_report_response()):
            b.locator('#end-btn').click()
            b.locator('#report-screen').wait_for(state='visible')
        b.locator('#again-btn').click()
        b.locator('#switch-student-btn').click()
        b.wait_for_function("studentIdentityToken === null")
        self.assertEqual(b.locator('#recap').text_content(), '')
        self.assertEqual(b.locator('#chat-log').text_content(), '')
        self.assertEqual(b.locator('#student').input_value(), '')
        self.assertTrue(b.locator('#start-btn').is_disabled())
        self.assertEqual(self.page_errors, [])

    def test_server_still_rejects_stale_tabs_without_broadcast_channel(self):
        self.context.add_init_script('window.BroadcastChannel = undefined;')
        a = self.page()
        self.select(a, 'A')
        b = self.page()
        self.select(b, 'B')
        result = a.evaluate("async () => { try { await api('/api/resume', {}); return 'unexpected success'; } catch (e) { return e.message; } }")
        self.assertIn('Select a student', result)
        self.assertEqual(self.page_errors, [])

    def test_an_in_flight_reply_cannot_restore_data_after_another_student_signs_in(self):
        a = self.page()
        self.select(a, 'A')
        with mock.patch.object(web.client.messages, 'create', return_value=fake_reply('Synthetic A previous line')):
            a.locator('#start-btn').click()
            a.locator('#chat-screen').wait_for(state='visible')
        a_call = a.evaluate('currentCallId')
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def delayed_reply(**kwargs):
            started.set()
            if not release.wait(10):
                raise AssertionError('Synthetic delayed reply was not released')
            return fake_reply('Synthetic A private late reply')

        with mock.patch.object(web.client.messages, 'create', side_effect=delayed_reply):
            a.locator('#type-toggle').click()
            a.locator('#msg-input').fill('Synthetic A in-flight message')
            a.locator('#send-btn').click()
            self.assertTrue(started.wait(5))
            b = self.page()
            self.select(b, 'B')
            a.wait_for_function('studentIdentityToken === null')
            release.set()
            a.wait_for_function('sending === false')
        self.assertEqual(a.locator('#juno-line').text_content(), '')
        self.assertEqual(a.locator('#chat-log').text_content(), '')
        self.assertNotIn('Synthetic A private late reply', b.locator('body').inner_text())
        self.assertEqual(store.get_call(a_call)['student_id'], self.students['A'])
        self.assertEqual(self.page_errors, [])

    def test_report_payload_is_literal_text_in_the_browser(self):
        page = self.page()
        payload = '<img src=x onerror="window.reportInjected=true"><svg onload="window.reportInjected=true"></svg>&\"\''
        report = fake_report_response().content[0].input
        report = json.loads(json.dumps(report))
        report['what_we_did'] = payload
        report['corrections'] = [{key: payload for key in ('tier', 'said', 'better', 'note')}]
        page.evaluate('(report) => { renderRecap(report); show("report-screen"); }', report)
        self.assertEqual(page.locator('#recap img, #recap svg, #recap script').count(), 0)
        self.assertIsNone(page.evaluate('window.reportInjected'))
        self.assertEqual(page.locator('#recap').text_content().count(payload), 5)
        self.assertEqual(self.page_errors, [])


    def test_inactivity_clears_visible_history_even_when_signout_is_offline(self):
        page = self.page()
        self.select(page, 'A')
        page.evaluate("() => { document.getElementById('recap').textContent = 'Synthetic private report'; document.getElementById('help-text').textContent = 'Synthetic private help'; document.getElementById('msg-input').value = 'Synthetic private draft'; }")
        self.context.route('**/api/signout', lambda route: route.abort())
        page.evaluate("async () => { lastStudentActivity = Date.now() - STUDENT_IDLE_MS - 1; await checkStudentSession(); }")
        self.assertEqual(page.locator('#recap').text_content(), '')
        self.assertEqual(page.locator('#student').input_value(), '')
        self.assertEqual(page.locator('#help-text').text_content(), '')
        self.assertEqual(page.locator('#msg-input').input_value(), '')
        self.assertIsNone(page.evaluate('studentIdentityToken'))
        self.assertTrue(page.locator('#auth-screen').is_visible())

    def test_session_check_clears_stale_visible_history_without_broadcast_channel(self):
        self.context.add_init_script('window.BroadcastChannel = undefined;')
        a, b = self.page(), self.page()
        self.select(a, 'A')
        a.evaluate("() => { document.getElementById('recap').textContent = 'Synthetic private report'; }")
        self.select(b, 'B')
        a.evaluate('async () => { await checkStudentSession(); }')
        self.assertEqual(a.locator('#recap').text_content(), '')
        self.assertIsNone(a.evaluate('studentIdentityToken'))

    def test_new_student_code_allows_return_after_reload(self):
        page = self.page()
        page.locator('#student').fill('Synthetic New Return')
        page.locator('#new-student-btn').click()
        page.wait_for_function("() => document.getElementById('identity-status').textContent.includes('Save your personal code')")
        code = page.evaluate("() => document.getElementById('identity-status').textContent.match(/juno_[A-Za-z0-9_-]{43}/)[0]")
        page.reload(wait_until='load')
        page.locator('#student-code').fill(code)
        page.locator('#identity-btn').click()
        page.wait_for_function("() => document.getElementById('student').value === 'Synthetic New Return' && studentIdentityToken !== null")
        self.assertTrue(page.locator('#student').evaluate('(node) => node.readOnly'))


    def test_explicit_signout_clears_report_and_shared_passphrase_even_offline(self):
        web.ACCESS_PASSPHRASE = 'synthetic-classroom-gate'
        page = self.context.new_page()
        page.goto(self.base_url, wait_until='load')
        page.locator('#passphrase').fill('synthetic-classroom-gate')
        page.locator('#auth-btn').click()
        page.locator('#identity-btn').wait_for(state='visible')
        self.assertEqual(page.locator('#passphrase').input_value(), '')
        self.select(page, 'A')
        page.evaluate("() => { document.getElementById('recap').textContent = 'Synthetic private report'; show('report-screen'); }")
        self.assertTrue(page.locator('#switch-student-btn').is_visible())
        self.context.route('**/api/signout', lambda route: route.abort())
        page.locator('#switch-student-btn').click()
        page.locator('#auth-screen').wait_for(state='visible')
        self.assertEqual(page.locator('#recap').text_content(), '')
        self.assertEqual(page.locator('#passphrase').input_value(), '')
        self.assertIsNone(page.evaluate('studentIdentityToken'))


if __name__ == '__main__':
    unittest.main()
