#!/usr/bin/env python3
"""Server-side state for Juno: identity, usage limits, live calls, metrics.

Everything here exists because the browser is not a trustworthy place to keep
any of it. Before this module, a student's identity was the name they typed,
their usage limit was a cookie counter, and the transcript of a call lived
only in the server process's memory — so a duplicate name merged two people's
histories, clearing cookies reset every limit, and a restart mid-class threw
the class away.

SQLite rather than JSON files: the server is threaded, so two requests can
write at the same moment, and a read-modify-write on a JSON file loses one of
them silently. SQLite is in the standard library and serialises writes for us.
Student memory records stay as JSON files (tutor.load_student) — they predate
this and are left exactly where they are.

A note on durability, since it shapes what this can promise: on Render's free
tier the disk is not persistent across deploys. This survives process
restarts, which is the common case (the free tier idles the service out
between classes), but a redeploy starts from an empty database. Limits and
recoverable calls are therefore best-effort, not guarantees, until this runs
somewhere with a real disk or a managed database.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("JUNO_DB_PATH", BASE_DIR / "data" / "juno.db"))

# --- Limits ------------------------------------------------------------
# All overridable from the environment so they can be tightened on a live
# deployment without a code change.
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


MAX_TURNS_PER_CALL = _int_env("JUNO_MAX_TURNS_PER_CALL", 40)
MAX_CALLS_PER_DAY = _int_env("JUNO_MAX_CALLS_PER_DAY", 6)
MAX_TRANSCRIPT_CHARS = _int_env("JUNO_MAX_TRANSCRIPT_CHARS", 120_000)
MAX_MESSAGE_CHARS = _int_env("JUNO_MAX_MESSAGE_CHARS", 4_000)
# The emergency brake: once the whole deployment has spent this much in a day,
# no new calls start for anyone. Set it to something you would not mind paying.
DAILY_COST_CEILING_USD = _float_env("JUNO_DAILY_COST_CEILING_USD", 5.0)
# Per-session warning threshold — logged, never shown to the student.
SESSION_COST_WARN_USD = _float_env("JUNO_SESSION_COST_WARN_USD", 1.0)

# Opus 5 list prices, $ per million tokens. Only used for the running cost
# estimate in the metrics log; nothing bills off these numbers.
PRICE_INPUT = 5.00
PRICE_OUTPUT = 25.00
PRICE_CACHE_WRITE = 6.25  # 1.25x input, 5-minute TTL
PRICE_CACHE_READ = 0.50   # 0.1x input

_LOCK = threading.Lock()
_conn: sqlite3.Connection | None = None


def connect() -> sqlite3.Connection:
    global _conn
    with _LOCK:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=5000")
            _init_schema(_conn)
        return _conn


def reset_for_tests(path: Path | None = None) -> None:
    """Point the module at a fresh database. Tests only."""
    global _conn, DB_PATH
    with _LOCK:
        if _conn is not None:
            _conn.close()
            _conn = None
        if path is not None:
            DB_PATH = path


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS students (
            student_id   TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            name_key     TEXT NOT NULL,
            access_code  TEXT UNIQUE,
            -- The name-as-identity this record was migrated from, if any.
            -- Kept so the migration can tell what it has already done, and so
            -- an old memory file can still be traced to its student.
            legacy_id    TEXT UNIQUE,
            created_at   TEXT NOT NULL,
            last_seen_at TEXT
        );
        CREATE INDEX IF NOT EXISTS students_name_key ON students(name_key);

        -- One row per student per day. Limits read from here, so they hold
        -- across browsers, devices and cleared cookies.
        CREATE TABLE IF NOT EXISTS usage_daily (
            student_id TEXT NOT NULL,
            day        TEXT NOT NULL,
            calls      INTEGER NOT NULL DEFAULT 0,
            turns      INTEGER NOT NULL DEFAULT 0,
            cost_usd   REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (student_id, day)
        );

        -- Deployment-wide spend, for the emergency ceiling.
        CREATE TABLE IF NOT EXISTS spend_daily (
            day      TEXT PRIMARY KEY,
            cost_usd REAL NOT NULL DEFAULT 0
        );

        -- A call, and its transcript, saved turn by turn so closing the tab
        -- no longer throws the class away.
        CREATE TABLE IF NOT EXISTS calls (
            call_id     TEXT PRIMARY KEY,
            student_id  TEXT NOT NULL,
            mode        TEXT NOT NULL,
            level       TEXT NOT NULL,
            scenario_id TEXT,
            system      TEXT NOT NULL,
            transcript  TEXT NOT NULL,
            turns       INTEGER NOT NULL DEFAULT 0,
            status      TEXT NOT NULL DEFAULT 'open',
            started_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            ended_at    TEXT,
            report      TEXT
        );
        CREATE INDEX IF NOT EXISTS calls_student ON calls(student_id, status);

        -- Replayed requests must not append the same turn twice.
        CREATE TABLE IF NOT EXISTS idempotency (
            key        TEXT PRIMARY KEY,
            call_id    TEXT NOT NULL,
            response   TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def name_key(name: str) -> str:
    """Fold a display name to a lookup key.

    Accent- and case-insensitive so "María" and "maria" find the same record
    when a student retypes their name; this is a convenience for finding an
    existing student, never the identity itself.
    """
    folded = unicodedata.normalize("NFKD", name.strip().lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(folded.split())


# --- Identity ----------------------------------------------------------

def create_student(display_name: str, access_code: str | None = None) -> str:
    """Register a new student and return their internal id.

    The id is random and permanent; the display name is neither. Two students
    who both call themselves Maria get two ids and never see each other's
    memory — which was the whole point of separating these.
    """
    conn = connect()
    student_id = "stu_" + secrets.token_urlsafe(12)
    conn.execute(
        "INSERT INTO students (student_id, display_name, name_key, access_code,"
        " created_at, last_seen_at) VALUES (?,?,?,?,?,?)",
        (student_id, display_name.strip() or "Student", name_key(display_name),
         access_code, _now(), _now()),
    )
    conn.commit()
    return student_id


def migrate_legacy_students(students_dir: Path) -> list[dict]:
    """Give every name-keyed memory file a real student id.

    Before this, a memory record was `data/students/<name>.json` and the name
    was the identity. Migration registers each one as a proper student and
    copies its memory to `<new id>.json`.

    Deliberately a copy, not a move: the original files are left byte-for-byte
    where they are. If anything here is wrong, nothing has been lost — the old
    files are still the old files. Re-running is safe; a record already
    migrated is recognised by its legacy_id and skipped.
    """
    conn = connect()
    migrated = []
    if not students_dir.exists():
        return migrated

    for path in sorted(students_dir.glob("*.json")):
        legacy_id = path.stem
        if legacy_id.startswith("stu_"):
            continue  # already a new-style record
        existing = conn.execute(
            "SELECT student_id FROM students WHERE legacy_id = ?", (legacy_id,)
        ).fetchone()
        if existing:
            continue  # migrated on an earlier run

        try:
            memory = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # unreadable file: leave it completely alone

        display = str(memory.get("name") or legacy_id).strip() or legacy_id
        student_id = "stu_" + secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO students (student_id, display_name, name_key, access_code,"
            " legacy_id, created_at, last_seen_at) VALUES (?,?,?,NULL,?,?,?)",
            (student_id, display, name_key(display), legacy_id, _now(), _now()),
        )
        conn.commit()

        memory["student_id"] = student_id
        memory.setdefault("display_name", display)
        (students_dir / f"{student_id}.json").write_text(
            json.dumps(memory, indent=2), encoding="utf-8"
        )
        migrated.append({"legacy_id": legacy_id, "student_id": student_id,
                         "display_name": display})
    return migrated


def get_student(student_id: str) -> dict | None:
    row = connect().execute(
        "SELECT * FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    return dict(row) if row else None


def student_by_access_code(code: str) -> dict | None:
    """An individual code identifies a student exactly, on any device.

    This is the escape from the shared-passphrase pilot: hand each student
    their own code and identity stops depending on the browser they happen to
    be sitting at.
    """
    if not code:
        return None
    row = connect().execute(
        "SELECT * FROM students WHERE access_code = ?", (code.strip(),)
    ).fetchone()
    return dict(row) if row else None


def set_display_name(student_id: str, display_name: str) -> None:
    """Rename a student in place. The id, and so the memory, is untouched."""
    name = display_name.strip()
    if not name:
        return
    conn = connect()
    conn.execute(
        "UPDATE students SET display_name = ?, name_key = ?, last_seen_at = ?"
        " WHERE student_id = ?",
        (name, name_key(name), _now(), student_id),
    )
    conn.commit()


def touch_student(student_id: str) -> None:
    conn = connect()
    conn.execute(
        "UPDATE students SET last_seen_at = ? WHERE student_id = ?",
        (_now(), student_id),
    )
    conn.commit()


# --- Limits ------------------------------------------------------------

class LimitReached(Exception):
    """A usage ceiling was hit. The message is shown to the student, so it
    says what happened and what they can do, never a bare code."""


def _usage_row(student_id: str) -> sqlite3.Row:
    conn = connect()
    day = _today()
    conn.execute(
        "INSERT OR IGNORE INTO usage_daily (student_id, day) VALUES (?,?)",
        (student_id, day),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM usage_daily WHERE student_id = ? AND day = ?",
        (student_id, day),
    ).fetchone()


def daily_spend() -> float:
    row = connect().execute(
        "SELECT cost_usd FROM spend_daily WHERE day = ?", (_today(),)
    ).fetchone()
    return float(row["cost_usd"]) if row else 0.0


def check_can_start_call(student_id: str) -> None:
    """Raise LimitReached if this student cannot start another call today."""
    if DAILY_COST_CEILING_USD > 0 and daily_spend() >= DAILY_COST_CEILING_USD:
        raise LimitReached(
            "Juno has reached today's usage limit for everyone. "
            "Please try again tomorrow, or ask your teacher."
        )
    used = _usage_row(student_id)["calls"]
    if MAX_CALLS_PER_DAY > 0 and used >= MAX_CALLS_PER_DAY:
        raise LimitReached(
            f"You've done {used} classes today, which is the daily limit. "
            "Your progress is saved — come back tomorrow."
        )


def check_can_send_turn(call: dict) -> None:
    """Raise LimitReached if this call cannot take another turn."""
    if call["turns"] >= MAX_TURNS_PER_CALL:
        raise LimitReached(
            "This class has reached its message limit. "
            "Press End call to get your report — you can start a new class after that."
        )
    if len(call["transcript_json"]) >= MAX_TRANSCRIPT_CHARS:
        raise LimitReached(
            "This class has got very long. "
            "Press End call to get your report, then start a fresh one."
        )


def record_call_started(student_id: str) -> None:
    conn = connect()
    _usage_row(student_id)
    conn.execute(
        "UPDATE usage_daily SET calls = calls + 1 WHERE student_id = ? AND day = ?",
        (student_id, _today()),
    )
    conn.commit()


def record_usage(student_id: str, cost_usd: float, turns: int = 1) -> None:
    conn = connect()
    day = _today()
    _usage_row(student_id)
    conn.execute(
        "UPDATE usage_daily SET turns = turns + ?, cost_usd = cost_usd + ?"
        " WHERE student_id = ? AND day = ?",
        (turns, cost_usd, student_id, day),
    )
    conn.execute(
        "INSERT INTO spend_daily (day, cost_usd) VALUES (?, ?)"
        " ON CONFLICT(day) DO UPDATE SET cost_usd = cost_usd + excluded.cost_usd",
        (day, cost_usd),
    )
    conn.commit()


def estimate_cost(usage) -> float:
    """Dollar estimate for one API response, from its usage block.

    Cache reads are a tenth of normal input and cache writes a quarter more,
    so a run where these are collapsed into one number hides exactly the thing
    worth watching.
    """
    def _n(attr: str) -> int:
        value = getattr(usage, attr, None) if usage is not None else None
        return int(value or 0)

    return (
        _n("input_tokens") / 1e6 * PRICE_INPUT
        + _n("output_tokens") / 1e6 * PRICE_OUTPUT
        + _n("cache_creation_input_tokens") / 1e6 * PRICE_CACHE_WRITE
        + _n("cache_read_input_tokens") / 1e6 * PRICE_CACHE_READ
    )


# --- Calls -------------------------------------------------------------

def create_call(student_id: str, mode: str, level: str,
                scenario_id: str | None, system: str, transcript: list) -> str:
    conn = connect()
    call_id = "call_" + secrets.token_urlsafe(12)
    conn.execute(
        "INSERT INTO calls (call_id, student_id, mode, level, scenario_id, system,"
        " transcript, turns, status, started_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,0,'open',?,?)",
        (call_id, student_id, mode, level, scenario_id, system,
         json.dumps(transcript), _now(), _now()),
    )
    conn.commit()
    return call_id


def get_call(call_id: str) -> dict | None:
    row = connect().execute(
        "SELECT * FROM calls WHERE call_id = ?", (call_id,)
    ).fetchone()
    if not row:
        return None
    call = dict(row)
    call["transcript_json"] = call["transcript"]
    call["transcript"] = json.loads(call["transcript"])
    call["report"] = json.loads(call["report"]) if call["report"] else None
    return call


def save_turn(call_id: str, transcript: list, turns: int) -> None:
    """Persist the transcript after each exchange.

    This is what makes a closed tab survivable: the class on disk is never
    more than one turn behind what the student sees.
    """
    conn = connect()
    conn.execute(
        "UPDATE calls SET transcript = ?, turns = ?, updated_at = ?"
        " WHERE call_id = ?",
        (json.dumps(transcript), turns, _now(), call_id),
    )
    conn.commit()


def open_call_for(student_id: str) -> dict | None:
    """The student's most recent unfinished class, if any."""
    row = connect().execute(
        "SELECT call_id FROM calls WHERE student_id = ? AND status = 'open'"
        " ORDER BY updated_at DESC LIMIT 1",
        (student_id,),
    ).fetchone()
    return get_call(row["call_id"]) if row else None


