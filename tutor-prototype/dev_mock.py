"""Fail-closed local Anthropic stand-in. Never imported by production web.py."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
MARKER = '.juno-local-mock.json'


class MockConfigurationError(RuntimeError):
    pass


def require_local_environment() -> None:
    forbidden = [name for name in os.environ if
                 name.startswith(('ANTHROPIC_', 'RENDER', 'RAILWAY_', 'VERCEL', 'FLY_'))
                 or name in ('PORT', 'DYNO', 'K_SERVICE', 'WEBSITE_INSTANCE_ID')]
    if forbidden:
        raise MockConfigurationError('Local mock refuses API credentials or deployment environment variables.')
    try:
        branch = subprocess.check_output(
            ['git', '-C', str(ROOT), 'branch', '--show-current'], text=True,
            stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MockConfigurationError('Local mock requires a verifiable development checkout.') from exc
    if branch != 'juno-v2-development':
        raise MockConfigurationError('Local mock requires juno-v2-development; detached or production checkouts are refused.')


def require_mock_storage(data_dir: Path) -> None:
    require_local_environment()
    path = Path(data_dir)
    resolved = path.resolve()
    if path.is_symlink() or resolved.parent != Path(tempfile.gettempdir()).resolve() or not resolved.name.startswith('juno-mock-'):
        raise MockConfigurationError('Mock storage must be a dedicated juno-mock-* directory directly in the system temporary directory.')
    # Reject links before reading any file, including the marker itself.
    for entry in resolved.rglob('*'):
        if entry.is_symlink():
            raise MockConfigurationError('Mock storage must not contain symlinks.')
    try:
        marker = json.loads((resolved / MARKER).read_text())
    except (OSError, ValueError) as exc:
        raise MockConfigurationError('Mock storage has no valid synthetic-only marker.') from exc
    if marker != {'purpose': 'juno-local-synthetic-only-v1', 'checkout': str(ROOT)}:
        raise MockConfigurationError('Mock storage marker belongs to a different context.')
    configured = os.environ.get('JUNO_DATA_DIR')
    database = os.environ.get('JUNO_DB_PATH')
    if configured and Path(configured).resolve() != resolved:
        raise MockConfigurationError('Configured data directory differs from mock storage.')
    if database and Path(database).resolve() != resolved / 'juno.db':
        raise MockConfigurationError('Configured database differs from mock storage.')


def synthetic_report() -> dict:
    return {
        'what_we_did': 'Synthetic classroom practice about following up at work.',
        'corrections': [{'tier': '1', 'said': 'I follow yesterday', 'better': 'I followed up yesterday',
                         'note': 'Synthetic report: use the past tense for yesterday.'}],
        'word_traps': [], 'pronunciation': [],
        'vocabulary_learned': [{'term': 'follow up', 'meaning': 'contact someone again'}],
        'what_went_well': ['You completed the synthetic conversation.'],
        'homework': ['Write one synthetic sentence using follow up.'],
        'updated_recurring_error_patterns': ['Synthetic past-tense practice'],
        'next_recommendation': 'Practise follow up in the next synthetic lesson.',
        'grammar_point_taught': '',
    }


class LocalMockAnthropic:
    """Only the synchronous call shapes Juno currently uses are supported.

    No SDK fallback, API key, URL override, or network transport exists here.
    Unknown request options/tools fail instead of silently pretending success.
    """
    def __init__(self, data_dir: Path):
        require_mock_storage(data_dir)
        self.data_dir = Path(data_dir).resolve()
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.help))

    def _validate(self, kwargs: dict, allowed: set) -> None:
        require_mock_storage(self.data_dir)
        if set(kwargs) - allowed or not {'model', 'max_tokens', 'system', 'messages'} <= set(kwargs):
            raise MockConfigurationError('Unsupported local mock request options.')
        messages = kwargs['messages']
        if not isinstance(messages, list) or not messages or any(
            not isinstance(m, dict) or m.get('role') not in ('user', 'assistant') or
            not isinstance(m.get('content'), str) for m in messages
        ):
            raise MockConfigurationError('Unsupported local mock message format.')
        if not isinstance(kwargs['system'], (str, list)):
            raise MockConfigurationError('Unsupported local mock system format.')

    @staticmethod
    def _response(block, stop_reason='end_turn'):
        return SimpleNamespace(content=[block], stop_reason=stop_reason,
                               usage=SimpleNamespace(input_tokens=0, output_tokens=0,
                                                     cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))

    def create(self, **kwargs):
        self._validate(kwargs, {'model', 'max_tokens', 'system', 'messages', 'cache_control', 'tools', 'tool_choice'})
        if 'tools' in kwargs or 'tool_choice' in kwargs:
            tools = kwargs.get('tools', [])
            if len(tools) != 1 or tools[0].get('name') != 'submit_session_report' or kwargs.get('tool_choice') != {'type': 'tool', 'name': 'submit_session_report'}:
                raise MockConfigurationError('Unsupported local mock report tool.')
            return self._response(SimpleNamespace(type='tool_use', name='submit_session_report',
                                                  input=copy.deepcopy(synthetic_report())), 'tool_use')
        opener = kwargs['messages'][-1]['content'] == '(the call has just connected — open it)'
        blocks = kwargs['system']
        system = blocks if isinstance(blocks, str) else '\n'.join(block['text'] for block in blocks)
        memory = False
        marker = 'STUDENT MEMORY (use this to open the call — reference something concrete):'
        if marker in system:
            try:
                student, _ = json.JSONDecoder().raw_decode(system.split(marker, 1)[1].lstrip())
                memory = any(entry.split(' — ')[0].strip().lower() == 'follow up'
                             for entry in student.get('vocab_acquired_log', []))
            except (ValueError, TypeError, AttributeError) as exc:
                raise MockConfigurationError('Unsupported local mock student-memory format.') from exc
        if opener:
            text = ('Local mock Juno: welcome back. Your saved learning memory includes follow up. '
                    if memory else 'Local mock Juno: welcome to this synthetic lesson. ')
            text += 'Tell me about a task at work.'
        else:
            text = 'Local mock Juno: thanks for your answer. Who will you follow up with tomorrow?'
        return self._response(SimpleNamespace(type='text', text=text))

    def help(self, **kwargs):
        self._validate(kwargs, {'model', 'max_tokens', 'system', 'messages', 'output_config', 'betas', 'fallbacks'})
        return self._response(SimpleNamespace(type='text', text='Ayuda simulada: describe una tarea de trabajo con una frase sencilla.'))
