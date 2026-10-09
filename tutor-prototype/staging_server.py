#!/usr/bin/env python3
"""Explicit mocked HTTPS staging behind Render's TLS proxy. Never used by web.py."""
from __future__ import annotations
import fcntl
import json
import socket
from contextlib import contextmanager
from unittest.mock import patch
import staging_config
from staging_config import StagingError, load_config
from staging_mock import StagingMockAnthropic


@contextmanager
def data_lock(config, exclusive=False):
    with open(config.data_dir / '.staging-data.lock', 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def deny_outbound(*args, **kwargs):
    raise StagingError('Outbound networking is disabled in mocked staging.')


def handler_class(web, config):
    class StagingHandler(web.Handler):
        def end_headers(self):
            self.send_header('Content-Security-Policy', "default-src 'self'; connect-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Strict-Transport-Security', 'max-age=86400')
            super().end_headers()

        def allowed(self):
            hosts = self.headers.get_all('Host', [])
            origin = self.headers.get_all('Origin', [])
            proto = self.headers.get_all('X-Forwarded-Proto', [])
            if len(hosts) != 1 or hosts[0].lower() not in (config.hostname, config.hostname + ':443') or proto != ['https'] or (origin and origin != [config.origin]):
                self._send_json({'error': 'Use the configured staging HTTPS origin.'}, 403)
                return False
            try:
                if load_config() != config:
                    raise StagingError('Configuration changed.')
                config.verify_storage()
            except (StagingError, OSError, ValueError):
                self._send_json({'error': 'Staging configuration or storage verification failed.'}, 503)
                return False
            return True

        def do_GET(self):
            if self.path == '/healthz':
                try:
                    if load_config() != config:
                        raise StagingError('Configuration changed.')
                    config.verify_storage()
                    self._send_json({'ok': True, 'environment': 'staging', 'model': 'mock'})
                except StagingError:
                    self._send_json({'ok': False}, 503)
            elif self.allowed():
                config.verify_storage()
                super().do_GET()

        def do_POST(self):
            if self.allowed():
                with data_lock(config):
                    config.verify_storage()
                    super().do_POST()

        def _handle_identity(self, session, data):
            if data.get('new_student') is True and not str(data.get('student') or '').startswith('Synthetic '):
                self._send_json({'error': 'Staging accepts only Synthetic student names.'}, 400)
                return
            super()._handle_identity(session, data)
    return StagingHandler


def main():
    try:
        config = load_config()
        # One runtime per mounted disk; no other staging directory may start a second writer.
        with open(staging_config.DISK_ROOT / '.staging-service.lock', 'a') as service_lock:
            try:
                fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StagingError('Another staging runtime already holds this disk.') from exc
            config.prepare_storage()
            with patch.object(socket.socket, 'connect', deny_outbound), patch.object(socket.socket, 'connect_ex', deny_outbound), patch.object(socket.socket, 'sendto', deny_outbound):
                import anthropic
                with patch.object(anthropic, 'Anthropic', lambda *a, **kw: StagingMockAnthropic(config)):
                    import web
                if not web.RUNNING_DEPLOYED or not isinstance(web.client, StagingMockAnthropic) or web.tutor.DATA_DIR.resolve() != config.data_dir.resolve() or web.store.DB_PATH.resolve() != config.data_dir / 'juno.db' or web.FEEDBACK_PATH.resolve() != config.data_dir / 'feedback.jsonl':
                    raise StagingError('Application bootstrap does not match the staging configuration.')
                with data_lock(config, exclusive=True):
                    web.store.connect()
                web.INDEX_HTML = web.INDEX_HTML.replace('<div class="app">', '<div class="app"><p>STAGING — SYNTHETIC STUDENTS — MOCK AI — Never enter real student information.</p>', 1)
                with web.ThreadingHTTPServer(('0.0.0.0', config.port), handler_class(web, config)) as server:
                    print('JUNO_STAGING_READY ' + json.dumps({'environment': 'staging', 'model': 'mock', 'commit': config.commit}), flush=True)
                    server.serve_forever()
    except (StagingError, OSError, ValueError):
        # Never echo environment values, passphrases, or provider credentials.
        print('Staging startup refused: configuration, identity, or storage verification failed.', flush=True)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
