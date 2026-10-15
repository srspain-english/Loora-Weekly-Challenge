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

A note on durability: on Render's free tier the disk is wiped by every deploy.
Set JUNO_DATABASE_URL to a Postgres address (for example a free Neon project)
and everything here - students, saved classes, limits, spend, and each
student's memory - lives there instead, and survives deploys. Without it,
this is the same local SQLite file as before.
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
DATA_DIR = Path(os.environ.get("JUNO_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("JUNO_DB_PATH", DATA_DIR / "juno.db"))

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
_conn = None

DATABASE_URL = os.environ.get("JUNO_DATABASE_URL", "").strip()


def using_postgres() -> bool:
    return bool(DATABASE_URL)


class _PgRow(dict):
    """A result row that reads by column name, like sqlite3.Row."""


class _PgCursor:
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        row = self._cur.fetchone()
        return _PgRow(row) if row is not None else None

    def fetchall(self):
        return [_PgRow(r) for r in self._cur.fetchall()]


class _PgConnection:
    """Gives a Postgres connection the small sqlite3 surface this module uses,
    so every query below runs unchanged on either database.

    One shared connection, guarded by a lock, as before. A managed database
    closes idle connections, so a dropped one is reopened once and the
    statement retried instead of failing in front of a student.
    """

    def __init__(self, url: str):
        self._url = url
        self._lock = threading.RLock()
        self._pg = None
        self._open()

    def _open(self):
        import psycopg
        from psycopg.rows import dict_row
        if self._pg is not None:
            try:
                self._pg.close()
            except Exception:  # noqa: BLE001
                pass
        # prepare_threshold=None: a pooled connection (Neon's default address)
        # cannot reuse prepared statements across requests.
        self._pg = psycopg.connect(self._url, row_factory=dict_row,
                                   autocommit=False, prepare_threshold=None)

    @staticmethod
    def _translate(sql: str) -> str:
        s = sql.replace("?", "%s")
        if "INSERT OR IGNORE INTO" in s:
            s = s.replace("INSERT OR IGNORE INTO", "INSERT INTO") + " ON CONFLICT DO NOTHING"
        if "INSERT OR REPLACE INTO idempotency" in s:
            s = s.replace("INSERT OR REPLACE INTO", "INSERT INTO") + (
                " ON CONFLICT (key) DO UPDATE SET call_id = EXCLUDED.call_id,"
                " response = EXCLUDED.response, created_at = EXCLUDED.created_at")
        return s

    def execute(self, sql: str, params=()):
        import psycopg
        s = self._translate(sql)
        with self._lock:
            for attempt in (1, 2):
                try:
                    cur = self._pg.cursor()
                    cur.execute(s, tuple(params))
                    return _PgCursor(cur)
                except (psycopg.OperationalError, psycopg.InterfaceError):
                    if attempt == 2:
                        raise
                    self._open()
                except Exception:
                    self._pg.rollback()
                    raise

    def executescript(self, script: str) -> None:
        with self._lock:
            script = (script.replace("REAL NOT NULL", "DOUBLE PRECISION NOT NULL")
                      .replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"))
            for stmt in script.split(";"):
                body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
                if body:
                    self.execute(body)
            self._pg.commit()

    def commit(self) -> None:
        with self._lock:
            self._pg.commit()

    def close(self) -> None:
        with self._lock:
            self._pg.close()


def connect():
    global _conn
    with _LOCK:
        if _conn is None:
            if using_postgres():
                _conn = _PgConnection(DATABASE_URL)
                _init_schema(_conn)
            else:
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
    if using_postgres():
        conn = connect()
        conn.execute("TRUNCATE students, usage_daily, spend_daily, calls,"
                     " idempotency, student_memory, feedback")
        conn.commit()


def _init_schema(conn) -> None:
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

        -- What Juno has learned about each student. Lives here, not in a
        -- file, so a deploy cannot erase it.
        CREATE TABLE IF NOT EXISTS student_memory (
            student_id TEXT PRIMARY KEY,
            memory     TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            entry        TEXT NOT NULL,
            submitted_at TEXT NOT NULL
        );

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
        "SELECT * FROM students WHERE UPPER(access_code) = UPPER(?)", (code.strip(),)
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
        " ON CONFLICT(day) DO UPDATE SET cost_usd = spend_daily.cost_usd + excluded.cost_usd",
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


# --- Student memory and feedback ---------------------------------------
# With a Postgres database these live in it, so a deploy cannot erase what
# Juno has learned about a student. tutor.MEMORY_STORE points at this module.

def get_memory(student_id: str) -> dict | None:
    row = connect().execute(
        "SELECT memory FROM student_memory WHERE student_id = ?", (student_id,)
    ).fetchone()
    return json.loads(row["memory"]) if row else None


def put_memory(student_id: str, memory: dict) -> None:
    conn = connect()
    if using_postgres():
        conn.execute(
            "INSERT INTO student_memory (student_id, memory, updated_at) VALUES (?,?,?)"
            " ON CONFLICT (student_id) DO UPDATE SET memory = EXCLUDED.memory,"
            " updated_at = EXCLUDED.updated_at",
            (student_id, json.dumps(memory), _now()),
        )
    else:
        conn.execute(
            "INSERT OR REPLACE INTO student_memory (student_id, memory, updated_at)"
            " VALUES (?,?,?)",
            (student_id, json.dumps(memory), _now()),
        )
    conn.commit()


def import_memory_files(students_dir: Path) -> int:
    """Copy memory files into the database, once each. Files are left alone."""
    count = 0
    if not students_dir.exists():
        return count
    for path in sorted(students_dir.glob("stu_*.json")):
        if get_memory(path.stem) is not None:
            continue
        try:
            put_memory(path.stem, json.loads(path.read_text(encoding="utf-8")))
            count += 1
        except (OSError, ValueError):
            continue
    return count


def add_feedback(entry: dict) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO feedback (entry, submitted_at) VALUES (?,?)",
        (json.dumps(entry), _now()),
    )
    conn.commit()


# --- Personal codes and the teacher's student list -----------------------

def _code_stem(display_name: str) -> str:
    stem = "".join(c for c in name_key(display_name).upper() if c.isalpha())
    return (stem[:10] or "JUNO")


def make_access_code(display_name: str) -> str:
    """A readable, unused code such as SANDRA-4821."""
    for _ in range(50):
        code = f"{_code_stem(display_name)}-{secrets.randbelow(9000) + 1000}"
        if student_by_access_code(code) is None:
            return code
    raise RuntimeError("could not find an unused access code")


def set_access_code(student_id: str, code: str) -> str:
    code = code.strip().upper()
    other = student_by_access_code(code)
    if other and other["student_id"] != student_id:
        raise ValueError("That code already belongs to another student.")
    conn = connect()
    conn.execute("UPDATE students SET access_code = ? WHERE student_id = ?",
                 (code, student_id))
    conn.commit()
    return code


def list_students() -> list[dict]:
    rows = connect().execute(
        "SELECT s.student_id, s.display_name, s.access_code, s.created_at,"
        " s.last_seen_at,"
        " (SELECT COUNT(*) FROM calls c WHERE c.student_id = s.student_id"
        "  AND c.status = 'ended') AS classes"
        " FROM students s ORDER BY s.last_seen_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def open_calls_for(student_id: str) -> list[dict]:
    """Every unfinished class of a student, oldest first."""
    rows = connect().execute(
        "SELECT call_id FROM calls WHERE student_id = ? AND status = 'open'"
        " ORDER BY started_at", (student_id,)
    ).fetchall()
    return [get_call(r["call_id"]) for r in rows]


# A class left open overnight would otherwise count as eight hours of practice.
MAX_MINUTES_PER_CLASS = 45


def _parse_time(value: str | None):
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def progress_for(student_id: str, tz_offset_minutes: int = 0, today=None) -> dict:
    """Real numbers for the student's Progress screen, from saved classes.

    `tz_offset_minutes` is the browser's getTimezoneOffset() (minutes to ADD to
    local time to get UTC), so a class at 23:30 in Madrid counts as that day
    for the student, not the next. Minutes are the time between starting and
    finishing a class, capped, since the app doesn't time speech itself.
    """
    from datetime import timedelta

    tz_offset_minutes = max(-840, min(840, int(tz_offset_minutes)))
    rows = connect().execute(
        "SELECT mode, started_at, ended_at, turns, report FROM calls"
        " WHERE student_id = ? AND status = 'ended' ORDER BY started_at",
        (student_id,),
    ).fetchall()

    classes = []
    for r in rows:
        start, end = _parse_time(r["started_at"]), _parse_time(r["ended_at"])
        if not start or not end:
            continue
        minutes = min(MAX_MINUTES_PER_CLASS, max(0.0, (end - start).total_seconds() / 60))
        local_day = (start - timedelta(minutes=tz_offset_minutes)).date()
        report = json.loads(r["report"]) if r["report"] else {}
        classes.append({"day": local_day, "minutes": minutes, "mode": r["mode"],
                        "turns": r["turns"], "what_we_did": report.get("what_we_did") or ""})

    if today is None:
        today = (datetime.now(timezone.utc) - timedelta(minutes=tz_offset_minutes)).date()
    by_day: dict = {}
    for c in classes:
        by_day[c["day"]] = by_day.get(c["day"], 0.0) + c["minutes"]

    week = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        week.append({"date": d.isoformat(), "weekday": d.strftime("%a"),
                     "minutes": round(by_day.get(d, 0.0))})

    # A streak survives until the end of the day after the last class.
    streak, d = 0, today if today in by_day else today - timedelta(days=1)
    while d in by_day:
        streak += 1
        d -= timedelta(days=1)

    return {
        "classes_total": len(classes),
        "minutes_total": round(sum(c["minutes"] for c in classes)),
        "minutes_week": sum(w["minutes"] for w in week),
        "exchanges_total": sum(c["turns"] for c in classes),
        "streak_days": streak,
        "week": week,
        "recent": [
            {"date": c["day"].isoformat(), "mode": c["mode"],
             "minutes": round(c["minutes"]), "what_we_did": c["what_we_did"]}
            for c in reversed(classes[-5:])
        ],
    }


def notes_for(student_id: str, limit: int = 30) -> list[dict]:
    """The student's finished classes with their session notes, newest first."""
    rows = connect().execute(
        "SELECT call_id, mode, started_at, ended_at, report FROM calls"
        " WHERE student_id = ? AND status = 'ended' AND report IS NOT NULL"
        " ORDER BY started_at DESC LIMIT ?",
        (student_id, int(limit)),
    ).fetchall()
    notes = []
    for r in rows:
        start, end = _parse_time(r["started_at"]), _parse_time(r["ended_at"])
        minutes = (min(MAX_MINUTES_PER_CLASS, max(0.0, (end - start).total_seconds() / 60))
                   if start and end else 0)
        notes.append({"call_id": r["call_id"], "mode": r["mode"],
                      "started_at": r["started_at"], "minutes": round(minutes),
                      "report": json.loads(r["report"])})
    return notes
