"""Local-only Mac test launcher. No model calls until the user starts a class.

Uses only a marked temporary directory, an isolated SQLite database and a
fictional identity. Never invokes web.main or its legacy-data import path.
"""
from __future__ import annotations

import argparse
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import venv
import webbrowser

SOURCE = Path(__file__).resolve().parent
PURPOSE = "juno-private-mac-test-v1"


def clean_environment():
    # Inherit only basic OS settings, never production/cron/mail/provider config.
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
    return {name: os.environ[name] for name in keep if name in os.environ}


def prepare_session(parent=None, source=SOURCE):
    parent = Path(parent or tempfile.gettempdir()).resolve()
    source = Path(source).resolve()
    suffix = hashlib.sha256(str(source).encode()).hexdigest()[:12]
    root = parent / f"juno-private-{os.getuid()}-{suffix}"
    marker = {"purpose": PURPOSE, "source": str(source), "uid": os.getuid()}
    if root.is_symlink():
        raise RuntimeError("The private-test folder must not be a link.")
    if root.exists():
        if root.stat().st_uid != os.getuid() or (root.stat().st_mode & 0o077):
            raise RuntimeError("The private-test folder has unsafe permissions.")
        if (root / "session.json").is_symlink() or json.loads((root / "session.json").read_text()) != marker:
            raise RuntimeError("This is not a recognised private-test folder. Nothing was opened.")
    else:
        root.mkdir(mode=0o700)
        (root / "session.json").write_text(json.dumps(marker))
    for name in ("data", "data/juno.db", "data/juno.db-wal", "data/juno.db-shm", "data/students", "run.lock", "port", "venv"):
        if (root / name).is_symlink():
            raise RuntimeError("A private-test path is a link. Nothing was opened.")
    return root


def check_recovery(root):
    db = root / "data" / "juno.db"
    if not db.exists():
        return
    # Read-only inspection before the application can create/alter any tables.
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The test database needs recovery. Stop and ask Codex for help.")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"students", "call_locks", "ai_reservations"}.issubset(tables):
            raise RuntimeError("Unrecognised test database. Nothing was changed.")
        students = connection.execute("SELECT display_name, access_code FROM students").fetchall()
        if students and students != [("Alex Test", "JUNO-PRIVATE-DEMO")]:
            raise RuntimeError("Unexpected student identity. Nothing was changed.")
        if connection.execute("SELECT COUNT(*) FROM call_locks").fetchone()[0]:
            raise RuntimeError("An interrupted test left a safety lock. Stop and ask Codex for help; do not delete the database.")
        if connection.execute("SELECT COUNT(*) FROM ai_reservations WHERE status != 'settled'").fetchone()[0]:
            raise RuntimeError("An earlier test has uncertain spending. Stop and ask Codex for help; do not reset the allowance.")


def configure(root):
    os.environ.clear()
    os.environ.update(clean_environment_snapshot)
    os.environ.update({
        "JUNO_DATA_DIR": str(root / "data"), "JUNO_DB_PATH": str(root / "data" / "juno.db"),
        "JUNO_TURN_MODEL": "gpt-6-luna", "JUNO_REPORT_MODEL": "gpt-6.1-terra",
        "JUNO_TURN_EFFORT": "none", "JUNO_REPORT_EFFORT": "low",
        "JUNO_DAILY_COST_CEILING_USD": "0.25", "JUNO_MAX_CALLS_PER_DAY": "3",
        "JUNO_ACCESS_PASSPHRASE": secrets.token_urlsafe(24),
    })


