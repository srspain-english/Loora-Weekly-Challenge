#!/usr/bin/env python3
"""Tests for student identity: who a class belongs to, and who can read it.

The old rule was that the name typed into the box *was* the identity, so two
students called Maria shared one memory record and overwrote each other's
history, while one student who typed her name differently one day became a
stranger with no history at all. These tests pin down the replacement:
identity is an internal id, the name is only a label.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store  # noqa: E402
import tutor  # noqa: E402
import web  # noqa: E402
from helpers import ServerTestCase, fake_reply  # noqa: E402


class IdentityTest(ServerTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.opener = self.new_browser()
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()

    def _start(self, opener, **body):
        with mock.patch.object(
            web.client.messages, "create", return_value=fake_reply()
        ):
            return self.post("/api/start", {"mode": "free", **body}, opener)

    def test_same_name_on_two_devices_is_two_students(self) -> None:
        # The headline case. Two people, same first name, their own laptops.
        maria_a, maria_b = self.new_browser(), self.new_browser()
        self.auth(maria_a)
        self.auth(maria_b)

        _, a = self._start(maria_a, student="maria")
        _, b = self._start(maria_b, student="maria")

        call_a = store.get_call(a["call_id"])
        call_b = store.get_call(b["call_id"])
        self.assertNotEqual(
            call_a["student_id"], call_b["student_id"],
            "two students sharing a first name were merged into one record",
        )

    def test_a_student_keeps_their_id_when_they_retype_their_name(self) -> None:
        # Same browser, name spelled differently: same person, same memory.
        self.auth()
        _, first = self._start(self.opener, student="maria")
        _, second = self._start(self.opener, student="María López")

        id_first = store.get_call(first["call_id"])["student_id"]
        id_second = store.get_call(second["call_id"])["student_id"]
        self.assertEqual(id_first, id_second, "renaming created a new student")

    def test_renaming_updates_the_display_name(self) -> None:
        self.auth()
        _, first = self._start(self.opener, student="maria")
        student_id = store.get_call(first["call_id"])["student_id"]
        self._start(self.opener, student="María López")
        self.assertEqual(
            store.get_student(student_id)["display_name"], "María López"
        )

    def test_an_individual_code_identifies_a_student_on_any_device(self) -> None:
        # The way out of the shared passphrase: a per-student code works from
        # a browser that has never seen this student before.
        student_id = store.create_student("Ana", access_code="ana-7Q2")
        fresh = self.new_browser()
        self.auth(fresh)
        _, started = self._start(fresh, student="whatever", access_code="ana-7Q2")
        self.assertEqual(store.get_call(started["call_id"])["student_id"], student_id)

    def test_an_unknown_code_does_not_borrow_someone_elses_identity(self) -> None:
        known = store.create_student("Bea", access_code="bea-1")
        fresh = self.new_browser()
        self.auth(fresh)
        _, started = self._start(fresh, student="bea", access_code="not-a-real-code")
        self.assertNotEqual(store.get_call(started["call_id"])["student_id"], known)

    def test_memory_of_two_students_never_crosses(self) -> None:
        a = store.create_student("Same Name")
        b = store.create_student("Same Name")
        self.assertNotEqual(a, b)

        mem_a = tutor.load_student(a)
        mem_a["vocab_acquired_log"] = ["bottleneck — a slow point"]
        mem_a["student_id"] = a
        tutor.save_student(mem_a)

        mem_b = tutor.load_student(b)
        self.assertEqual(
            mem_b["vocab_acquired_log"], [],
            "one student's vocabulary leaked into another's memory",
        )


class NameKeyTest(unittest.TestCase):
    def test_folds_case_and_accents_for_lookup_only(self) -> None:
        self.assertEqual(store.name_key("María"), store.name_key("maria"))
        self.assertEqual(store.name_key("  Ana  Ruiz "), "ana ruiz")

    def test_different_names_do_not_collide(self) -> None:
        self.assertNotEqual(store.name_key("Ana"), store.name_key("Anna"))


class MigrationTest(unittest.TestCase):
    """The old name-keyed memory files must survive being brought across."""

    def setUp(self) -> None:
        import shutil, tempfile
        self.tmp = Path(tempfile.mkdtemp())
        store.reset_for_tests(self.tmp / "juno.db")
        self.students = self.tmp / "students"
        self.students.mkdir()
        self._cleanup = lambda: shutil.rmtree(self.tmp, ignore_errors=True)

    def tearDown(self) -> None:
        store.reset_for_tests()
        self._cleanup()

    def _write_legacy(self, name: str, vocab: list[str]) -> Path:
        import json
        path = self.students / f"{name}.json"
        path.write_text(json.dumps({
            "student_id": name, "name": name.capitalize(),
            "cefr_level": "B1", "vocab_acquired_log": vocab,
        }))
        return path

    def test_migration_preserves_the_original_file(self) -> None:
        # Non-destructive is the whole contract: if the migration is wrong,
        # nothing has been lost.
        legacy = self._write_legacy("alex", ["on the ball — alert"])
        before = legacy.read_bytes()
        store.migrate_legacy_students(self.students)
        self.assertTrue(legacy.exists(), "the original memory file was removed")
        self.assertEqual(legacy.read_bytes(), before, "the original was rewritten")

    def test_migration_carries_the_memory_across(self) -> None:
        import json
        self._write_legacy("alex", ["on the ball — alert"])
        migrated = store.migrate_legacy_students(self.students)
        self.assertEqual(len(migrated), 1)
        new_id = migrated[0]["student_id"]
        carried = json.loads((self.students / f"{new_id}.json").read_text())
        self.assertEqual(carried["vocab_acquired_log"], ["on the ball — alert"])
        self.assertEqual(carried["student_id"], new_id)

    def test_migration_is_idempotent(self) -> None:
        self._write_legacy("alex", [])
        first = store.migrate_legacy_students(self.students)
        second = store.migrate_legacy_students(self.students)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [], "re-running migrated the same student twice")

    def test_unreadable_file_is_left_alone(self) -> None:
        broken = self.students / "corrupt.json"
        broken.write_text("{ not json")
        migrated = store.migrate_legacy_students(self.students)
        self.assertEqual(migrated, [])
        self.assertEqual(broken.read_text(), "{ not json")


if __name__ == "__main__":
    unittest.main()
