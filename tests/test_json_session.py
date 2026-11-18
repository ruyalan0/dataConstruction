import json
from types import SimpleNamespace

import pytest

from dojo_astra.azure_session import JsonSession


class Trace:
    def event(self, kind, value):
        pass


def test_explicit_reasoning_json_protocol_retains_full_messages():
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        message = SimpleNamespace(
            content=json.dumps({"name": "recall", "arguments": {"sequence": 0, "camera": "head"}})
        )
        message.model_dump = lambda **kw: {
            "role": "assistant",
            "content": message.content,
            "reasoning_content": "preserved",
        }
        response = SimpleNamespace(
            id="response-1", choices=[SimpleNamespace(finish_reason="stop", message=message)]
        )
        response.model_dump = lambda **kw: {"id": response.id}
        return response

    session = JsonSession(
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        "gpt-6-astra",
        "system",
        Trace(),
        reasoning_effort="high",
    )
    call, name, args = session.request([], [])
    assert name == "recall" and args["sequence"] == 0
    assert requests[0]["reasoning_effort"] == "high" and "tools" not in requests[0]
    assert session.messages[-1]["reasoning_content"] == "preserved"
    session.result(call, {"executed": True})
    assert session.messages[-1]["role"] == "user"
    with pytest.raises(RuntimeError):
        session.result(call, {})
