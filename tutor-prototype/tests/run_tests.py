#!/usr/bin/env python3
"""Run tests with synthetic credentials, disposable data, and loopback-only networking."""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def main() -> int:
    sys.dont_write_bytecode = True
    tests_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(tests_dir))
    sys.path.insert(0, str(tests_dir.parent))
    # Override, never reuse, any supplied runtime credentials or data paths.
    for name in list(os.environ):
        if name.startswith(("ANTHROPIC_", "JUNO_")) or name == "PORT":
            os.environ.pop(name)
    os.environ["ANTHROPIC_API_KEY"] = "synthetic-test-credential-not-valid"

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def local_only(method):
        def connect(sock, address):
            if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1", "localhost"):
                raise AssertionError("Tests may connect only to loopback services")
            return method(sock, address)
        return connect

    with tempfile.TemporaryDirectory(prefix="juno-security-tests-") as data_dir:
        os.environ["JUNO_DATA_DIR"] = data_dir
        with mock.patch.object(socket.socket, "connect", local_only(real_connect)), \
                mock.patch.object(socket.socket, "connect_ex", local_only(real_connect_ex)):
            loader = unittest.TestLoader()
            suite = (loader.loadTestsFromNames(sys.argv[1:]) if len(sys.argv) > 1
                     else loader.discover(str(tests_dir), pattern="test_*.py"))
            result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
