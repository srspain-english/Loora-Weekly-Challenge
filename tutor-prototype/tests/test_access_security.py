"""Synthetic migration, credential lifecycle, throttling and session regression tests."""
import json
import sqlite3
import unittest
from unittest import mock

import access_security as security
import store
import tutor
import web
from helpers import ServerTestCase, fake_reply


class AccessSecurityTest(ServerTestCase, unittest.TestCase):
    def setUp(self):
        web.ACCESS_PASSPHRASE = 'synthetic-gate'
        web.SESSIONS.clear()
        self.opener = self.new_browser()
        with security.throttle_database() as conn:
            conn.execute('DELETE FROM login_failures')

    def gate(self, browser=None):
        self.assertEqual(self.post('/api/auth', {'passphrase': 'synthetic-gate'}, browser)[0], 200)

    def test_generated_codes_use_256_bits_of_secure_randomness(self):
        with mock.patch.object(security.secrets, 'token_urlsafe', return_value='synthetic-random') as random:
            self.assertEqual(security.generate_code(), 'juno_synthetic-random')
            random.assert_called_once_with(32)

    def test_codes_are_unique_and_stored_only_as_hashes(self):
        a, b = store.create_student('Synthetic A'), store.create_student('Synthetic B')
        ca, cb = security.provision_code(a), security.provision_code(b)
        self.assertNotEqual(ca, cb)
        self.assertNotEqual(store.get_student(a)['access_code'], ca)
        self.assertTrue(store.get_student(a)['access_code'].startswith('sha256$'))
        self.assertEqual(store.student_by_access_code(ca)['student_id'], a)
        self.assertIsNone(store.student_by_access_code(cb + 'wrong'))
        self.assertIsNone(store.student_by_access_code(store.get_student(a)['access_code']))

    def test_code_less_existing_student_keeps_lessons_reports_and_memory(self):
        sid = store.create_student('Synthetic Existing')
        memory = tutor.load_student(sid)
        memory['vocabulary_learned'] = ['synthetic earlier vocabulary']
        tutor.save_student(memory)
        memory_path = tutor.STUDENTS_DIR / (sid + '.json')
        original_memory = memory_path.read_bytes()
        opened = store.create_call(sid, 'free', 'B1', None, '[]', [{'role': 'assistant', 'content': 'Previous lesson'}])
        finished = store.create_call(sid, 'free', 'B1', None, '[]', [{'role': 'assistant', 'content': 'Earlier lesson'}])
        report = {'what_we_did': 'Synthetic earlier report'}
        store.finish_call(finished, report)
        before = [dict(r) for r in store.connect().execute('SELECT * FROM calls WHERE student_id = ? ORDER BY call_id', (sid,))]
        code = security.provision_code(sid)
        self.assertEqual(memory_path.read_bytes(), original_memory)
        self.assertEqual(before, [dict(r) for r in store.connect().execute('SELECT * FROM calls WHERE student_id = ? ORDER BY call_id', (sid,))])
        self.gate()
        self.identify(access_code=code)
        status, result = self.post('/api/resume', {})
        self.assertEqual(status, 200)
        self.assertEqual(result['open_call']['call_id'], opened)
        self.assertEqual(self.post('/api/end', {'call_id': finished}), (200, {'report': report}))
        self.assertEqual(tutor.load_student(sid)['vocabulary_learned'], memory['vocabulary_learned'])
        # Normal teaching still loads the same learning memory.
        with mock.patch.object(tutor, 'build_system_blocks', wraps=tutor.build_system_blocks) as build, mock.patch.object(web.client.messages, 'create', return_value=fake_reply()):
            self.assertEqual(self.post('/api/start', {'mode': 'free'})[0], 200)
            self.assertEqual(build.call_args.args[3]['vocabulary_learned'], memory['vocabulary_learned'])

    def test_legacy_name_migration_then_provision_keeps_memory(self):
        original = tutor.STUDENTS_DIR / 'synthetic-legacy.json'
        original.write_text(json.dumps({'name': 'Synthetic Legacy', 'vocabulary_learned': ['synthetic word']}))
        original_bytes = original.read_bytes()
        sid = store.migrate_legacy_students(tutor.STUDENTS_DIR)[0]['student_id']
        migrated_path = tutor.STUDENTS_DIR / (sid + '.json')
        migrated_bytes = migrated_path.read_bytes()
        code = security.provision_code(sid)
        self.gate()
        self.identify(access_code=code)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(migrated_path.read_bytes(), migrated_bytes)
        self.assertEqual(tutor.load_student(sid)['vocabulary_learned'], ['synthetic word'])

    def test_plaintext_legacy_code_requires_explicit_replacement(self):
        sid = store.create_student('Synthetic Legacy Credential')
        store.connect().execute('UPDATE students SET access_code = ? WHERE student_id = ?', ('synthetic-old-plaintext', sid))
        store.connect().commit()
        self.assertIsNone(store.student_by_access_code('synthetic-old-plaintext'))
        with self.assertRaises(ValueError):
            security.provision_code(sid)
        code = security.provision_code(sid, replace=True)
        self.assertEqual(store.student_by_access_code(code)['student_id'], sid)

    def test_unknown_student_id_never_creates_or_modifies_a_student(self):
        before = store.connect().execute('SELECT COUNT(*) FROM students').fetchone()[0]
        with self.assertRaises(ValueError):
            security.provision_code('stu_synthetic_missing')
        self.assertEqual(store.connect().execute('SELECT COUNT(*) FROM students').fetchone()[0], before)

    def test_replacement_revokes_old_code_and_existing_sessions(self):
        sid = store.create_student('Synthetic Replace')
        old = security.provision_code(sid)
        self.gate()
        self.identify(access_code=old)
        with self.assertRaises(ValueError):
            security.provision_code(sid)
        new = security.provision_code(sid, replace=True)
        self.assertEqual(self.post('/api/resume', {})[0], 401)
        self.gate()
        self.assertEqual(self.post('/api/identity', {'access_code': old})[0], 400)
        self.identify(access_code=new)
        self.assertEqual(self.post('/api/resume', {})[0], 200)

    def test_new_student_receives_a_recoverable_code(self):
        self.gate()
        result = self.identify(student='Synthetic New')
        code = result['personal_code']
        self.assertTrue(code.startswith('juno_'))
        sid = store.student_by_access_code(code)['student_id']
        browser = self.new_browser()
        self.gate(browser)
        again = self.identify(browser, access_code=code)
        self.assertIsNone(again['personal_code'])
        self.assertEqual(next(v['student_id'] for v in web.SESSIONS.values() if v.get('identity_token') == again['identity_token']), sid)

    def test_student_failures_cannot_be_reset_by_new_browser_sessions(self):
        with mock.patch.object(security, 'SOURCE_FAILURE_LIMIT', 3):
            for _ in range(3):
                browser = self.new_browser()
                self.gate(browser)
                self.assertEqual(self.post('/api/identity', {'access_code': 'synthetic-invalid'}, browser)[0], 400)
            browser = self.new_browser()
            self.gate(browser)
            status, response = self.post('/api/identity', {'access_code': 'another-invalid'}, browser)
            self.assertEqual(status, 429)
            self.assertIn('five minutes', response['error'])

    def test_shared_gate_failures_cannot_be_reset_by_cookies_or_forwarded_headers(self):
        with mock.patch.object(security, 'SOURCE_FAILURE_LIMIT', 3):
            for _ in range(3):
                self.assertEqual(self.post('/api/auth', {'passphrase': 'synthetic-wrong'}, self.new_browser(), headers={'X-Forwarded-For': security.generate_code()})[0], 401)
            self.assertEqual(self.post('/api/auth', {'passphrase': 'synthetic-gate'}, self.new_browser())[0], 429)

    def test_global_bucket_catches_changing_source_addresses(self):
        with mock.patch.object(security, 'GLOBAL_FAILURE_LIMIT', 3):
            for index in range(3):
                self.assertFalse(security.check_login('student', f'synthetic-address-{index}', lambda: False))
            with self.assertRaises(security.LoginLimited):
                security.check_login('student', 'synthetic-new-address', lambda: True)

    def test_attempt_limits_survive_reopening_database(self):
        self.assertFalse(security.check_login('student', 'synthetic-source', lambda: False))
        store.reset_for_tests(store.DB_PATH)
        with mock.patch.object(security, 'SOURCE_FAILURE_LIMIT', 1):
            with self.assertRaises(security.LoginLimited):
                security.check_login('student', 'synthetic-source', lambda: True)

    def test_success_does_not_reset_failures_and_window_expires(self):
        with mock.patch.object(security.time, 'time', return_value=1000):
            self.assertFalse(security.check_login('student', 'synthetic-source', lambda: False))
            self.assertTrue(security.check_login('student', 'synthetic-source', lambda: True))
            with mock.patch.object(security, 'SOURCE_FAILURE_LIMIT', 1):
                with self.assertRaises(security.LoginLimited):
                    security.check_login('student', 'synthetic-source', lambda: True)
        with mock.patch.object(security.time, 'time', return_value=1301):
            self.assertTrue(security.check_login('student', 'synthetic-source', lambda: True))

    def test_signout_clears_shared_gate_as_well_as_student_identity(self):
        self.gate()
        self.identify()
        self.assertEqual(self.post('/api/signout', {})[0], 200)
        self.assertEqual(self.post('/api/identity', {'new_student': True, 'student': 'Synthetic Next'})[0], 401)
        self.gate()
        self.identify(student='Synthetic Next')

    def test_inactivity_expires_server_identity_and_shared_gate(self):
        self.gate()
        self.identify()
        session = next(v for v in web.SESSIONS.values() if v.get('identity_token') == self.opener.identity_token)
        session['last_activity'] -= security.IDLE_SECONDS + 1
        self.assertEqual(self.post('/api/resume', {})[0], 401)
        self.assertEqual(self.post('/api/identity', {'new_student': True, 'student': 'Synthetic Next'})[0], 401)

    def test_active_class_session_refreshes_expiration(self):
        self.gate()
        self.identify()
        session = next(v for v in web.SESSIONS.values() if v.get('identity_token') == self.opener.identity_token)
        session['last_activity'] -= security.IDLE_SECONDS - 1
        self.assertEqual(self.post('/api/session', {})[0], 200)
        self.assertGreater(session['last_activity'], security.time.time() - 2)


    def test_passive_page_gate_check_never_counts_as_a_failed_attempt(self):
        for _ in range(12):
            self.assertEqual(self.post('/api/gate', {}, self.new_browser())[0], 401)
        self.gate()
        self.assertEqual(self.post('/api/gate', {})[0], 200)

    def test_concurrent_failed_attempts_cannot_exceed_limit(self):
        import concurrent.futures
        def attempt(_):
            try:
                security.check_login('student', 'synthetic-concurrent', lambda: False)
                return 'checked'
            except security.LoginLimited:
                return 'blocked'
        with mock.patch.object(security, 'SOURCE_FAILURE_LIMIT', 3):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(attempt, range(8)))
        self.assertEqual(results.count('checked'), 3)
        self.assertEqual(results.count('blocked'), 5)

    def test_code_replacement_during_signin_cannot_authorize_old_code(self):
        sid = store.create_student('Synthetic Race')
        old = security.provision_code(sid)
        self.gate()
        original_touch = store.touch_student
        def replace_at_touch(student_id):
            security.provision_code(student_id, replace=True)
            original_touch(student_id)
        with mock.patch.object(store, 'touch_student', side_effect=replace_at_touch):
            self.assertEqual(self.post('/api/identity', {'access_code': old})[0], 401)
        self.assertEqual(self.post('/api/resume', {})[0], 401)

    def test_admin_command_requires_private_terminal_and_explicit_replacement(self):
        import admin_codes
        import sys
        sid = store.create_student('Synthetic Admin')
        arguments = ['admin_codes.py', '--database', str(store.DB_PATH), '--student-id', sid]
        with mock.patch.object(sys, 'argv', arguments), mock.patch.object(sys.stdout, 'isatty', return_value=False), mock.patch.object(sys, 'stderr'):
            with self.assertRaises(SystemExit):
                admin_codes.main()
        self.assertIsNone(store.get_student(sid)['access_code'])
        with mock.patch.object(sys, 'argv', arguments), mock.patch.object(sys.stdout, 'isatty', return_value=True), mock.patch('builtins.print') as output:
            self.assertEqual(admin_codes.main(), 0)
            code = output.call_args.args[0]
        self.assertEqual(store.student_by_access_code(code)['student_id'], sid)
        with mock.patch.object(sys, 'argv', arguments), mock.patch.object(sys.stdout, 'isatty', return_value=True), mock.patch('builtins.print'):
            self.assertEqual(admin_codes.main(), 1)

    def test_admin_command_refuses_an_unrelated_database(self):
        import admin_codes
        import sys
        wrong = self.tmp / 'synthetic-unrelated.db'
        with sqlite3.connect(wrong) as conn:
            conn.execute('CREATE TABLE unrelated (value TEXT)')
        before = wrong.read_bytes()
        with mock.patch.object(sys, 'argv', ['admin_codes.py', '--database', str(wrong), '--student-id', 'synthetic-id']), mock.patch.object(sys.stdout, 'isatty', return_value=True), mock.patch.object(sys, 'stderr'):
            with self.assertRaises(SystemExit):
                admin_codes.main()
        self.assertEqual(wrong.read_bytes(), before)
