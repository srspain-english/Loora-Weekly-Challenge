#!/usr/bin/env python3
"""Tests for installing Juno on a phone's home screen."""

from __future__ import annotations

import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web  # noqa: E402
from helpers import ServerTestCase  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"


class InstallableTest(ServerTestCase, unittest.TestCase):
    def get(self, path):
        try:
            with urllib.request.urlopen(f"{self.base_url}{path}") as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, b""

    def test_manifest_describes_a_standalone_app_with_icons(self) -> None:
        status, headers, body = self.get("/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/manifest+json")
        manifest = json.loads(body)
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        sizes = {i["sizes"] for i in manifest["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)
        self.assertIn("maskable", {i["purpose"] for i in manifest["icons"]})

    def test_every_icon_the_manifest_names_is_really_served(self) -> None:
        for icon in json.loads(self.get("/manifest.webmanifest")[2])["icons"]:
            status, headers, body = self.get(icon["src"])
            self.assertEqual(status, 200, icon["src"])
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertTrue(body.startswith(PNG))

    def test_phones_and_browsers_find_their_icons(self) -> None:
        for path in ("/icons/apple-touch-icon.png", "/favicon.ico"):
            status, _, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body.startswith(PNG))

    def test_service_worker_is_served_fresh_and_caches_nothing(self) -> None:
        status, headers, body = self.get("/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("caches.", body.decode())     # a class is never served from a stale copy

    def test_only_the_listed_files_can_be_fetched(self) -> None:
        for path in ("/icons/../web.py", "/icons/nothing.png", "/static/icon.svg", "/icons/"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_page_links_the_manifest_icons_and_registers_the_worker(self) -> None:
        html = web.INDEX_HTML
        for piece in ('rel="manifest" href="/manifest.webmanifest"', 'rel="apple-touch-icon"',
                      'name="apple-mobile-web-app-capable"', "serviceWorker.register('/sw.js')",
                      'id="install-card"'):
            self.assertIn(piece, html)

    def test_iphone_students_are_told_how_to_install_and_where_voice_may_fail(self) -> None:
        self.assertIn("Add to Home Screen", web.INDEX_HTML)
        self.assertIn("voice may not work inside the home-screen icon", web.INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
