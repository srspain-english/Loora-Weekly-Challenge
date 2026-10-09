#!/usr/bin/env python3
"""Explicit local-only synthetic validation server; no production startup changes."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import socket
import sys
from contextlib import redirect_stdout
import tempfile
from pathlib import Path
from unittest.mock import patch

from dev_mock import (LocalMockAnthropic, MARKER, MockConfigurationError,
                      ROOT, require_local_environment, require_mock_storage, synthetic_report)

FIXTURES = '.synthetic-fixtures.json'


def private_json(path: Path, value) -> None:
    with open(path, 'x', opener=lambda name, flags: os.open(name, flags, 0o600)) as handle:
        json.dump(value, handle, indent=2)


def prepare_storage(path: Path) -> Path:
    require_local_environment()
    path = path.absolute()
    if path.is_symlink() or path.resolve().parent != Path(tempfile.gettempdir()).resolve() or not path.name.startswith('juno-mock-'):
        raise MockConfigurationError('Use a dedicated juno-mock-* temporary directory, never application data.')
    path.mkdir(mode=0o700, exist_ok=True)
    if not (path / MARKER).exists():
        if any(path.iterdir()):
            raise MockConfigurationError('Refusing to initialise a nonempty unmarked directory.')
        private_json(path / MARKER, {'purpose': 'juno-local-synthetic-only-v1', 'checkout': str(ROOT)})
    require_mock_storage(path)
    if path.stat().st_uid != os.getuid():
        raise MockConfigurationError('Mock storage must belong to the current user.')
    path.chmod(0o700)
    return path.resolve()


def seed_synthetic_students(path: Path) -> dict:
    require_mock_storage(path)
    import access_security
    import store
    import tutor
    if (path / FIXTURES).exists():
        fixtures = json.loads((path / FIXTURES).read_text())
        for account in fixtures['students'].values():
            student = store.student_by_access_code(account['code'])
            if not student or student['student_id'] != account['student_id']:
                raise MockConfigurationError('Synthetic fixture identities do not match storage; refusing to reseed.')
        return fixtures
    conn = store.connect()
    if conn.execute('SELECT COUNT(*) FROM students').fetchone()[0] or conn.execute('SELECT COUNT(*) FROM calls').fetchone()[0]:
        raise MockConfigurationError('Existing data has no fixture manifest; refusing to overwrite it.')
    students = {}
    for label in ('A', 'B'):
        sid = store.create_student('Synthetic Classroom Student')
        students[label] = {'student_id': sid, 'code': access_security.provision_code(sid)}
    sid = store.create_student('Synthetic Existing Student')
    memory = tutor.load_student(sid)
    memory['vocab_acquired_log'] = ['historical synthetic term — preserved meaning']
    memory['next_recommendation'] = 'Preserve historical synthetic learning memory.'
    tutor.save_student(memory)
    memory_path = tutor.STUDENTS_DIR / (sid + '.json')
    opened = store.create_call(sid, 'free', 'B1', None, '[]', [
        {'role': 'user', 'content': '(the call has just connected — open it)'},
        {'role': 'assistant', 'content': 'Synthetic historical unfinished lesson.'},
        {'role': 'user', 'content': 'Synthetic historical answer.'}])
    finished = store.create_call(sid, 'free', 'B1', None, '[]', [
        {'role': 'assistant', 'content': 'Synthetic historical finished lesson.'}])
    report = synthetic_report()
    report['what_we_did'] = 'Historical synthetic recap: preserve this report.'
    store.finish_call(finished, report)
    before_calls = [dict(row) for row in conn.execute('SELECT * FROM calls WHERE student_id = ? ORDER BY call_id', (sid,))]
    before_memory = memory_path.read_bytes()
    code = access_security.provision_code(sid)
    after_calls = [dict(row) for row in conn.execute('SELECT * FROM calls WHERE student_id = ? ORDER BY call_id', (sid,))]
    if before_calls != after_calls or before_memory != memory_path.read_bytes():
        raise MockConfigurationError('Synthetic provisioning changed historical data.')
    students['existing'] = {'student_id': sid, 'code': code,
                            'open_call': opened, 'finished_call': finished,
                            'historical_calls': before_calls,
                            'memory_sha256': hashlib.sha256(before_memory).hexdigest()}
    fixtures = {'synthetic_only': True, 'gate': 'synthetic-local-' + os.urandom(24).hex(), 'students': students}
    private_json(path / FIXTURES, fixtures)
    return fixtures


def no_outbound_connections(*args, **kwargs):
    raise MockConfigurationError('Outbound connections are disabled in the local mock process.')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-local-mock', action='store_true', required=True)
    parser.add_argument('--data-dir', type=Path, help='New or marked juno-mock-* directory directly in system temp')
    parser.add_argument('--port', type=int, default=0, help='Loopback port; zero selects an available port')
    args = parser.parse_args(argv)
    temporary = None
    try:
        require_local_environment()
        if any(name.startswith('JUNO_') for name in os.environ):
            raise MockConfigurationError('Inherited Juno configuration is refused; use launcher arguments.')
        if not 0 <= args.port <= 65535:
            raise MockConfigurationError('Invalid local port.')
        if args.data_dir is None:
            temporary = tempfile.TemporaryDirectory(prefix='juno-mock-')
            path = Path(temporary.name)
        else:
            path = args.data_dir
        path = prepare_storage(path)
        with (path / '.server.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise MockConfigurationError('Another local mock already uses this storage.') from exc
            # Production imports never consult mock flags or import this launcher.
            os.environ['JUNO_DATA_DIR'] = str(path)
            os.environ['JUNO_DB_PATH'] = str(path / 'juno.db')
            with patch.object(socket.socket, 'connect', no_outbound_connections), \
                    patch.object(socket.socket, 'connect_ex', no_outbound_connections):
                with redirect_stdout(sys.stderr):
                    fixtures = seed_synthetic_students(path)
                os.environ['JUNO_ACCESS_PASSPHRASE'] = fixtures['gate']
                import anthropic
                with patch.object(anthropic, 'Anthropic', lambda *a, **kw: LocalMockAnthropic(path)):
                    import web
                if web.RUNNING_DEPLOYED or web.HOST != '127.0.0.1' or not isinstance(web.client, LocalMockAnthropic):
                    raise MockConfigurationError('Local mock boot configuration is unsafe.')
                web.INDEX_HTML = web.INDEX_HTML.replace('<div class="app">', '<div class="app"><p>LOCAL SYNTHETIC MOCK — No real students or AI responses.</p>', 1)
                with web.ThreadingHTTPServer(('127.0.0.1', args.port), web.Handler) as server:
                    print('JUNO_LOCAL_MOCK_READY ' + json.dumps({
                        'url': 'http://127.0.0.1:' + str(server.server_port),
                        'data_dir': str(path), 'fixtures_file': str(path / FIXTURES),
                        'mode': 'local-synthetic-mock', 'api_calls_enabled': False,
                    }), flush=True)
                    server.serve_forever()
    except (MockConfigurationError, OSError, ValueError) as exc:
        # Do not echo configuration values or supplied credentials.
        print('Local mock refused to start: ' + str(exc), flush=True)
        return 2
    except KeyboardInterrupt:
        return 0
    finally:
        if temporary:
            temporary.cleanup()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
