"""Mac launcher isolation/recovery; all files and identities are fictional."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent.parent))
import private_mac_test as private


class PrivateMacTest(unittest.TestCase):
    def test_unknown_existing_folder_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = private.prepare_session(temporary)
            self.assertEqual(root.stat().st_mode & 0o077, 0)
            (root / "session.json").write_text('{}')
            with self.assertRaises(RuntimeError):
                private.prepare_session(temporary)
        with tempfile.TemporaryDirectory() as temporary:
            root = private.prepare_session(temporary)
            (root / "data").symlink_to(Path(temporary), target_is_directory=True)
            with self.assertRaises(RuntimeError):
                private.prepare_session(temporary)

    def test_recovery_refuses_unknown_identity_locks_and_uncertain_spending(self):
        for failure in ("identity", "lock", "spend"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = private.prepare_session(temporary)
                (root / "data").mkdir()
                db = root / "data" / "juno.db"
                with sqlite3.connect(db) as connection:
                    connection.executescript('CREATE TABLE students(display_name TEXT,access_code TEXT); CREATE TABLE call_locks(call_id TEXT); CREATE TABLE ai_reservations(status TEXT);')
                    if failure == "identity":
                        connection.execute("INSERT INTO students VALUES ('Other fictional name','Other')")
                    if failure == "lock":
                        connection.execute("INSERT INTO call_locks VALUES ('fictional')")
                    if failure == "spend":
                        connection.execute("INSERT INTO ai_reservations VALUES ('uncertain')")
                before = db.read_bytes()
                with self.assertRaises(RuntimeError):
                    private.check_recovery(root)
                self.assertEqual(db.read_bytes(), before)

    def test_environment_allowlist_removes_provider_and_production_settings(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"PORT": "9999", "JUNO_DATABASE_URL": "fictional-forbidden-db",
                                         "OPENAI_API_KEY": "fictional-forbidden-key", "RESEND_API_KEY": "fictional-mail"}):
            cleaned = private.clean_environment()
        for name in ("PORT", "JUNO_DATABASE_URL", "OPENAI_API_KEY", "RESEND_API_KEY"):
            self.assertNotIn(name, cleaned)

    def test_launcher_server_is_local_and_startup_makes_no_ai_calls(self):
        code = r'''
import os, json, threading, urllib.request, urllib.error
from unittest import mock
from http.cookiejar import CookieJar
import private_mac_test as private
root = private.prepare_session()
web, handler = private.create_application(root)
import store, ai
assert not store.using_postgres()
assert str(store.DB_PATH).startswith(str(root))
assert 'OPENAI_API_KEY' not in os.environ
assert 'PORT' not in os.environ
with mock.patch.object(ai.Responses, 'create', side_effect=AssertionError('AI network forbidden')) as create:
    server = web.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    def post(path, origin):
        request=urllib.request.Request(base+path, data=b'{}', headers={'content-type':'application/json','Origin':origin})
        try:
            with browser.open(request) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as error:return error.code,None
    with browser.open(base) as response:assert response.status==200
    assert post('/api/resume','https://foreign.example')[0] == 403
    assert post('/api/resume',base)[0] == 200
    assert post('/api/admin',base)[0] == 404
    create.assert_not_called()
    assert store.list_students()[0]['display_name']=='Alex Test'
    assert len(store.list_students())==1
    server.shutdown(); server.server_close()
private.check_recovery(root)
print(json.dumps({'ai_calls':0,'student':'Alex Test','host':'127.0.0.1','daily_allowance':store.DAILY_COST_CEILING_USD}))
'''
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ, TMPDIR=temporary, PORT="9999", JUNO_DATABASE_URL="fictional-forbidden-db", OPENAI_API_KEY="fictional-forbidden-key")
            result = subprocess.run([sys.executable, "-c", code], cwd=Path(private.__file__).parent,
                                    env=environment, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"ai_calls": 0, "student": "Alex Test", "host": "127.0.0.1", "daily_allowance": 0.25})
