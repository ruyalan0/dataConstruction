from types import SimpleNamespace

import httpx2 as httpx
import pytest
from openai import APITimeoutError, BadRequestError

from dojo_astra.azure_session import complete


def test_timeout_retries_identical_request_without_duplicate_result(monkeypatch):
    calls, events = [], []
    monkeypatch.setattr("dojo_astra.azure_session.time.sleep", lambda _: None)

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            raise APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
        return "one-result"

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    trace = SimpleNamespace(event=lambda *args: events.append(args))
    assert complete(client, trace, messages=[{"role": "user", "content": "same"}]) == "one-result"
    assert calls[0] == calls[1] == calls[2]
    assert calls[0]["timeout"] == 900 and len(events) == 2


def test_invalid_request_is_not_retried():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        raise BadRequestError(
            "invalid",
            response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")),
            body=None,
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(BadRequestError):
        complete(client, SimpleNamespace(event=lambda *args: None))
    assert len(calls) == 1