def create_application(root):
    configure(root)
    # Import only after isolation. Never use app startup's legacy import/migration.
    import store
    import web
    store.connect()
    existing = store.student_by_access_code("JUNO-PRIVATE-DEMO")
    sid = existing["student_id"] if existing else store.create_student("Alex Test", "JUNO-PRIVATE-DEMO")

    class PrivateHandler(web.Handler):
        def _local_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self._local_host():
                self.send_error(403)
                return
            super().do_GET()

        def _load_session(self):
            session = super()._load_session()
            session["authed"] = True
            return session

        def _resolve_student(self, data):
            self._set_cookie("juno_student", sid, web.STUDENT_COOKIE_MAX_AGE)
            return store.get_student(sid)

        def _handle_start(self, session, data):
            return super()._handle_start(session, dict(data, mode="free", student="Alex Test", access_code="JUNO-PRIVATE-DEMO"))

        def do_POST(self):
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if (not self._local_host() or self.headers.get("Origin") != expected
                    or self.headers.get("Content-Type", "").partition(";")[0] != "application/json"):
                self.send_error(403)
                return
            allowed = {"/api/auth", "/api/start", "/api/message", "/api/end", "/api/resume",
                       "/api/abandon", "/api/progress", "/api/notes", "/api/help", "/api/define"}
            if self.path not in allowed:
                self.send_error(404)
                return
            super().do_POST()

    return web, PrivateHandler


# A snapshot, not a reference to a mutable environment. No secrets are retained.
clean_environment_snapshot = clean_environment()


def main():
    if sys.version_info < (3, 10):
        sys.exit("Please install Python 3.12 or newer from python.org, then try again.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check isolation using fictional data; no server, key or AI calls")
    parser.add_argument("--ready", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = prepare_session()
    with (root / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("Juno Private Test is already running. Use its existing window.")
        check_recovery(root)
        if not args.check and not args.ready:
            target = root / "venv"
            python = target / "bin" / "python"
            if not python.exists():
                print("Installing the local test software. This does not call OpenAI.", flush=True)
                venv.EnvBuilder(with_pip=True).create(target)
            result = subprocess.run([str(python), "-m", "pip", "--isolated", "install", "--no-cache-dir", "--disable-pip-version-check",
                                     "-r", str(SOURCE / "requirements.txt")], env=clean_environment_snapshot)
            if result.returncode:
                sys.exit("Software setup did not finish. No AI call was made.")
            # Release the setup lock before the configured child acquires it.
            fcntl.flock(lock, fcntl.LOCK_UN)
            sys.exit(subprocess.call([str(python), str(Path(__file__).resolve()), "--ready"], env=clean_environment_snapshot))
        web, handler = create_application(root)
        if args.check:
            print(json.dumps({"student": "Alex Test", "database": str(root / "data" / "juno.db"),
                              "postgres": False, "daily_allowance_usd": 0.25, "ai_calls": 0}))
            return
        # Bind before requesting a key; a busy port never opens another app.
        port_file = root / "port"
        port = int(port_file.read_text()) if port_file.exists() else 0
        server = web.ThreadingHTTPServer(("127.0.0.1", port), handler)
        port_file.write_text(str(server.server_address[1]))
        try:
            key = getpass.getpass("Paste your OpenAI TEST-project key here (hidden; not saved): ").strip()
            if not key:
                sys.exit("No key entered. No AI calls were made.")
            os.environ["OPENAI_API_KEY"] = key
            url = f"http://127.0.0.1:{server.server_address[1]}"
            print(f"\nJuno Private Test: {url}\nStudent: Alex Test. Choose 6 minutes; leave Juno speaks on.")
            print("No AI request runs until you start a class. Use fictional conversation details.")
            print("Press Control+C in this window to stop. Test progress and allowance survive a restart.")
            print(f"Temporary test folder: {root}")
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nPrivate test stopped. Your key was not saved.")
        finally:
            server.server_close()
            os.environ.pop("OPENAI_API_KEY", None)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nPrivate test stopped. No key was saved.")
    except sqlite3.Error:
        sys.exit("The private test database needs recovery. Stop and ask Codex for help.")
    except (RuntimeError, OSError, ValueError) as error:
        sys.exit(str(error))
