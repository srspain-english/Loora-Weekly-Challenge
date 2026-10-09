#!/usr/bin/env python3
"""Consistent staging-only backups and non-overwriting restore rehearsals."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from staging_config import MARKER, StagingError, load_config, no_links
from staging_server import data_lock
import staging_config


def private_file(path: Path):
    return open(path, 'x', opener=lambda p, f: os.open(p, f, 0o600))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup_root():
    return staging_config.DISK_ROOT / 'juno-staging-backups'


def permitted_snapshot(path):
    path = Path(path)
    no_links(path)
    if not path.is_absolute() or path.parent != backup_root() or not path.name.startswith('backup-'):
        raise StagingError('Backup must be a new directory in the staging backup area.')
    return path


def integrity(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise StagingError('SQLite integrity check failed.')


def create_backup(config, output):
    output = permitted_snapshot(output)
    config.verify_storage()
    if output.exists():
        raise StagingError('Backup destination already exists; refusing overwrite.')
    output.parent.mkdir(mode=0o700, exist_ok=True)
    output.mkdir(mode=0o700)
    try:
        with data_lock(config, exclusive=True):
            config.verify_storage()
            source_db = config.data_dir / 'juno.db'
            if not source_db.is_file():
                raise StagingError('No existing staging database to back up.')
            with closing(sqlite3.connect(source_db.resolve().as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(output / 'juno.db')) as target:
                source.backup(target)
                target.commit()
                # Materialise a standalone snapshot; only the copy changes journal mode.
                target.execute('PRAGMA journal_mode=DELETE')
                target.commit()
            (output / 'juno.db').chmod(0o600)
            files = [config.data_dir / MARKER]
            feedback = config.data_dir / 'feedback.jsonl'
            if feedback.exists():
                files.append(feedback)
            students = config.data_dir / 'students'
            if students.exists():
                files.extend(students.glob('*.json'))
            for source in files:
                relative = source.relative_to(config.data_dir)
                target = output / relative
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with private_file(target) as handle:
                    handle.write(source.read_text())
            integrity(output / 'juno.db')
            checksums = {str(file.relative_to(output)): digest(file) for file in output.rglob('*') if file.is_file()}
            with private_file(output / 'manifest.json') as handle:
                json.dump({'purpose': 'juno-staging-backup-v1', 'identity': config.identity,
                           'commit': config.commit, 'checksums': checksums}, handle, indent=2)
        return output
    except Exception:
        # This function created the output directory; never remove an existing backup.
        shutil.rmtree(output)
        raise


def verify_backup(config, snapshot):
    snapshot = permitted_snapshot(snapshot)
    try:
        manifest = json.loads((snapshot / 'manifest.json').read_text())
    except (OSError, ValueError) as exc:
        raise StagingError('Invalid backup manifest.') from exc
    if manifest.get('purpose') != 'juno-staging-backup-v1' or manifest.get('identity') != config.identity:
        raise StagingError('Backup belongs to another service or staging instance.')
    checksums = manifest.get('checksums', {})
    if not isinstance(checksums, dict) or not {'juno.db', MARKER} <= set(checksums):
        raise StagingError('Incomplete backup manifest.')
    actual = {str(file.relative_to(snapshot)) for file in snapshot.rglob('*') if file.is_file()} - {'manifest.json'}
    if actual != set(checksums):
        raise StagingError('Backup contains missing or unexpected files.')
    for name, expected in checksums.items():
        relative = Path(name)
        allowed = name in ('juno.db', MARKER, 'feedback.jsonl') or (len(relative.parts) == 2 and relative.parts[0] == 'students' and relative.suffix == '.json')
        if not allowed or relative.is_absolute() or '..' in relative.parts or digest(snapshot / name) != expected:
            raise StagingError('Backup path or checksum verification failed.')
    if json.loads((snapshot / MARKER).read_text()) != config.identity:
        raise StagingError('Backup storage marker does not match staging.')
    integrity(snapshot / 'juno.db')
    return manifest


def restore_backup(config, snapshot, target):
    manifest = verify_backup(config, snapshot)
    target = Path(target)
    no_links(target)
    if target.parent != staging_config.DISK_ROOT or not target.name.startswith('juno-staging-restore-') or target.exists():
        raise StagingError('Restore requires a new staging restore directory; live data is never overwritten.')
    # UUID naming prevents ambiguous or traversal-containing restore paths.
    try:
        uuid.UUID(target.name.removeprefix('juno-staging-restore-'))
    except ValueError as exc:
        raise StagingError('Restore directory suffix must be a UUID.') from exc
    target.mkdir(mode=0o700)
    try:
        for name in manifest['checksums']:
            if name == MARKER:
                continue
            destination = target / name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(Path(snapshot) / name, destination)
            destination.chmod(0o600)
        integrity(target / 'juno.db')
        # Publish the provenance marker last, after every file has been copied.
        with private_file(target / MARKER) as handle:
            json.dump(config.identity, handle)
        return target
    except Exception:
        shutil.rmtree(target)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    backup = sub.add_parser('backup')
    backup.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('--snapshot', type=Path, required=True)
    restore = sub.add_parser('restore')
    restore.add_argument('--snapshot', type=Path, required=True)
    restore.add_argument('--target', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config()
        config.verify_storage()
        if args.operation == 'backup':
            create_backup(config, args.output)
        elif args.operation == 'verify':
            verify_backup(config, args.snapshot)
        else:
            restore_backup(config, args.snapshot, args.target)
        print('Staging ' + args.operation + ' verified.')
        return 0
    except (StagingError, OSError, ValueError, sqlite3.Error):
        print('Staging data operation refused; configuration, provenance, or integrity validation failed.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
