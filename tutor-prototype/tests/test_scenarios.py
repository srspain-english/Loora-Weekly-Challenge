#!/usr/bin/env python3
"""Tests for the scenario library and how a scenario gets picked.

Two halves:

1. Integrity checks over scenarios.json itself. It's the file that grows
   every time a sector is added, by hand or by script, and a typo there
   (a duplicated id, a CEFR level the prompt builder doesn't know) fails
   at call time in front of a student rather than here.

2. pick_scenario, including that an unknown pack or scenario raises rather
   than calling sys.exit - SystemExit inherits from BaseException, so on a
   request thread it would bypass web.py's `except Exception` and kill the
   request with no answer at all.

Touches no network: picking a scenario happens before any model call.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tutor  # noqa: E402
import web  # noqa: E402

REQUIRED_FIELDS = (
    "id",
    "title",
    "expression",
    "cefr",
    "role",
    "domain",
    "correction_instruction",
    "push_instruction",
    "closing_instruction",
)


def load_packs() -> list[dict]:
    return json.loads(tutor.SCENARIOS_PATH.read_text(encoding="utf-8"))["business_packs"]


def all_scenarios() -> list[tuple[str, dict]]:
    return [(p["id"], s) for p in load_packs() for s in p["scenarios"]]


class ScenarioLibraryTest(unittest.TestCase):
    def test_packs_have_unique_ids_and_labels(self) -> None:
        packs = load_packs()
        ids = [p["id"] for p in packs]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate pack ids in {ids}")
        for p in packs:
            self.assertTrue(p.get("label", "").strip(), f"pack {p['id']} has no label")
            self.assertTrue(p.get("scenarios"), f"pack {p['id']} has no scenarios")

    def test_scenario_ids_are_globally_unique(self) -> None:
        # pick_scenario flattens every pack into one library and matches on
        # id, so a repeat across packs would make the second one unreachable.
        ids = [s["id"] for _, s in all_scenarios()]
        dupes = {i for i in ids if ids.count(i) > 1}
        self.assertFalse(dupes, f"scenario ids used more than once: {sorted(dupes)}")

    def test_no_expression_is_taught_twice(self) -> None:
        # Each scenario exists to drill one expression. The same one appearing
        # in two packs isn't a crash, it's a teaching bug: a student working
        # through sectors would be handed the same target twice and one of
        # the two slots would be wasted.
        seen: dict[str, str] = {}
        clashes = []
        for pack_id, s in all_scenarios():
            key = s["expression"].strip().lower()
            if key in seen:
                clashes.append(f"{s['expression']!r}: {seen[key]} and {pack_id}/{s['id']}")
            seen[key] = f"{pack_id}/{s['id']}"
        self.assertFalse(clashes, "expressions taught more than once: " + "; ".join(clashes))

    def test_every_scenario_has_every_field_filled(self) -> None:
        for pack_id, s in all_scenarios():
            for field in REQUIRED_FIELDS:
                with self.subTest(pack=pack_id, scenario=s.get("id"), field=field):
                    self.assertIn(field, s)
                    self.assertTrue(
                        str(s[field]).strip(), f"{s.get('id')}.{field} is empty"
                    )

    def test_cefr_levels_are_ones_the_prompt_builder_knows(self) -> None:
        # An unknown level here would silently reach build_system_prompt.
        for pack_id, s in all_scenarios():
            with self.subTest(pack=pack_id, scenario=s["id"]):
                self.assertIn(s["cefr"], tutor.LEVEL_RULES)

    def test_ids_are_slugs(self) -> None:
        for _, s in all_scenarios():
            with self.subTest(scenario=s["id"]):
                self.assertRegex(s["id"], r"^[a-z0-9_]+$")


class PickScenarioTest(unittest.TestCase):
    def test_picks_a_named_scenario(self) -> None:
        first = all_scenarios()[0][1]
        self.assertEqual(tutor.pick_scenario(first["id"])["id"], first["id"])

    def test_picks_randomly_from_a_named_pack(self) -> None:
        pack = load_packs()[-1]
        ids_in_pack = {s["id"] for s in pack["scenarios"]}
        # Repeat: a wrong implementation could return an out-of-pack scenario
        # only occasionally.
        for _ in range(25):
            self.assertIn(tutor.pick_scenario(None, pack["id"])["id"], ids_in_pack)

    def test_picks_from_anywhere_when_nothing_is_specified(self) -> None:
        every_id = {s["id"] for _, s in all_scenarios()}
        self.assertIn(tutor.pick_scenario(None)["id"], every_id)

    def test_unknown_pack_raises_rather_than_exiting(self) -> None:
        with self.assertRaises(tutor.UnknownScenario):
            tutor.pick_scenario(None, "no_such_pack")

    def test_unknown_scenario_raises_rather_than_exiting(self) -> None:
        with self.assertRaises(tutor.UnknownScenario):
            tutor.pick_scenario("no_such_scenario")

    def test_unknown_scenario_is_an_ordinary_exception(self) -> None:
        # The whole point: `except Exception` must catch it. SystemExit,
        # which this used to be, would not be caught and would leave a
        # request hanging with no response.
        self.assertTrue(issubclass(tutor.UnknownScenario, Exception))
        self.assertFalse(issubclass(tutor.UnknownScenario, SystemExit))

    def test_scenario_id_outside_a_named_pack_is_rejected(self) -> None:
        packs = load_packs()
        other_id = packs[-1]["scenarios"][0]["id"]
        with self.assertRaises(tutor.UnknownScenario):
            tutor.pick_scenario(other_id, packs[0]["id"])


class StartEndpointScenarioTest(unittest.TestCase):
    """A bad pack from the page must come back as an error, not a dead
    request - and must not take the server down with it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        web.ACCESS_PASSPHRASE = ""
        web.SESSIONS.clear()

    def _post(self, path: str, body: dict):
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_unknown_pack_returns_400_and_server_survives(self) -> None:
        self._post("/api/auth", {"passphrase": ""})
        status, data = self._post(
            "/api/start", {"student": "demo", "mode": "business", "pack": "nope"}
        )
        self.assertEqual(status, 400)
        self.assertIn("nope", data["error"])
        # Still serving afterwards.
        with urllib.request.urlopen(f"{self.base_url}/") as resp:
            self.assertEqual(resp.status, 200)

    def test_page_offers_each_pack_grouped_with_its_own_random_option(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/") as resp:
            html = resp.read().decode("utf-8")
        self.assertIn("optgroup", html)
        self.assertIn("pack:", html)


if __name__ == "__main__":
    unittest.main()
