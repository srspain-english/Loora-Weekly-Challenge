"""OpenAI-only boundary. Construction/imports never require credentials or network.

No automatic retries: a lost response may have been billed. The durable budget
reservation remains held when final usage is unknown. Tests replace responses.create.
"""
from __future__ import annotations

import copy
import json
import os
from types import SimpleNamespace

import jsonschema

import store


class AIError(Exception):
    """Safe to show to a student; provider payloads never leave this module."""


class Responses:
    def __init__(self):
        self._client = None

    def create(self, **kwargs):
        if self._client is None:
            from openai import OpenAI
            if not os.environ.get("OPENAI_API_KEY"):
                raise AIError("OpenAI is not configured. Your class is saved.")
            self._client = OpenAI(max_retries=0, timeout=45.0)
        return self._client.responses.create(**kwargs)


def strict_schema(schema):
    """Keep the report contract, adding the object restrictions OpenAI requires."""
    result = copy.deepcopy(schema)
    def visit(item):
        if isinstance(item, dict):
            if item.get("type") == "object":
                item["additionalProperties"] = False
                item["required"] = list(item.get("properties", {}))
            for value in item.values():
                visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)
    visit(result)
    return result


def instructions(system):
    # Old saved calls contain text blocks with provider cache metadata. Read
    # their text without rewriting the saved call or any teaching instruction.
    return "\n".join(b["text"] for b in system) if isinstance(system, list) else system


class AIError(Exception):
    """Safe, student-facing error; never an SDK exception payload."""


class Refusal(AIError):
    pass


class Connection:
    def __init__(self):
        self.responses = Responses()

    def complete(self, *, model, system, messages, student_id="", call_id="",
                 kind="turn", max_output_tokens=2048, effort="none", schema=None,
                 on_delta=None):
        request = dict(model=model, instructions=instructions(system),
                       input=copy.deepcopy(messages), store=False,
                       max_output_tokens=max_output_tokens,
                       reasoning={"effort": effort}, service_tier="default",
                       prompt_cache_options={"ttl": "30m"})
        if schema is not None:
            request["text"] = {"format": {"type": "json_schema", "name": "session_report",
                                          "strict": True, "schema": strict_schema(schema)}}
        if on_delta is not None:
            request["stream"] = True
        # A UTF-8 byte bound plus framing allowance deliberately over-reserves
        # input. Reject very large requests rather than silently truncate lessons.
        input_bound = len(json.dumps(request, ensure_ascii=False).encode("utf-8")) + 16384
        if input_bound > 200_000:
            raise AIError("This class is too long to process safely. Your progress is saved.")
        reservation = store.reserve_ai(model, input_bound, max_output_tokens,
                                       student_id=student_id, call_id=call_id, kind=kind)
        final = None
        try:
            if on_delta is None:
                final = self.responses.create(**request)
            else:
                stream = self.responses.create(**request)
                try:
                    for event in stream:
                        if event.type == "response.output_text.delta":
                            on_delta(event.delta)
                        elif event.type == "response.completed":
                            final = event.response
                        elif event.type in ("response.failed", "response.incomplete", "error"):
                            final = getattr(event, "response", None)
                            raise AIError("The reply could not finish. Your class is saved.")
                finally:
                    close = getattr(stream, "close", None)
                    if close:
                        close()
            if final is None or final.status != "completed":
                raise AIError("The reply could not finish. Your class is saved.")
            for item in final.output:
                for block in getattr(item, "content", []):
                    if block.type == "refusal":
                        raise Refusal("Juno cannot answer that request. Try a different question.")
            text = final.output_text.strip()
            if not text:
                raise AIError("Juno could not produce a reply. Your class is saved.")
            report = None
            if schema is not None:
                report = json.loads(text)
                jsonschema.validate(report, strict_schema(schema))
                # Structured output is not proof: quotations must be grounded.
                evidence = "\n".join(m["content"] for m in (messages[:-1] if kind == "report" else messages) if m["role"] == "user" and m["content"] != "(the call has just connected — open it)")
                for correction in report["corrections"]:
                    if not correction["said"].strip() or correction["said"] not in evidence:
                        raise AIError("The report could not be verified. Your class is saved.")
            return SimpleNamespace(text=text, report=report, usage=final.usage, model=model)
        except (AIError, store.LimitReached):
            raise
        except Exception:
            # Never log SDK exceptions: they can contain keys and lesson text.
            raise AIError("Juno could not complete the request. Your class is saved.") from None
        finally:
            store.settle_ai(reservation, getattr(final, "usage", None))
