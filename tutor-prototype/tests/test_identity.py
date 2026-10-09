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
        maria_a, maria_b = self.new_browser(), self.new_browser()
        self.auth(maria_a)
        self.auth(maria_b)
        self.identify(maria_a, student="Maria")
        self.identify(maria_b, student="Maria")
        _, a = self._start(maria_a)
        _, b = self._start(maria_b)
        self.assertNotEqual(store.get_call(a["call_id"])["student_id"],
                            store.get_call(b["call_id"])["student_id"])

    def test_retyping_a_name_does_not_select_or_rename_another_student(self) -> None:
        self.auth()
        self.identify(student="Maria")
        _, first = self._start(self.opener, student="Maria")
        status, _ = self._start(self.opener, student="María López")
        self.assertEqual(status, 409)
        student_id = store.get_call(first["call_id"])["student_id"]
        self.assertEqual(store.get_student(student_id)["display_name"], "Maria")

    def test_a_personal_code_keeps_identity_without_trusting_the_typed_name(self) -> None:
        student_id = store.create_student("Maria", access_code="synthetic-maria-code")
        self.auth()
        self.post("/api/identity", {"access_code": "synthetic-maria-code",
                                    "student": "Someone else"})
        _, first = self._start(self.opener)
        self.assertEqual(store.get_call(first["call_id"])["student_id"], student_id)
        self.assertEqual(store.get_student(student_id)["display_name"], "Maria")

    def test_an_individual_code_identifies_a_student_on_any_device(self) -> None:
        student_id = store.create_student("Ana", access_code="synthetic-ana-code")
        fresh = self.new_browser()
        self.auth(fresh)
        self.identify(fresh, access_code="synthetic-ana-code")
        _, started = self._start(fresh)
        self.assertEqual(store.get_call(started["call_id"])["student_id"], student_id)

    def test_an_unknown_code_is_an_error_without_creating_a_student(self) -> None:
        self.auth()
        count = store.connect().execute("SELECT COUNT(*) FROM students").fetchone()[0]
        status, body = self.post("/api/identity", {"access_code": "invalid-synthetic-code",
                                                "student": "Bea", "new_student": True})
        self.assertEqual(status, 400)
        self.assertIn("Invalid personal code", body["error"])
        self.assertEqual(store.connect().execute("SELECT COUNT(*) FROM students").fetchone()[0], count)

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
