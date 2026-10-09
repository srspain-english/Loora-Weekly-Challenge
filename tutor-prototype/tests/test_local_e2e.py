"""Real process and browser journeys with synthetic-only local mock storage."""
from __future__ import annotations
import hashlib
import json
import os
import selectors
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from unittest import mock
import dev_mock
import local_mock
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
APP = Path(__file__).resolve().parent.parent


def clean_environment():
    return {name: os.environ[name] for name in os.environ
            if not name.startswith(('JUNO_', 'ANTHROPIC_', 'RENDER', 'RAILWAY_', 'VERCEL', 'FLY_'))
            and name not in ('PORT', 'DYNO', 'K_SERVICE', 'WEBSITE_INSTANCE_ID')}


class LocalProcess:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='juno-mock-')
        self.path = Path(self.tmp.name)
        self.process = None
        self.log = None

    def start(self, port=0):
        self.log = tempfile.TemporaryFile(mode='w+')
        self.process = subprocess.Popen(
            [sys.executable, '-u', '-B', str(APP / 'local_mock.py'), '--allow-local-mock',
             '--data-dir', str(self.path), '--port', str(port)],
            cwd=APP, env=clean_environment(), stdout=subprocess.PIPE,
            stderr=self.log, text=True)
        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                if not selector.select(timeout=.2):
                    continue
                line = self.process.stdout.readline()
                if line.startswith('JUNO_LOCAL_MOCK_READY '):
                    ready = json.loads(line.split(' ', 1)[1])
                    self.url = ready['url']
                    self.port = int(self.url.rsplit(':', 1)[1])
                    self.fixtures = json.loads(Path(ready['fixtures_file']).read_text())
                    if ready['api_calls_enabled'] is not False:
                        raise AssertionError('Local mock unexpectedly enables API calls')
                    return self
                if not line and self.process.poll() is not None:
                    raise AssertionError('Local mock exited before readiness')
            raise AssertionError('Local mock did not become ready')
        finally:
            selector.close()

    def stop(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process.stdout.close()
            self.process = None
        if self.log:
            self.log.close()
            self.log = None

    def close(self):
        self.stop()
        self.tmp.cleanup()

    def browser(self):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def post(self, browser, path, body=None):
        token = getattr(browser, 'identity_token', None)
        req = urllib.request.Request(self.url + path, method='POST',
                                     data=json.dumps(body or {}).encode(),
                                     headers={'Content-Type': 'application/json',
                                              **({'X-Juno-Identity': token} if token else {})})
        try:
            with browser.open(req, timeout=5) as response:
                status, result = response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            status, result = exc.code, json.loads(exc.read())
        if status == 200 and path == '/api/identity':
            browser.identity_token = result['identity_token']
        if status == 200 and path == '/api/signout':
            browser.identity_token = None
        return status, result

    def sign_in(self, browser, label):
        if self.post(browser, '/api/auth', {'passphrase': self.fixtures['gate']})[0] != 200:
            raise AssertionError('Synthetic gate sign-in failed')
        status, result = self.post(browser, '/api/identity', {'access_code': self.fixtures['students'][label]['code']})
        if status != 200:
            raise AssertionError('Synthetic student sign-in failed')
        return result

    def memory(self, label):
        sid = self.fixtures['students'][label]['student_id']
        return json.loads((self.path / 'students' / (sid + '.json')).read_text())


class MockSafeguardsTest(unittest.TestCase):
    def test_production_or_detached_branch_is_refused(self):
        for branch in ('claude/mila-platform-feasibility-ogq7xo', 'main', ''):
            with self.subTest(branch=branch), mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(dev_mock.subprocess, 'check_output', return_value=branch):
                with self.assertRaises(dev_mock.MockConfigurationError):
                    dev_mock.require_local_environment()

    def test_deployment_markers_and_any_api_credentials_are_refused(self):
        for name in ('PORT', 'RENDER', 'RENDER_SERVICE_ID', 'DYNO', 'K_SERVICE', 'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN'):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: 'synthetic-only'}, clear=True):
                with self.assertRaises(dev_mock.MockConfigurationError):
                    dev_mock.require_local_environment()

    def test_unverifiable_branch_is_refused(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(dev_mock.subprocess, 'check_output', side_effect=OSError()):
            with self.assertRaises(dev_mock.MockConfigurationError):
                dev_mock.require_local_environment()

    def test_unmarked_or_nonempty_storage_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='juno-mock-') as directory, mock.patch.dict(os.environ, {}, clear=True):
            path = Path(directory)
            original = path / 'synthetic-existing-record.json'
            original.write_text('synthetic preserved content')
            with self.assertRaises(dev_mock.MockConfigurationError):
                local_mock.prepare_storage(path)
            self.assertEqual(original.read_text(), 'synthetic preserved content')
            self.assertFalse((path / dev_mock.MARKER).exists())
            with self.assertRaises(dev_mock.MockConfigurationError):
                dev_mock.LocalMockAnthropic(path)

    def test_symlink_storage_and_foreign_markers_are_refused(self):
        with tempfile.TemporaryDirectory(prefix='juno-mock-') as directory, mock.patch.dict(os.environ, {}, clear=True):
            path = local_mock.prepare_storage(Path(directory))
            link = path / 'synthetic-link'
            link.symlink_to(path / dev_mock.MARKER)
            with self.assertRaises(dev_mock.MockConfigurationError):
                dev_mock.LocalMockAnthropic(path)
            link.unlink()
            (path / dev_mock.MARKER).write_text('{}')
            with self.assertRaises(dev_mock.MockConfigurationError):
                dev_mock.LocalMockAnthropic(path)

    def test_client_supports_required_shapes_and_refuses_unknown_requests(self):
        with tempfile.TemporaryDirectory(prefix='juno-mock-') as directory, mock.patch.dict(os.environ, {}, clear=True):
            path = local_mock.prepare_storage(Path(directory))
            client = dev_mock.LocalMockAnthropic(path)
            request = {'model': 'synthetic-model', 'max_tokens': 10, 'system': 'Synthetic system',
                       'messages': [{'role': 'user', 'content': 'Synthetic answer'}]}
            self.assertIn('Local mock', client.messages.create(**request).content[0].text)
            self.assertIn('simulada', client.beta.messages.create(**request).content[0].text)
            report = client.messages.create(**request, tools=[{'name': 'submit_session_report'}], tool_choice={'type': 'tool', 'name': 'submit_session_report'})
            self.assertEqual(report.content[0].type, 'tool_use')
            for options in ({'api_key': 'synthetic'}, {'tools': [{'name': 'unknown'}]}, {'messages': []}):
                with self.subTest(options=options), self.assertRaises(dev_mock.MockConfigurationError):
                    client.messages.create(**{**request, **options})
            with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'synthetic-disallowed'}):
                with self.assertRaises(dev_mock.MockConfigurationError):
                    client.messages.create(**request)

    def test_network_connections_are_blocked(self):
        with mock.patch.object(socket.socket, 'connect', local_mock.no_outbound_connections), mock.patch.object(socket.socket, 'connect_ex', local_mock.no_outbound_connections):
            with socket.socket() as sock:
                for method in (sock.connect, sock.connect_ex):
                    with self.assertRaises(dev_mock.MockConfigurationError):
                        method(('127.0.0.1', 443))

    def test_launcher_requires_explicit_opt_in_and_rejects_inherited_config(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                local_mock.main([])
        with mock.patch.dict(os.environ, {'JUNO_DATA_DIR': '/synthetic-disallowed'}, clear=True), mock.patch('builtins.print'):
            self.assertEqual(local_mock.main(['--allow-local-mock']), 2)

    def test_production_entrypoints_never_import_or_select_the_mock(self):
        import ast
        for filename in ('web.py', 'tutor.py'):
            tree = ast.parse((APP / filename).read_text())
            modules = [name.name for node in ast.walk(tree) if isinstance(node, ast.Import) for name in node.names]
            modules += [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            self.assertNotIn('dev_mock', modules)
            self.assertNotIn('local_mock', modules)


    def test_marker_symlink_is_rejected_before_reading_it(self):
        with tempfile.TemporaryDirectory(prefix='juno-mock-') as directory, mock.patch.dict(os.environ, {}, clear=True):
            path = local_mock.prepare_storage(Path(directory))
            marker = path / dev_mock.MARKER
            marker.unlink()
            marker.symlink_to(path / 'synthetic-target')
            with mock.patch.object(Path, 'read_text', side_effect=AssertionError('No linked file should be read')):
                with self.assertRaises(dev_mock.MockConfigurationError):
                    dev_mock.require_mock_storage(path)

    def test_mock_memory_response_requires_actual_student_memory(self):
        with tempfile.TemporaryDirectory(prefix='juno-mock-') as directory, mock.patch.dict(os.environ, {}, clear=True):
            client = dev_mock.LocalMockAnthropic(local_mock.prepare_storage(Path(directory)))
            request = {'model': 'synthetic-model', 'max_tokens': 10,
                       'messages': [{'role': 'user', 'content': '(the call has just connected — open it)'}]}
            marker = 'STUDENT MEMORY (use this to open the call — reference something concrete):'
            empty = 'General follow up example\n' + marker + '\n' + json.dumps({'vocab_acquired_log': []})
            self.assertNotIn('saved learning memory', client.create(**request, system=empty).content[0].text)
            learned = marker + '\n' + json.dumps({'vocab_acquired_log': ['follow up — synthetic meaning']})
            self.assertIn('saved learning memory', client.create(**request, system=learned).content[0].text)


class LocalClassroomTest(unittest.TestCase):
    def setUp(self):
        self.local = LocalProcess()
        self.addCleanup(self.local.close)
        self.local.start()

    def test_complete_journey_return_and_recovered_learning_memory(self):
        a = self.local.browser()
        self.local.sign_in(a, 'A')
        status, lesson = self.local.post(a, '/api/start', {'mode': 'free'})
        self.assertEqual(status, 200)
        self.assertNotIn('saved learning memory', lesson['reply'])
        call = lesson['call_id']
        self.assertEqual(self.local.post(a, '/api/message', {'call_id': call, 'text': 'Synthetic work task', 'idempotency_key': 'synthetic-journey-turn'})[0], 200)
        self.assertEqual(self.local.post(a, '/api/help', {'call_id': call})[0], 200)
        status, recap = self.local.post(a, '/api/end', {'call_id': call})
        self.assertEqual(status, 200)
        self.assertIn('Synthetic classroom', recap['report']['what_we_did'])
        memory = self.local.memory('A')
        self.assertEqual(memory['vocab_acquired_log'], ['follow up — contact someone again'])
        self.assertEqual(len(memory['session_history']), 1)
        self.assertEqual(self.local.post(a, '/api/signout')[0], 200)
        self.assertEqual(self.local.post(a, '/api/end', {'call_id': call})[0], 401)
        returning = self.local.browser()
        self.local.sign_in(returning, 'A')
        self.assertEqual(self.local.post(returning, '/api/end', {'call_id': call}), (200, recap))
        status, next_lesson = self.local.post(returning, '/api/start', {'mode': 'free'})
        self.assertEqual(status, 200)
        self.assertIn('saved learning memory includes follow up', next_lesson['reply'])
        self.local.post(returning, '/api/message', {'text': 'Synthetic continuation', 'call_id': next_lesson['call_id']})
        self.local.post(returning, '/api/signout')
        self.local.sign_in(returning, 'A')
        status, resume = self.local.post(returning, '/api/resume')
        self.assertEqual(status, 200)
        self.assertEqual(resume['open_call']['call_id'], next_lesson['call_id'])
        self.assertIn('Synthetic continuation', str(resume['open_call']['transcript']))

    def test_existing_student_provisioning_preserves_historical_data(self):
        existing = self.local.fixtures['students']['existing']
        sid = existing['student_id']
        with sqlite3.connect(self.local.path / 'juno.db') as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(row) for row in conn.execute('SELECT * FROM calls WHERE student_id = ? ORDER BY call_id', (sid,))]
        self.assertEqual(rows, existing['historical_calls'])
        memory_path = self.local.path / 'students' / (sid + '.json')
        self.assertEqual(hashlib.sha256(memory_path.read_bytes()).hexdigest(), existing['memory_sha256'])
        browser = self.local.browser()
        self.local.sign_in(browser, 'existing')
        status, resume = self.local.post(browser, '/api/resume')
        self.assertEqual(status, 200)
        self.assertEqual(resume['open_call']['call_id'], existing['open_call'])
        status, report = self.local.post(browser, '/api/end', {'call_id': existing['finished_call']})
        self.assertEqual(status, 200)
        self.assertIn('Historical synthetic recap', report['report']['what_we_did'])
        self.assertEqual(hashlib.sha256(memory_path.read_bytes()).hexdigest(), existing['memory_sha256'])

    def test_cross_student_denial_cached_reply_and_shared_browser_handoff(self):
        browser = self.local.browser()
        self.local.sign_in(browser, 'A')
        _, lesson = self.local.post(browser, '/api/start', {'mode': 'free'})
        call = lesson['call_id']
        self.local.post(browser, '/api/message', {'call_id': call, 'text': 'Synthetic A private', 'idempotency_key': 'synthetic-a-cache'})
        self.local.post(browser, '/api/end', {'call_id': call})
        old_token = browser.identity_token
        self.local.sign_in(browser, 'B')
        new_token = browser.identity_token
        for endpoint in ('message', 'end', 'help', 'abandon'):
            self.assertEqual(self.local.post(browser, '/api/' + endpoint, {'call_id': call, 'text': 'Synthetic attack'})[0], 404)
        _, b = self.local.post(browser, '/api/start', {'mode': 'free'})
        self.assertEqual(self.local.post(browser, '/api/message', {'call_id': b['call_id'], 'text': 'Synthetic B', 'idempotency_key': 'synthetic-a-cache'})[0], 409)
        browser.identity_token = old_token
        self.assertEqual(self.local.post(browser, '/api/resume')[0], 401)
        browser.identity_token = new_token
        _, resume = self.local.post(browser, '/api/resume')
        self.assertEqual(resume['open_call']['call_id'], b['call_id'])
        self.assertNotIn('Synthetic A private', str(resume))
        self.assertFalse((self.local.path / 'students' / (self.local.fixtures['students']['B']['student_id'] + '.json')).exists())

    def test_process_restart_retains_identity_recaps_memory_and_open_class(self):
        browser = self.local.browser()
        self.local.sign_in(browser, 'A')
        _, first = self.local.post(browser, '/api/start', {'mode': 'free'})
        _, report = self.local.post(browser, '/api/end', {'call_id': first['call_id']})
        before_memory = self.local.memory('A')
        _, unfinished = self.local.post(browser, '/api/start', {'mode': 'free'})
        self.local.post(browser, '/api/message', {'call_id': unfinished['call_id'], 'text': 'Synthetic saved before restart', 'idempotency_key': 'synthetic-restart-key'})
        old_fixtures, old_port = self.local.fixtures, self.local.port
        self.local.stop()
        self.local.start(port=old_port)
        self.assertEqual(self.local.fixtures, old_fixtures)
        self.assertEqual(self.local.post(browser, '/api/resume')[0], 401)
        self.local.sign_in(browser, 'A')
        status, resume = self.local.post(browser, '/api/resume')
        self.assertEqual(status, 200)
        self.assertEqual(resume['open_call']['call_id'], unfinished['call_id'])
        self.assertIn('Synthetic saved before restart', str(resume))
        self.assertEqual(self.local.memory('A'), before_memory)
        self.assertEqual(self.local.post(browser, '/api/end', {'call_id': first['call_id']}), (200, report))
        self.assertEqual(self.local.post(browser, '/api/message', {'call_id': unfinished['call_id'], 'text': 'Synthetic resume after restart'})[0], 200)
        self.assertEqual(self.local.post(browser, '/api/end', {'call_id': unfinished['call_id']})[0], 200)
        self.assertEqual(len(self.local.memory('A')['session_history']), 2)


    def test_launcher_refuses_second_process_on_same_storage(self):
        result = subprocess.run([sys.executable, '-B', str(APP / 'local_mock.py'), '--allow-local-mock', '--data-dir', str(self.local.path)],
                                cwd=APP, env=clean_environment(), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Another local mock', result.stdout)
        self.assertNotIn(self.local.fixtures['gate'], result.stdout + result.stderr)

    def test_failed_login_limit_survives_actual_process_restart(self):
        browser = self.local.browser()
        for _ in range(10):
            self.assertEqual(self.local.post(browser, '/api/auth', {'passphrase': 'synthetic-wrong-gate'})[0], 401)
        port = self.local.port
        self.local.stop()
        self.local.start(port=port)
        browser = self.local.browser()
        self.assertEqual(self.local.post(browser, '/api/auth', {'passphrase': self.local.fixtures['gate']})[0], 429)


@unittest.skipUnless(sync_playwright and Path('/usr/bin/chromium').is_file(),
                     'Python Playwright and system Chromium are required')
class LocalBrowserClassroomTest(unittest.TestCase):
    def setUp(self):
        self.local = LocalProcess()
        self.addCleanup(self.local.close)
        self.local.start()
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True)
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context()
        self.context.set_default_timeout(15000)
        self.context.route('**/*', lambda route: route.continue_()
                           if route.request.url.startswith(self.local.url + '/') else route.abort())
        self.errors = []

    def page(self):
        page = self.context.new_page()
        page.on('pageerror', lambda error: self.errors.append(str(error)))
        page.goto(self.local.url, wait_until='load')
        page.wait_for_function("document.getElementById('lede').textContent.startsWith('A live practice')")
        return page

    def sign_in(self, page, label, gate=True):
        if gate and page.locator('#auth-screen').is_visible():
            page.locator('#passphrase').fill(self.local.fixtures['gate'])
            page.locator('#auth-btn').click()
        page.locator('#identity-btn').wait_for(state='visible')
        page.locator('#student-code').fill(self.local.fixtures['students'][label]['code'])
        page.locator('#identity-btn').click()
        page.wait_for_function('studentIdentityToken !== null')
        page.wait_for_function("!document.getElementById('identity-btn').disabled")
        page.evaluate("() => { document.getElementById('handsfree-toggle').checked = false; document.getElementById('speak-toggle').checked = false; }")

    def start_lesson(self, page):
        page.locator('#start-btn').click()
        page.locator('#chat-screen').wait_for(state='visible')
        page.locator('#type-toggle').click()
        return page.evaluate('currentCallId')

    def send(self, page, text):
        page.locator('#msg-input').fill(text)
        page.locator('#send-btn').click()
        page.wait_for_function('sending === false')
        self.assertEqual(page.locator('#chat-error').text_content(), '')

    def test_real_browser_classroom_recap_return_resume_and_memory(self):
        page = self.page()
        self.assertIn('LOCAL SYNTHETIC MOCK', page.locator('body').inner_text())
        self.sign_in(page, 'A')
        first = self.start_lesson(page)
        self.send(page, 'Synthetic browser classroom answer')
        page.locator('#help-btn').click()
        page.wait_for_function("document.getElementById('help-text').textContent.includes('Ayuda simulada')")
        page.locator('#end-btn').click()
        page.locator('#report-screen').wait_for(state='visible')
        self.assertIn('follow up', page.locator('#recap').inner_text())
        self.assertEqual(self.local.memory('A')['vocab_acquired_log'], ['follow up — contact someone again'])
        page.locator('#switch-student-btn').click()
        page.locator('#auth-screen').wait_for(state='visible')
        self.assertEqual(page.locator('#recap').text_content(), '')
        self.sign_in(page, 'A')
        second = self.start_lesson(page)
        self.assertNotEqual(first, second)
        self.assertIn('saved learning memory includes follow up', page.locator('#juno-line').text_content())
        self.send(page, 'Synthetic browser unfinished lesson')
        page.locator('#switch-student-btn').click()
        page.locator('#auth-screen').wait_for(state='visible')
        self.sign_in(page, 'A')
        page.locator('#resume-notice').wait_for(state='visible')
        page.locator('#resume-btn').click()
        page.locator('#chat-screen').wait_for(state='visible')
        self.assertEqual(page.evaluate('currentCallId'), second)
        self.assertIn('Synthetic browser unfinished lesson', page.locator('#chat-log').text_content())
        page.locator('#end-btn').click()
        page.locator('#report-screen').wait_for(state='visible')
        self.assertEqual(len(self.local.memory('A')['session_history']), 2)
        self.assertEqual(self.errors, [])

    def test_real_browser_shared_handoff_clears_previous_student_and_denies_recap(self):
        a = self.page()
        self.sign_in(a, 'A')
        a_call = self.start_lesson(a)
        self.send(a, 'Synthetic A private browser transcript')
        a.locator('#end-btn').click()
        a.locator('#report-screen').wait_for(state='visible')
        b = self.page()
        self.sign_in(b, 'B')
        a.wait_for_function('studentIdentityToken === null')
        self.assertEqual(a.locator('#recap').text_content(), '')
        self.assertEqual(a.locator('#chat-log').text_content(), '')
        denial = b.evaluate("async (call) => { try { await api('/api/end', {call_id: call}); return 'unexpected success'; } catch (e) { return e.message; } }", a_call)
        self.assertIn('Class not found', denial)
        self.assertFalse(b.locator('#resume-notice').is_visible())
        b_call = self.start_lesson(b)
        self.assertNotEqual(a_call, b_call)
        self.assertNotIn('Synthetic A private browser transcript', b.locator('body').inner_text())
        self.assertEqual(self.errors, [])

    def test_real_browser_recovers_after_actual_server_restart(self):
        page = self.page()
        self.sign_in(page, 'A')
        call = self.start_lesson(page)
        self.send(page, 'Synthetic browser persisted across restart')
        port = self.local.port
        self.local.stop()
        self.local.start(port=port)
        page.reload(wait_until='load')
        page.locator('#auth-screen').wait_for(state='visible')
        self.sign_in(page, 'A')
        page.locator('#resume-notice').wait_for(state='visible')
        page.locator('#resume-btn').click()
        page.locator('#chat-screen').wait_for(state='visible')
        self.assertEqual(page.evaluate('currentCallId'), call)
        self.assertIn('Synthetic browser persisted across restart', page.locator('#chat-log').text_content())
        page.locator('#end-btn').click()
        page.locator('#report-screen').wait_for(state='visible')
        self.assertIn('Synthetic classroom', page.locator('#recap').text_content())
        self.assertEqual(self.errors, [])
