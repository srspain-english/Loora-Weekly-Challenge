"""Run the entire suite with no credentials, production data or outbound network.

Optional Postgres: set JUNO_TEST_DATABASE_URL to a disposable database whose
name begins juno_test_. No other database URL is accepted by this runner.
"""
import os
import pathlib
import socket
import sys
import tempfile
import unittest
from urllib.parse import urlparse

root = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(root / "tests"), str(root)]
for key in list(os.environ):
    if key.startswith(("JUNO_", "ANTHROPIC_", "OPENAI_")) and key != "JUNO_TEST_DATABASE_URL":
        os.environ.pop(key)
os.environ.pop("PORT", None)
test_url = os.environ.get("JUNO_TEST_DATABASE_URL", "")
if test_url:
    parsed = urlparse(test_url)
    if not parsed.path.removeprefix("/").startswith("juno_test_") or parsed.hostname not in ("localhost", "127.0.0.1"):
        sys.exit("Only local disposable juno_test_* Postgres databases are accepted")
    os.environ["JUNO_DATABASE_URL"] = test_url

_original_connect = socket.socket.connect
def local_only(sock, address):
    if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
        raise AssertionError("Outbound network is disabled during offline tests")
    return _original_connect(sock, address)
socket.socket.connect = local_only
with tempfile.TemporaryDirectory(prefix="juno-fictional-") as directory:
    os.environ["JUNO_DATA_DIR"] = directory
    os.environ["JUNO_DB_PATH"] = str(pathlib.Path(directory) / "juno.db")
    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
