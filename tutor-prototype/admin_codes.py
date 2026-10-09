#!/usr/bin/env python3
"""Local-only administrator provisioning. Does not import web or use API keys."""
import argparse
import os
import sys
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True,
                        help='Explicit existing database; never defaults to production data')
    parser.add_argument('--student-id', required=True)
    parser.add_argument('--replace', action='store_true', help='Revoke and replace an existing code')
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('Database must already exist.')
    if not sys.stdout.isatty():
        parser.error('Use a private interactive terminal; codes must not enter redirected logs.')
    try:
        conn = sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            columns = {row[1] for row in conn.execute('PRAGMA table_info(students)')}
            if not {'student_id', 'access_code'}.issubset(columns):
                parser.error('Specified database is not a Juno student database.')
        finally:
            conn.close()
    except sqlite3.Error:
        parser.error('Specified database cannot be opened as a Juno database.')
    os.environ['JUNO_DB_PATH'] = str(args.database.resolve())
    import access_security
    try:
        code = access_security.provision_code(args.student_id, replace=args.replace)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print('Deliver this code privately to the verified owner of the specified student ID:')
    print(code)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
