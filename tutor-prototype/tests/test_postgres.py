#!/usr/bin/env python3
"""Tests for keeping Juno's data in a Postgres database.

The reason this exists: on Render's free tier every deploy wipes the local
files, which erased students' memory and saved classes. With
JUNO_DATABASE_URL set, all of it lives in the database instead.

The translation tests always run. The rest need a real database and run
only when JUNO_DATABASE_URL is set (the whole suite can be run that way).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store  # noqa: E402
import tutor  # noqa: E402


class TranslationTest(unittest.TestCase):
    tr = staticmethod(store._PgConnection._translate)

    def test_placeholders_become_postgres_style(self) -> None:
        self.assertEqual(self.tr("SELECT * FROM t WHERE a = ? AND b = ?"),
                         "SELECT * FROM t WHERE a = %s AND b = %s")

    def test_insert_or_ignore_becomes_on_conflict(self) -> None:
        out = self.tr("INSERT OR IGNORE INTO usage_daily (student_id, day) VALUES (?,?)")
        self.assertTrue(out.startswith("INSERT INTO usage_daily"))
        self.assertTrue(out.endswith("ON CONFLICT DO NOTHING"))

    def test_insert_or_replace_becomes_an_upsert(self) -> None:
        out = self.tr("INSERT OR REPLACE INTO idempotency (key, call_id, response,"
                      " created_at) VALUES (?,?,?,?)")
        self.assertIn("ON CONFLICT (key) DO UPDATE", out)
        self.assertNotIn("OR REPLACE", out)


@unittest.skipUnless(store.using_postgres(), "needs JUNO_DATABASE_URL")
class DatabaseModeTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset_for_tests()
        self._orig_memory = tutor.MEMORY_STORE
        tutor.MEMORY_STORE = store

    def tearDown(self) -> None:
        tutor.MEMORY_STORE = self._orig_memory

    def test_student_memory_is_saved_in_the_database(self) -> None:
        sid = store.create_student("Sandra")
        memory = tutor.load_student(sid)
        memory["next_recommendation"] = "Review the past simple."
        tutor.save_student(memory)
        self.assertEqual(tutor.load_student(sid)["next_recommendation"],
                         "Review the past simple.")

    def test_everything_survives_a_restart(self) -> None:
        sid = store.create_student("Sandra", access_code="SANDRA-1")
        call_id = store.create_call(sid, "free", "B1", None, "{}", [{"role": "user", "content": "hi"}])
        store.save_turn(call_id, [{"role": "user", "content": "hi"}], 1)
        memory = tutor.load_student(sid)
        memory["next_recommendation"] = "Keep going."
        tutor.save_student(memory)

        # A deploy restarts the process: drop the connection, reconnect.
        store._conn.close()
        store._conn = None

        self.assertEqual(store.student_by_access_code("SANDRA-1")["student_id"], sid)
        self.assertEqual(store.open_call_for(sid)["call_id"], call_id)
        self.assertEqual(tutor.load_student(sid)["next_recommendation"], "Keep going.")

    def test_a_dropped_connection_is_reopened(self) -> None:
        sid = store.create_student("Pilar")
        store.connect()._pg.close()  # what an idle managed database does
        self.assertEqual(store.get_student(sid)["display_name"], "Pilar")

    def test_spend_adds_up_across_calls(self) -> None:
        sid = store.create_student("Spender")
        store.record_usage(sid, 0.25)
        store.record_usage(sid, 0.5)
        self.assertAlmostEqual(store.daily_spend(), 0.75)

    def test_feedback_is_kept_in_the_database(self) -> None:
        store.add_feedback({"text": "Great", "student": None})
        row = store.connect().execute("SELECT entry FROM feedback").fetchone()
        self.assertIn("Great", row["entry"])


if __name__ == "__main__":
    unittest.main()