def finish_call(call_id: str, report: dict | None) -> None:
    conn = connect()
    conn.execute(
        "UPDATE calls SET status = 'ended', ended_at = ?, updated_at = ?, report = ?"
        " WHERE call_id = ?",
        (_now(), _now(), json.dumps(report) if report else None, call_id),
    )
    conn.commit()


def abandon_call(call_id: str) -> None:
    """Close a call without a report — the student chose to drop it."""
    conn = connect()
    conn.execute(
        "UPDATE calls SET status = 'abandoned', ended_at = ?, updated_at = ?"
        " WHERE call_id = ?",
        (_now(), _now(), call_id),
    )
    conn.commit()


# --- Idempotency -------------------------------------------------------

def replayed_response(key: str) -> dict | None:
    """The stored answer for a request key we've already handled.

    A student on a flaky connection who retries — or a double-clicked Send —
    must not get their message appended to the transcript twice, and must not
    be billed twice for it.
    """
    if not key:
        return None
    row = connect().execute(
        "SELECT response FROM idempotency WHERE key = ?", (key,)
    ).fetchone()
    return json.loads(row["response"]) if row else None


def remember_response(key: str, call_id: str, response: dict) -> None:
    if not key:
        return
    conn = connect()
    conn.execute(
        "INSERT OR REPLACE INTO idempotency (key, call_id, response, created_at)"
        " VALUES (?,?,?,?)",
        (key, call_id, json.dumps(response), _now()),
    )
    conn.commit()


def prune_idempotency(older_than_seconds: int = 86_400) -> None:
    conn = connect()
    cutoff = datetime.fromtimestamp(
        time.time() - older_than_seconds, tz=timezone.utc
    ).isoformat(timespec="seconds")
    conn.execute("DELETE FROM idempotency WHERE created_at < ?", (cutoff,))
    conn.commit()
