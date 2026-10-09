"""Deterministic staging-only responses; no SDK transport or credential fallback."""
from __future__ import annotations
import json
from types import SimpleNamespace
from staging_config import StagingError, load_config


def report():
    return {'what_we_did': 'Synthetic staging practice about following up at work.',
            'corrections': [], 'word_traps': [], 'pronunciation': [],
            'vocabulary_learned': [{'term': 'follow up', 'meaning': 'contact someone again'}],
            'what_went_well': ['Completed the synthetic staging conversation.'],
            'homework': ['Write a synthetic sentence using follow up.'],
            'updated_recurring_error_patterns': [],
            'next_recommendation': 'Practise follow up in the next synthetic lesson.',
            'grammar_point_taught': ''}


class StagingMockAnthropic:
    def __init__(self, config):
        config.verify_storage()
        self.config = config
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.help))

    def validate(self, kwargs, allowed):
        if load_config() != self.config:
            raise StagingError('Staging runtime configuration changed.')
        self.config.verify_storage()
        if set(kwargs) - allowed or not {'model', 'max_tokens', 'system', 'messages'} <= set(kwargs):
            raise StagingError('Unsupported staging mock request options.')
        messages = kwargs['messages']
        if not isinstance(messages, list) or not messages or any(
            not isinstance(m, dict) or m.get('role') not in ('user', 'assistant') or
            not isinstance(m.get('content'), str) for m in messages):
            raise StagingError('Unsupported staging mock messages.')
        if not isinstance(kwargs['system'], (str, list)):
            raise StagingError('Unsupported staging mock system format.')

    @staticmethod
    def response(block, reason='end_turn'):
        return SimpleNamespace(content=[block], stop_reason=reason,
                               usage=SimpleNamespace(input_tokens=0, output_tokens=0,
                                                     cache_read_input_tokens=0, cache_creation_input_tokens=0))

    def create(self, **kwargs):
        self.validate(kwargs, {'model', 'max_tokens', 'system', 'messages', 'cache_control', 'tools', 'tool_choice'})
        if 'tools' in kwargs or 'tool_choice' in kwargs:
            tools = kwargs.get('tools', [])
            if len(tools) != 1 or tools[0].get('name') != 'submit_session_report' or kwargs.get('tool_choice') != {'type': 'tool', 'name': 'submit_session_report'}:
                raise StagingError('Unsupported staging mock tool.')
            return self.response(SimpleNamespace(type='tool_use', name='submit_session_report', input=report()), 'tool_use')
        system = kwargs['system']
        system = system if isinstance(system, str) else '\n'.join(block['text'] for block in system)
        memory = False
        marker = 'STUDENT MEMORY (use this to open the call — reference something concrete):'
        if marker in system:
            try:
                student, _ = json.JSONDecoder().raw_decode(system.split(marker, 1)[1].lstrip())
                memory = any(entry.split(' — ')[0].strip().lower() == 'follow up' for entry in student.get('vocab_acquired_log', []))
            except (ValueError, TypeError, AttributeError) as exc:
                raise StagingError('Unsupported staging mock memory format.') from exc
        opener = kwargs['messages'][-1]['content'] == '(the call has just connected — open it)'
        text = 'Staging mock Juno: tell me about a synthetic task at work.'
        if opener and memory:
            text = 'Staging mock Juno: your saved learning memory includes follow up. Tell me about a synthetic task.'
        elif not opener:
            text = 'Staging mock Juno: who will you follow up with tomorrow?'
        return self.response(SimpleNamespace(type='text', text=text))

    def help(self, **kwargs):
        self.validate(kwargs, {'model', 'max_tokens', 'system', 'messages', 'output_config', 'betas', 'fallbacks'})
        return self.response(SimpleNamespace(type='text', text='Ayuda simulada: describe una tarea de trabajo.'))
