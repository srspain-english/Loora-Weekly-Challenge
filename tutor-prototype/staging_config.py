"""Explicit Render staging identity and storage policy; no production defaults."""
from __future__ import annotations
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISK_ROOT = Path('/var/data')
MARKER = '.juno-staging.json'
BRANCH = 'juno-v2-development'
JUNO_NAMES = {
    'JUNO_ENVIRONMENT', 'JUNO_MODEL_MODE', 'JUNO_STAGING_SYNTHETIC_ONLY',
    'JUNO_STAGING_SERVICE_ID', 'JUNO_STAGING_HOSTNAME', 'JUNO_STAGING_INSTANCE_ID',
    'JUNO_STAGING_COMMIT', 'JUNO_STAGING_INITIALIZE', 'JUNO_ACCESS_PASSPHRASE', 'JUNO_DATA_DIR',
}


class StagingError(RuntimeError):
    pass


def disk_is_mounted(path: Path) -> bool:
    # Recognises bind mounts too; os.path.ismount alone can miss them.
    try:
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            mount = line.split()[4].replace('\\040', ' ')
            if Path(mount) == path:
                return True
    except (OSError, IndexError):
        pass
    return os.path.ismount(path)


def no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise StagingError('Staging paths must not contain symlinks.')
    if path.exists():
        for item in path.rglob('*'):
            if item.is_symlink():
                raise StagingError('Staging storage must not contain symlinks.')


@dataclass(frozen=True)
class StagingConfig:
    service_id: str
    hostname: str
    instance_id: str
    commit: str
    data_dir: Path
    port: int
    initialize: bool

    @property
    def identity(self):
        return {'purpose': 'juno-staging-synthetic-mock-v1', 'service_id': self.service_id,
                'hostname': self.hostname, 'instance_id': self.instance_id, 'branch': BRANCH}

    @property
    def origin(self):
        return 'https://' + self.hostname

    def verify_storage(self):
        no_links(self.data_dir)
        try:
            marker = json.loads((self.data_dir / MARKER).read_text())
        except (OSError, ValueError) as exc:
            raise StagingError('Missing or invalid staging storage marker.') from exc
        if marker != self.identity:
            raise StagingError('Storage belongs to another service or staging instance.')

    def prepare_storage(self):
        no_links(self.data_dir)
        if not (self.data_dir / MARKER).exists():
            if not self.initialize:
                raise StagingError('Storage initialization is disabled; no existing staging marker.')
            if self.data_dir.exists() and any(self.data_dir.iterdir()):
                raise StagingError('Refusing to initialize nonempty unmarked storage.')
            self.data_dir.mkdir(mode=0o700, exist_ok=True)
            with open(self.data_dir / MARKER, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
                json.dump(self.identity, handle)
        self.verify_storage()
        if self.data_dir.stat().st_uid != os.getuid():
            raise StagingError('Storage must belong to the staging runtime user.')
        self.data_dir.chmod(0o700)


def load_config() -> StagingConfig:
    env = os.environ
    if set(name for name in env if name.startswith('JUNO_')) - JUNO_NAMES:
        raise StagingError('Unexpected inherited Juno configuration.')
    forbidden = [name for name in env if name.startswith('ANTHROPIC_') or
                 name in ('DATABASE_URL', 'GH_TOKEN', 'GITHUB_TOKEN', 'RENDER_API_KEY',
                          'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY') or
                 name.endswith(('_API_KEY', '_PASSWORD', '_TOKEN', '_SECRET'))]
    if forbidden:
        raise StagingError('Credentials, proxy overrides, or external database settings are forbidden in mocked staging.')
    if env.get('JUNO_ENVIRONMENT') != 'staging' or env.get('JUNO_MODEL_MODE') != 'mock' or env.get('JUNO_STAGING_SYNTHETIC_ONLY') != 'true':
        raise StagingError('Explicit staging, mock, and synthetic-only settings are required.')
    if env.get('RENDER') != 'true' or env.get('RENDER_GIT_BRANCH') != BRANCH:
        raise StagingError('Only a separate Render staging service on the development branch is permitted.')
    service = env.get('JUNO_STAGING_SERVICE_ID', '')
    host = env.get('JUNO_STAGING_HOSTNAME', '')
    instance = env.get('JUNO_STAGING_INSTANCE_ID', '')
    commit = env.get('JUNO_STAGING_COMMIT', '')
    if not re.fullmatch(r'srv-[a-z0-9]{5,}', service) or env.get('RENDER_SERVICE_ID') != service:
        raise StagingError('Staging service ID does not match Render runtime identity.')
    if not env.get('RENDER_SERVICE_NAME', '').startswith('juno-staging-'):
        raise StagingError('Render service name must explicitly identify staging.')
    if not re.fullmatch(r'juno-staging-[a-z0-9-]+\.onrender\.com', host) or env.get('RENDER_EXTERNAL_URL') != 'https://' + host:
        raise StagingError('Staging HTTPS hostname does not match the Render service.')
    try:
        if str(uuid.UUID(instance)) != instance:
            raise ValueError()
    except ValueError as exc:
        raise StagingError('A stable staging instance UUID is required.') from exc
    if not re.fullmatch(r'[0-9a-f]{40}', commit) or env.get('RENDER_GIT_COMMIT') != commit:
        raise StagingError('An explicitly approved full commit must match Render deployment metadata.')
    try:
        actual = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise StagingError('Cannot verify deployed checkout commit.') from exc
    if actual != commit:
        raise StagingError('Checkout differs from approved staging commit.')
    if not re.fullmatch(r'staging_[A-Za-z0-9_-]{43}', env.get('JUNO_ACCESS_PASSPHRASE', '')):
        raise StagingError('A separately generated staging passphrase is required.')
    if env.get('JUNO_STAGING_INITIALIZE') not in ('true', 'false'):
        raise StagingError('Explicit storage initialization policy is required.')
    raw = env.get('JUNO_DATA_DIR', '')
    path = Path(raw)
    if not raw or not path.is_absolute() or path.parent != DISK_ROOT or not re.fullmatch(r'juno-staging(?:-restore-[a-z0-9-]+)?', path.name):
        raise StagingError('JUNO_DATA_DIR must be an approved directory on the dedicated staging disk.')
    no_links(DISK_ROOT)
    if not disk_is_mounted(DISK_ROOT):
        raise StagingError('The dedicated persistent staging disk is not mounted.')
    try:
        port = int(env.get('PORT', ''))
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError as exc:
        raise StagingError('A valid Render PORT is required.') from exc
    return StagingConfig(service, host, instance, commit, path, port,
                         env['JUNO_STAGING_INITIALIZE'] == 'true')
