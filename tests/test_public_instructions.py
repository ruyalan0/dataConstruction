import json

import pytest
from test_harness_boundary import act, client_for, three_views

from dojo_astra.agent import Agent
from dojo_astra.public_instructions import REVIEWED, VERSION
from dojo_astra.recovery import restore_agent


def task_obs(instruction):
    obs = three_views()
    obs['instruction'] = instruction
    return obs


def summary(request):
    return next(json.loads(p['text']) for m in reversed(request) if isinstance(m['content'], list)
                for p in m['content'] if p['type'] == 'text' and p['text'].startswith('{"instruction"'))


@pytest.mark.parametrize('entry', REVIEWED, ids=lambda e: e.slug)
def test_public_requirements_reach_real_request_without_truth(tmp_path, entry):
    captures = []
    for dirty in (False, True):
        requests = []
        agent = Agent(client_for(iter([act()]), requests), tmp_path / str(dirty),
                      protocol='json-high', compact_evidence=True)
        data = task_obs(entry.instruction)
        if dirty:
            data.update(task_name='other', reward='HIDDEN', success='HIDDEN', step_lim='HIDDEN')
            data['state']['object_pose'] = 'HIDDEN'
        agent.update_obs(data)
        agent.get_action()
        captures.append(requests[0])
        sent = summary(requests[0])
        assert sent['instruction'] == entry.instruction
        assert sent['public_task_requirements'] == entry.as_message()
        assert sent['public_task_requirements']['version'] == VERSION
        assert sent['initial_poses'] == agent.initial_poses
    assert captures[0] == captures[1]
    assert 'HIDDEN' not in json.dumps(captures)


def test_unreviewed_instruction_is_not_given_a_return_goal(tmp_path):
    requests = []
    agent = Agent(client_for(iter([act()]), requests), tmp_path, protocol='json-high')
    agent.update_obs(task_obs('Lift the target object.'))
    agent.get_action()
    assert 'public_task_requirements' not in summary(requests[0])
    assert agent.instruction_contract is None


def test_instruction_change_fails_instead_of_selecting_another_task(tmp_path):
    agent = Agent(None, tmp_path, protocol='json-high')
    agent.update_obs(task_obs(REVIEWED[0].instruction))
    with pytest.raises(ValueError, match='instruction changed'):
        agent.update_obs(task_obs(REVIEWED[1].instruction))


def make_source(tmp_path):
    source = tmp_path / 'source'
    original = Agent(client_for(iter([act()]), []), source / 'agent', protocol='json-high')
    obs = task_obs(REVIEWED[1].instruction)
    original.update_obs(obs)
    original.get_action()
    original.update_obs(obs)
    return source, original, obs


def test_recovery_retains_reviewed_contract_and_exact_messages(tmp_path):
    source, original, obs = make_source(tmp_path)
    requests = []
    restored = Agent(client_for(iter([act()]), requests), tmp_path / 'restored', protocol='json-high')
    restore_agent(restored, source)
    assert restored.session.messages == original.session.messages
    restored.update_obs(obs)
    restored.get_action()
    assert summary(requests[0])['public_task_requirements'] == REVIEWED[1].as_message()


def test_legacy_recovery_does_not_silently_add_new_requirements(tmp_path):
    source, original, obs = make_source(tmp_path)
    path = original.trace.root / 'events.jsonl'
    events = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text(''.join(json.dumps(e) + chr(10) for e in events
                            if e['kind'] != 'public_instruction_contract'))
    requests = []
    restored = Agent(client_for(iter([act()]), requests), tmp_path / 'restored', protocol='json-high')
    restore_agent(restored, source)
    restored.update_obs(obs)
    restored.get_action()
    assert 'public_task_requirements' not in summary(requests[0])


def test_changed_contract_recovery_fails(tmp_path):
    source, original, _ = make_source(tmp_path)
    path = original.trace.root / 'events.jsonl'
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events[0]['data']['contract']['version'] = 'different-version'
    path.write_text(''.join(json.dumps(e) + chr(10) for e in events))
    restored = Agent(None, tmp_path / 'restored', protocol='json-high')
    with pytest.raises(ValueError, match='contract changed'):
        restore_agent(restored, source)
