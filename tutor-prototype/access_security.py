"""Personal credentials and persistent login throttles; no lesson data changes."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

import store

IDLE_SECONDS = 20 * 60
WINDOW_SECONDS = 5 * 60
SOURCE_FAILURE_LIMIT = 10
GLOBAL_FAILURE_LIMIT = 100
LOGIN_LOCK = threading.RLock()


def generate_code() -> str:
    # 256 random bits. The printable prefix is not part of the entropy.
    return 'juno_' + secrets.token_urlsafe(32)


def code_hash(code: str) -> str:
    # Fast hashing is appropriate for randomly generated 256-bit bearer secrets.
    # Human-chosen passwords must never be provisioned through this API.
    return 'sha256$' + hashlib.sha256(('juno-personal-code-v1:' + code.strip()).encode()).hexdigest()


def provision_code(student_id: str, *, replace: bool = False) -> str:
    """Local administrator operation: exact ID only, never name matching.

    Returns the secret once; callers must deliver it privately. Existing
    plaintext credentials are replaced, never accepted by the public login.
    """
    store.connect()
    conn = sqlite3.connect(str(store.DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        with LOGIN_LOCK, conn:
            conn.execute('BEGIN IMMEDIATE')
            student = conn.execute('SELECT * FROM students WHERE student_id = ?', (student_id,)).fetchone()
            if not student:
                raise ValueError('Unknown student ID; no student was created.')
            if student['access_code'] and not replace:
                raise ValueError('A code already exists. Explicit replacement is required.')
            code = generate_code()
            conn.execute('UPDATE students SET access_code = ? WHERE student_id = ?',
                         (code_hash(code), student_id))
        return code
    finally:
        conn.close()


@contextmanager
def throttle_database():
    # Separate connection/transaction avoids sharing the lesson connection's
    # transaction with concurrent login requests. Same database architecture.
    store.connect()
    conn = sqlite3.connect(str(store.DB_PATH), timeout=5)
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS login_failures '
                     '(kind TEXT NOT NULL, source TEXT NOT NULL, occurred REAL NOT NULL)')
        conn.execute('CREATE INDEX IF NOT EXISTS login_failures_time ON login_failures(occurred)')
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.commit()
    finally:
        conn.close()


class LoginLimited(Exception):
    pass


def check_login(kind: str, source: str, verify):
    """Serialise limit check + verification + recording across processes.

    Source is the socket peer, never an untrusted forwarding header. A global
    bucket also caps attempts from changing addresses. Success does not reset
    failures, and blocked attempts do not prolong the fixed window.
    """
    with LOGIN_LOCK, throttle_database() as conn:
        now = time.time()
        conn.execute('DELETE FROM login_failures WHERE occurred <= ?', (now - WINDOW_SECONDS,))
        count, source_count = conn.execute(
            'SELECT COUNT(*), COALESCE(SUM(source = ?), 0) FROM login_failures WHERE kind = ?',
            (source, kind)).fetchone()
        if count >= GLOBAL_FAILURE_LIMIT or source_count >= SOURCE_FAILURE_LIMIT:
            raise LoginLimited()
        result = verify()
        if not result:
            conn.execute('INSERT INTO login_failures VALUES (?,?,?)', (kind, source, now))
        return result
