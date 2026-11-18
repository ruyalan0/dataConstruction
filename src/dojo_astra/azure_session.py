import ast
import json
import math
import time
from pathlib import Path
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, AzureOpenAI

from .evidence_context import compact_history
from .visual_context import trim_images


def reject_nonfinite(value):
    raise ValueError(f'Non-finite JSON number: {value}')


def check_finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite JSON number')
    if isinstance(value, dict):
        for child in value.values():
            check_finite(child)
    elif isinstance(value, list):
        for child in value:
            check_finite(child)


def update_working_state(session):
    state = getattr(session, 'working_state', None)
    if state is not None:
        session.messages = [m for m in session.messages if not (
            isinstance(m.get('content'), str) and m['content'].startswith('{"current_working_state":'))]
        session.messages.append({'role': 'user', 'content': json.dumps({'current_working_state': state})})


def prepare_context(session, content):
    update_working_state(session)
    session.messages.append({'role': 'user', 'content': content})
    if session.compact_evidence:
        session.trace.save_context_archive(session.messages, session.calls)
    # Image-removal labels consume text bytes too: budget the final representation.
    visual = trim_images(session.messages, session.image_budget, session.protected_images, session.priority_images)
    if session.compact_evidence:
        session.messages, removed = compact_history(session.messages, max_bytes=session.context_bytes,
                                                     ledger_bytes=session.ledger_bytes,
                                                     protected=session.protected_images)
        session.trace.event('context_compaction', {'messages_removed': removed})
    visual['images_sent'] = sum(p.get('type') == 'image_url' for m in session.messages
                               if isinstance(m.get('content'), list) for p in m['content'])
    session.trace.event('visual_context', visual)


def from_example(path):
    """Read literal Azure configuration without executing the example or copying secrets."""
    tree = ast.parse(Path(path).read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "AzureOpenAI"
    ]
    if len(calls) != 1:
        raise ValueError("Expected exactly one AzureOpenAI constructor")
    config = {k.arg: ast.literal_eval(k.value) for k in calls[0].keywords}
    return AzureOpenAI(**config, max_retries=0, timeout=900)


def complete(client, trace, **kwargs):
    """Five retries of transient transport/server failures; never replay an action."""
    if hasattr(trace, 'record_request'):
        trace.record_request(dict(kwargs, timeout=900))
    for attempt in range(6):
        try:
            return client.chat.completions.create(**kwargs, timeout=900)
        except (APIConnectionError, APIStatusError) as error:
            retryable = (
                isinstance(error, APIConnectionError)
                or error.status_code in {408, 409, 429}
                or error.status_code >= 500
            )
            if not retryable or attempt == 5:
                raise
            delay = min(2 ** (attempt + 1), 30)
            trace.event(
                "api_retry",
                {
                    "attempt": attempt + 1,
                    "delay_seconds": delay,
                    "error_type": type(error).__name__,
                    "status_code": getattr(error, "status_code", None),
                },
            )
            time.sleep(delay)


class ChatSession:
    def __init__(self, client, model, system, trace, image_budget=6):
        self.client, self.model, self.trace = client, model, trace
        self.messages = [{"role": "system", "content": system}]
        self.calls = 0
        self.image_budget = image_budget
        self.protected_images = []
        self.priority_images = []
        self.compact_evidence = False
        self.pending = None
        self.context_bytes, self.ledger_bytes = 65536, 8192

    def request(self, content, tools):
        if self.pending is not None:
            raise RuntimeError('Action result required before next decision')
        prepare_context(self, content)
        started = time.monotonic()
        self.trace.event("request_started", {"call": self.calls + 1, "messages": len(self.messages)})
        response = complete(
            self.client,
            self.trace,
            model=self.model,
            messages=self.messages,
            tools=tools,
            tool_choice="required",
            parallel_tool_calls=False,
            reasoning_effort="none",
            max_tokens=4096,
        )
        self.calls += 1
        self.trace.event("request_finished", {"call": self.calls, "seconds": time.monotonic() - started})
        self.trace.event("response", response.model_dump(mode="json"))
        choice = response.choices[0]
        if choice.finish_reason != "tool_calls" or len(choice.message.tool_calls or []) != 1:
            raise ValueError("Expected exactly one completed tool call")
        message = choice.message.model_dump(exclude_none=True)
        self.messages.append(message)
        call = choice.message.tool_calls[0]
        arguments = json.loads(call.function.arguments, parse_constant=reject_nonfinite)
        check_finite(arguments)
        if not isinstance(arguments, dict):
            raise TypeError('Operation arguments must be an object')
        self.pending = call.id
        return call.id, call.function.name, arguments

    def result(self, call_id, result):
        if self.pending != call_id:
            raise RuntimeError('Stale or duplicate action result')
        self.messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, allow_nan=False)}
        )
        self.pending = None


class JsonSession:
    """Explicit JSON action protocol; no native function tools or implicit downgrade."""

    def __init__(self, client, model, system, trace, reasoning_effort="high", image_budget=6):
        self.client, self.model, self.trace = client, model, trace
        self.reasoning_effort = reasoning_effort
        self.messages = [{"role": "system", "content": system}]
        self.calls = 0
        self.pending = None
        self.image_budget = image_budget
        self.protected_images = []
        self.priority_images = []
        self.compact_evidence = False
        self.context_bytes, self.ledger_bytes = 65536, 8192

    def request(self, content, tools):
        if self.pending is not None:
            raise RuntimeError("Action result required before next decision")
        if self.calls == 0:
            self.messages.append(
                {
                    "role": "system",
                    "content": 'Return only JSON {"name":"operation_name","arguments":{...}}. '
                    "Match the selected operation schema exactly: " + json.dumps(tools),
                }
            )
        prepare_context(self, content)
        started = time.monotonic()
        self.trace.event("request_started", {"call": self.calls + 1, "messages": len(self.messages)})
        response = complete(
            self.client,
            self.trace,
            model=self.model,
            messages=self.messages,
            reasoning_effort=self.reasoning_effort,
            response_format={"type": "json_object"},
            max_tokens=8192,
        )
        self.calls += 1
        self.trace.event("request_finished", {"call": self.calls, "seconds": time.monotonic() - started})
        self.trace.event("response", response.model_dump(mode="json"))
        choice = response.choices[0]
        if choice.finish_reason != "stop":
            raise ValueError("Incomplete JSON decision")
        payload = json.loads(choice.message.content, parse_constant=reject_nonfinite)
        check_finite(payload)
        if set(payload) != {"name", "arguments"} or payload["name"] not in {'act', 'trajectory', 'recall', 'inspect', 'history', 'remember'}:
            raise ValueError("Invalid JSON operation envelope")
        if not isinstance(payload["arguments"], dict):
            raise TypeError("Operation arguments must be an object")
        self.messages.append(choice.message.model_dump(exclude_none=True))
        self.pending = uuid4().hex
        if hasattr(self.trace, "save_session"):
            self.trace.save_session(self.messages, self.calls, self.pending)
        return self.pending, payload["name"], payload["arguments"]

    def result(self, call_id, result):
        if self.pending != call_id:
            raise RuntimeError("Stale or duplicate action result")
        self.messages.append(
            {
                "role": "user",
                "content": json.dumps({"operation_result": result, "operation_id": call_id}, allow_nan=False),
            }
        )
        self.pending = None
        if hasattr(self.trace, "save_session"):
            self.trace.save_session(self.messages, self.calls, self.pending)
