from types import SimpleNamespace

from test_control import decision, raw

from dojo_astra.agent import Agent


class Response:
    def __init__(self):
        payload = decision().model_dump()
        payload["steps"] = 1
        import json

        call = SimpleNamespace(
            id="call_1", function=SimpleNamespace(name="act", arguments=json.dumps(payload))
        )
        message = SimpleNamespace(tool_calls=[call])
        message.model_dump = lambda **kw: {
            "role": "assistant",
            "content": "",
            "reasoning_content": "preserve-me",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": "act", "arguments": call.function.arguments},
                }
            ],
        }
        self.choices = [SimpleNamespace(finish_reason="tool_calls", message=message)]

    def model_dump(self, **kwargs):
        return {"id": "test-response"}


def test_tool_result_waits_for_observation_and_reset_isolates(tmp_path):
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return Response()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    agent = Agent(client, tmp_path)
    agent.update_obs(raw())
    assert len(agent.get_action()) == 1
    assert agent.session.messages[-1]["role"] == "assistant"
    assert agent.session.messages[-1]["reasoning_content"] == "preserve-me"
    agent.update_obs(raw())
    assert agent.session.messages[-1]["role"] == "tool"
    assert agent.session.messages[-1]["tool_call_id"] == "call_1"
    old_episode = agent.episode
    agent.reset()
    assert agent.episode != old_episode
    assert len(agent.session.messages) == 1
    assert agent.pending_call is None and agent.executor.obs is None
    assert not agent.trace.frames
    assert requests[0]["reasoning_effort"] == "none"
