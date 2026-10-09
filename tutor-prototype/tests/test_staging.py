"""Synthetic Render metadata/mount simulation, real local TLS, backup and restart tests."""
from __future__ import annotations
import concurrent.futures
import fcntl
import http.client
import json
import os
import secrets
import selectors
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
import staging_config as policy
import staging_data
import staging_server
from staging_mock import StagingMockAnthropic

APP = Path(__file__).resolve().parent.parent
BOOTSTRAP = "import sys; from pathlib import Path; import staging_config as c; c.DISK_ROOT=Path(sys.argv[1]); c.disk_is_mounted=lambda p:p==c.DISK_ROOT; import staging_server; sys.exit(staging_server.main())"


def environment(root, port):
    commit = subprocess.check_output(['git', '-C', str(APP), 'rev-parse', 'HEAD'], text=True).strip()
    host = 'juno-staging-' + uuid.uuid4().hex + '.onrender.com'
    service = 'srv-' + uuid.uuid4().hex
    return {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHONPATH': os.environ.get('PYTHONPATH', ''),
            'PYTHONDONTWRITEBYTECODE': '1', 'RENDER': 'true', 'RENDER_SERVICE_ID': service,
            'RENDER_SERVICE_NAME': 'juno-staging-synthetic', 'RENDER_EXTERNAL_URL': 'https://' + host,
            'RENDER_GIT_BRANCH': 'juno-v2-development', 'RENDER_GIT_COMMIT': commit, 'PORT': str(port),
            'JUNO_ENVIRONMENT': 'staging', 'JUNO_MODEL_MODE': 'mock', 'JUNO_STAGING_SYNTHETIC_ONLY': 'true',
            'JUNO_STAGING_SERVICE_ID': service, 'JUNO_STAGING_HOSTNAME': host,
            'JUNO_STAGING_INSTANCE_ID': str(uuid.uuid4()), 'JUNO_STAGING_COMMIT': commit,
            'JUNO_STAGING_INITIALIZE': 'true', 'JUNO_ACCESS_PASSPHRASE': 'staging_' + secrets.token_urlsafe(32),
            'JUNO_DATA_DIR': str(root / 'juno-staging')}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='juno-stage-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = environment(self.root, 10000)
        self.patchers = [mock.patch.dict(os.environ, self.env, clear=True),
                         mock.patch.object(policy, 'DISK_ROOT', self.root),
                         mock.patch.object(policy, 'disk_is_mounted', return_value=True)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_required_settings_fail_closed_when_missing(self):
        names = list(policy.JUNO_NAMES) + ['RENDER', 'RENDER_SERVICE_ID', 'RENDER_SERVICE_NAME', 'RENDER_EXTERNAL_URL', 'RENDER_GIT_BRANCH', 'RENDER_GIT_COMMIT', 'PORT']
        for name in names:
            with self.subTest(name=name), mock.patch.dict(os.environ, self.env, clear=True):
                del os.environ[name]
                with self.assertRaises(policy.StagingError):
                    policy.load_config()

    def test_production_modes_service_branch_commit_and_origin_are_rejected(self):
        for key, value in [('JUNO_ENVIRONMENT', 'production'), ('JUNO_MODEL_MODE', 'live'),
                           ('RENDER_GIT_BRANCH', 'claude/mila-platform-feasibility-ogq7xo'),
                           ('RENDER_SERVICE_ID', 'srv-otherproduction'), ('RENDER_SERVICE_NAME', 'production-juno'),
                           ('RENDER_EXTERNAL_URL', 'https://synthetic-production.invalid'),
                           ('RENDER_GIT_COMMIT', '0' * 40), ('JUNO_STAGING_COMMIT', '0' * 40),
                           ('JUNO_ACCESS_PASSPHRASE', 'human-chosen-weak-code'), ('PORT', '0')]:
            with self.subTest(key=key), mock.patch.dict(os.environ, {key: value}):
                with self.assertRaises(policy.StagingError):
                    policy.load_config()

    def test_credentials_external_paths_and_endpoint_overrides_are_rejected(self):
        for name in ['ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'DATABASE_URL', 'RENDER_API_KEY', 'GH_TOKEN', 'AWS_SECRET_ACCESS_KEY', 'HTTPS_PROXY', 'JUNO_DB_PATH']:
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: 'synthetic-disallowed'}):
                with self.assertRaises(policy.StagingError):
                    policy.load_config()
        with mock.patch.dict(os.environ, {'JUNO_DATA_DIR': str(APP / 'data')}):
            with self.assertRaises(policy.StagingError):
                policy.load_config()

    def test_missing_mount_and_unknown_provenance_are_rejected(self):
        with mock.patch.object(policy, 'disk_is_mounted', return_value=False):
            with self.assertRaises(policy.StagingError):
                policy.load_config()
        config = policy.load_config()
        config.data_dir.mkdir()
        original = config.data_dir / 'synthetic-unmarked.json'
        original.write_text('Synthetic preserved record')
        with self.assertRaises(policy.StagingError):
            config.prepare_storage()
        self.assertEqual(original.read_text(), 'Synthetic preserved record')

    def test_initialization_and_service_owned_markers(self):
        config = policy.load_config()
        config.prepare_storage()
        self.assertEqual(config.data_dir.stat().st_mode & 0o777, 0o700)
        with mock.patch.dict(os.environ, {'JUNO_STAGING_INITIALIZE': 'false'}):
            policy.load_config().verify_storage()
        marker = config.data_dir / policy.MARKER
        original = marker.read_text()
        marker.write_text('{}')
        with self.assertRaises(policy.StagingError):
            config.prepare_storage()
        marker.write_text(original)
        marker.unlink()
        with mock.patch.dict(os.environ, {'JUNO_STAGING_INITIALIZE': 'false'}):
            with self.assertRaises(policy.StagingError):
                policy.load_config().prepare_storage()

    def test_symlinks_are_rejected_before_storage_is_read(self):
        config = policy.load_config()
        config.prepare_storage()
        marker = config.data_dir / policy.MARKER
        marker.unlink()
        marker.symlink_to(self.root / 'synthetic-target')
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('Linked data must not be read')):
            with self.assertRaises(policy.StagingError):
                config.verify_storage()

    def test_mock_has_no_sdk_fallback_and_detects_configuration_change(self):
        config = policy.load_config()
        config.prepare_storage()
        client = StagingMockAnthropic(config)
        request = {'model': 'synthetic-model', 'max_tokens': 10, 'system': 'Synthetic system',
                   'messages': [{'role': 'user', 'content': 'Synthetic answer'}]}
        self.assertIn('Staging mock', client.messages.create(**request).content[0].text)
        self.assertEqual(client.messages.create(**request).usage.input_tokens, 0)
        with self.assertRaises(policy.StagingError):
            client.messages.create(**request, api_key='synthetic')
        with mock.patch.dict(os.environ, {'JUNO_MODEL_MODE': 'live'}):
            with self.assertRaises(policy.StagingError):
                client.messages.create(**request)

    def test_outbound_tcp_udp_and_sdk_connections_are_denied(self):
        with mock.patch.object(socket.socket, 'connect', staging_server.deny_outbound), mock.patch.object(socket.socket, 'sendto', staging_server.deny_outbound):
            with socket.socket() as sock:
                with self.assertRaises(policy.StagingError):
                    sock.connect(('127.0.0.1', 443))
            with socket.socket(type=socket.SOCK_DGRAM) as sock:
                with self.assertRaises(policy.StagingError):
                    sock.sendto(b'Synthetic', ('127.0.0.1', 443))

    def test_all_application_requests_fail_closed_after_configuration_change(self):
        from email.message import Message
        from types import SimpleNamespace
        config = policy.load_config()
        config.prepare_storage()
        handler = staging_server.handler_class(SimpleNamespace(Handler=object), config)()
        handler.headers = Message()
        handler.headers['Host'] = config.hostname
        handler.headers['X-Forwarded-Proto'] = 'https'
        handler._send_json = mock.Mock()
        self.assertTrue(handler.allowed())
        with mock.patch.dict(os.environ, {'JUNO_ENVIRONMENT': 'production'}):
            self.assertFalse(handler.allowed())
            self.assertEqual(handler._send_json.call_args.args[1], 503)
        with mock.patch.object(policy, 'disk_is_mounted', return_value=False):
            self.assertFalse(handler.allowed())
        (config.data_dir / policy.MARKER).write_text('{}')
        self.assertFalse(handler.allowed())

    def test_production_and_local_launchers_remain_independent(self):
        import ast
        for name in ('web.py', 'tutor.py', 'local_mock.py', 'dev_mock.py'):
            modules = []
            for node in ast.walk(ast.parse((APP / name).read_text())):
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    modules.append(node.module)
            self.assertFalse(any(module and module.startswith('staging_') for module in modules))


class StagingProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='juno-stage-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.env = environment(self.root, port)
        self.process = None
        self.log = None
        self.addCleanup(self.stop)
        self.start()
        # Actual TLS termination in a local test proxy, not a Render deployment.
        key, cert = self.root / 'test-key.pem', self.root / 'test-cert.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-keyout', str(key), '-out', str(cert), '-subj', '/CN=127.0.0.1',
                        '-addext', 'subjectAltName=IP:127.0.0.1'], check=True, capture_output=True)
        key.chmod(0o600)
        owner = self
        class Proxy(BaseHTTPRequestHandler):
            def forward(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                headers = {name: value for name, value in self.headers.items() if name.lower() not in ('host', 'x-forwarded-proto', 'content-length', 'connection')}
                headers.update({'Host': owner.env['JUNO_STAGING_HOSTNAME'], 'X-Forwarded-Proto': 'https'})
                conn = http.client.HTTPConnection('127.0.0.1', int(owner.env['PORT']), timeout=5)
                try:
                    conn.request(self.command, self.path, body=body, headers=headers)
                    response = conn.getresponse()
                    payload = response.read()
                    self.send_response(response.status)
                    for name, value in response.getheaders():
                        if name.lower() not in ('connection', 'server', 'date', 'transfer-encoding'):
                            self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    conn.close()
            do_GET = forward
            do_POST = forward
            def log_message(self, *args):
                pass
        self.proxy = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        self.proxy.socket = tls.wrap_socket(self.proxy.socket, server_side=True)
        self.thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_proxy)
        self.url = 'https://127.0.0.1:' + str(self.proxy.server_port)
        self.client_tls = ssl.create_default_context(cafile=str(cert))

    def close_proxy(self):
        self.proxy.shutdown()
        self.proxy.server_close()
        self.thread.join(timeout=5)

    def start(self):
        self.log = tempfile.TemporaryFile(mode='w+')
        self.process = subprocess.Popen([sys.executable, '-u', '-B', '-c', BOOTSTRAP, str(self.root)],
                                        cwd=APP, env=self.env, stdout=subprocess.PIPE, stderr=self.log, text=True)
        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if selector.select(.2):
                    line = self.process.stdout.readline()
                    if line.startswith('JUNO_STAGING_READY '):
                        return
                    if not line and self.process.poll() is not None:
                        raise AssertionError('Synthetic staging process refused startup')
            raise AssertionError('Synthetic staging process readiness timed out')
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

    def browser(self):
        return urllib.request.build_opener(urllib.request.HTTPSHandler(context=self.client_tls), urllib.request.HTTPCookieProcessor(CookieJar()))

    def post(self, browser, path, data=None, headers=None):
        token = getattr(browser, 'identity_token', None)
        request = urllib.request.Request(self.url + path, method='POST', data=json.dumps(data or {}).encode(),
                                         headers={'Content-Type': 'application/json', **(headers or {}),
                                                  **({'X-Juno-Identity': token} if token else {})})
        try:
            with browser.open(request, timeout=5) as response:
                status, result = response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            status, result = exc.code, json.loads(exc.read())
        if status == 200 and path == '/api/identity':
            browser.identity_token = result['identity_token']
        return status, result

    def sign_in(self, browser, code=None):
        self.assertEqual(self.post(browser, '/api/auth', {'passphrase': self.env['JUNO_ACCESS_PASSPHRASE']})[0], 200)
        body = {'access_code': code} if code else {'new_student': True, 'student': 'Synthetic Staging Student'}
        status, result = self.post(browser, '/api/identity', body)
        self.assertEqual(status, 200)
        return result

    def config_context(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(mock.patch.dict(os.environ, self.env, clear=True))
        stack.enter_context(mock.patch.object(policy, 'DISK_ROOT', self.root))
        stack.enter_context(mock.patch.object(policy, 'disk_is_mounted', return_value=True))
        return stack

    def test_https_cookies_identity_memory_recaps_and_restart(self):
        browser = self.browser()
        selected = self.sign_in(browser)
        code = selected['personal_code']
        jar = next(handler.cookiejar for handler in browser.handlers if hasattr(handler, 'cookiejar'))
        self.assertTrue(all(cookie.secure for cookie in jar))
        with browser.open(self.url + '/') as response:
            self.assertIn("connect-src 'self'", response.headers['Content-Security-Policy'])
            self.assertIn('STAGING', response.read().decode())
        _, first = self.post(browser, '/api/start', {'mode': 'free'})
        self.assertEqual(self.post(browser, '/api/message', {'call_id': first['call_id'], 'text': 'Synthetic HTTPS lesson'})[0], 200)
        _, report = self.post(browser, '/api/end', {'call_id': first['call_id']})
        _, second = self.post(browser, '/api/start', {'mode': 'free'})
        self.assertIn('saved learning memory includes follow up', second['reply'])
        self.post(browser, '/api/message', {'call_id': second['call_id'], 'text': 'Synthetic unfinished HTTPS lesson'})
        self.env['JUNO_STAGING_INITIALIZE'] = 'false'
        self.stop()
        self.start()
        self.assertEqual(self.post(browser, '/api/resume')[0], 401)
        self.sign_in(browser, code)
        status, resume = self.post(browser, '/api/resume')
        self.assertEqual(status, 200)
        self.assertEqual(resume['open_call']['call_id'], second['call_id'])
        self.assertIn('Synthetic unfinished HTTPS lesson', str(resume))
        self.assertEqual(self.post(browser, '/api/end', {'call_id': first['call_id']}), (200, report))
        self.assertTrue((Path(self.env['JUNO_DATA_DIR']) / 'juno.db').exists())

    def test_host_https_origin_and_real_name_requests_are_denied(self):
        browser = self.browser()
        self.assertEqual(self.post(browser, '/api/auth', headers={'Origin': 'https://synthetic-production.invalid'})[0], 403)
        self.assertEqual(self.post(browser, '/api/auth', {'passphrase': self.env['JUNO_ACCESS_PASSPHRASE']})[0], 200)
        self.assertEqual(self.post(browser, '/api/identity', {'new_student': True, 'student': 'Not a synthetic account'})[0], 400)
        for host, proto in [('synthetic-production.invalid', 'https'), (self.env['JUNO_STAGING_HOSTNAME'], 'http')]:
            conn = http.client.HTTPConnection('127.0.0.1', int(self.env['PORT']))
            conn.request('POST', '/api/auth', '{}', {'Host': host, 'X-Forwarded-Proto': proto})
            response = conn.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            conn.close()

    def test_cross_student_access_and_feedback_use_separate_storage(self):
        a, b = self.browser(), self.browser()
        self.sign_in(a)
        self.sign_in(b)
        _, lesson = self.post(a, '/api/start', {'mode': 'free'})
        self.post(a, '/api/end', {'call_id': lesson['call_id']})
        self.assertEqual(self.post(b, '/api/end', {'call_id': lesson['call_id']})[0], 404)
        self.assertEqual(self.post(a, '/api/feedback', {'text': 'Synthetic staging feedback'})[0], 200)
        self.assertIn('Synthetic staging feedback', (Path(self.env['JUNO_DATA_DIR']) / 'feedback.jsonl').read_text())

    def test_consistent_backup_restore_and_restarted_restored_service(self):
        browser = self.browser()
        code = self.sign_in(browser)['personal_code']
        _, first = self.post(browser, '/api/start', {'mode': 'free'})
        _, report = self.post(browser, '/api/end', {'call_id': first['call_id']})
        _, unfinished = self.post(browser, '/api/start', {'mode': 'free'})
        self.post(browser, '/api/message', {'call_id': unfinished['call_id'], 'text': 'Synthetic saved backup lesson'})
        self.post(browser, '/api/feedback', {'text': 'Synthetic backed-up feedback'})
        self.post(browser, '/api/identity', {'access_code': 'synthetic-invalid-code'})
        snapshot = self.root / 'juno-staging-backups' / ('backup-' + uuid.uuid4().hex)
        restored = self.root / ('juno-staging-restore-' + str(uuid.uuid4()))
        with self.config_context():
            config = policy.load_config()
            staging_data.create_backup(config, snapshot)
            staging_data.verify_backup(config, snapshot)
            staging_data.restore_backup(config, snapshot, restored)
            with self.assertRaises(policy.StagingError):
                staging_data.restore_backup(config, snapshot, config.data_dir)
        with sqlite3.connect(restored / 'juno.db') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM login_failures').fetchone()[0], 1)
        self.assertIn('Synthetic backed-up feedback', (restored / 'feedback.jsonl').read_text())
        self.assertEqual([p.read_bytes() for p in (restored / 'students').glob('*.json')], [p.read_bytes() for p in (Path(self.env['JUNO_DATA_DIR']) / 'students').glob('*.json')])
        self.stop()
        self.env['JUNO_DATA_DIR'] = str(restored)
        self.env['JUNO_STAGING_INITIALIZE'] = 'false'
        self.start()
        self.sign_in(browser, code)
        self.assertEqual(self.post(browser, '/api/end', {'call_id': first['call_id']}), (200, report))
        status, resume = self.post(browser, '/api/resume')
        self.assertEqual(status, 200)
        self.assertEqual(resume['open_call']['call_id'], unfinished['call_id'])
        self.assertIn('Synthetic saved backup lesson', str(resume))
        _, next_lesson = self.post(browser, '/api/start', {'mode': 'free'})
        self.assertIn('saved learning memory includes follow up', next_lesson['reply'])

    def test_backups_wait_for_writers_and_refuse_tampering(self):
        browser = self.browser()
        self.sign_in(browser)
        self.post(browser, '/api/start', {'mode': 'free'})
        snapshot = self.root / 'juno-staging-backups' / ('backup-' + uuid.uuid4().hex)
        with self.config_context():
            config = policy.load_config()
            started = threading.Event()
            def backup():
                started.set()
                return staging_data.create_backup(config, snapshot)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                with staging_server.data_lock(config):
                    future = executor.submit(backup)
                    self.assertTrue(started.wait(2))
                    with self.assertRaises(concurrent.futures.TimeoutError):
                        future.result(timeout=.1)
                future.result(timeout=5)
            staging_data.verify_backup(config, snapshot)
            (snapshot / policy.MARKER).write_text('{}')
            with self.assertRaises(policy.StagingError):
                staging_data.verify_backup(config, snapshot)
