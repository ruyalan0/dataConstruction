import json
from types import SimpleNamespace

import pytest
from test_harness_boundary import act, client_for, three_views

from dojo_astra.agent import Agent
from dojo_astra.deploy import Model, eval_one_episode


class Environment:
    def __init__(self, steps):
        self.steps = steps
        self.count = 0

    def is_episode_end(self):
        return self.count == self.steps

    def get_obs(self):
        return three_views()

    def take_action(self, action):
        self.count += 1


def events(agent, kind):
    return [e['data'] for e in map(json.loads, (agent.trace.root / 'events.jsonl').read_text().splitlines())
            if e['kind'] == kind]


def test_official_keyword_records_terminal_observation_and_closes_segment(tmp_path):
    requests = []
    agent = Agent(client_for(iter([act(8)]), requests), tmp_path, protocol='json-high')
    model = Model.__new__(Model)
    model.agent = agent
    client = SimpleNamespace(call=lambda func_name, **kwargs: getattr(model, func_name)(**kwargs))
    env = Environment(3)
    eval_one_episode(TASK_ENV=env, model_client=client)
    assert [e['sequence'] for e in events(agent, 'observation')] == [0, 1, 2, 3]
    assert len(events(agent, 'native_action')) == 3
    assert len(requests) == 1
    assert agent.executor.remaining == 0
    assert agent.session.pending is None
    assert events(agent, 'feedback')[-1]['terminated_by_environment'] is True
    with pytest.raises(RuntimeError, match='ended'):
        agent.get_action()


@pytest.mark.parametrize('invalid', [
    {'name': 'act', 'arguments': {**act()['arguments'], 'left': {
        **act()['arguments']['left'], 'operation': 'rotate', 'amount': 16}}},
    {'name': 'recall', 'arguments': {'sequence': 999, 'camera': 'cam_head'}},
    {'name': 'recall', 'arguments': {'sequence': -1, 'camera': 'cam_head'}},
])
def test_invalid_tool_is_repaired_at_same_observation_without_execution(tmp_path, invalid):
    requests = []
    agent = Agent(client_for(iter([invalid, act()]), requests), tmp_path, protocol='json-high')
    agent.update_obs(three_views())
    action = agent.get_action()
    assert len(action) == 1
    assert len(requests) == 2
    assert len(events(agent, 'observation')) == 1
    assert len(events(agent, 'native_action')) == 1
    assert 'tool_validation_error' in json.dumps(requests[-1])
    assert len(events(agent, 'tool_error')) == 1
    agent.update_obs(three_views())
    assert agent.session.pending is None


def test_repair_limit_finishes_error_result_before_failing(tmp_path):
    invalid = {'name': 'recall', 'arguments': {'sequence': 999, 'camera': 'cam_head'}}
    requests = []
    agent = Agent(client_for(iter([invalid] * 10), requests), tmp_path, protocol='json-high')
    agent.update_obs(three_views())
    with pytest.raises(RuntimeError, match='repair'):
        agent.get_action()
    assert len(requests) == 4
    assert agent.session.pending is None
    assert events(agent, 'native_action') == []


def test_formal_and_process_config_use_same_effective_contract(tmp_path, monkeypatch):
    from dojo_astra.config import HarnessConfig

    cfg = HarnessConfig.for_profile('evidence', trace_root=str(tmp_path / 'trace'), model='deployment-9',
                                    image_budget=9)
    monkeypatch.setattr('dojo_astra.azure_session.from_example', lambda path: client_for(iter([]), []))
    model = Model(cfg.model_dump())
    restored = HarnessConfig.model_validate_json(cfg.model_dump_json()).create_agent(client_for(iter([]), []))
    assert model.agent.session.model == restored.session.model == 'deployment-9'
    assert model.agent.image_budget == restored.image_budget == 9
    assert model.agent.compact_evidence and restored.compact_evidence
    a = json.loads((model.agent.trace.root / 'harness-config.json').read_text())
    b = json.loads((restored.trace.root / 'harness-config.json').read_text())
    assert a == b
    with pytest.raises(ValueError, match='extra'):
        Model({**cfg.model_dump(), 'image_budegt': 9})


def test_nonfinite_response_and_corrupt_observation_are_fatal(tmp_path):
    bad = act()
    bad['arguments']['left']['amount'] = float('nan')
    agent = Agent(client_for(iter([bad]), []), tmp_path / 'response', protocol='json-high')
    agent.update_obs(three_views())
    with pytest.raises(ValueError, match='Non-finite'):
        agent.get_action()
    assert events(agent, 'tool_error') == []
    assert events(agent, 'native_action') == []
    other = Agent(None, tmp_path / 'obs')
    obs = three_views()
    obs['state']['left_ee_pose'][0] = float('nan')
    with pytest.raises(ValueError, match='finite'):
        other.update_obs(obs)
