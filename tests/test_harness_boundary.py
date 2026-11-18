import copy
import json
from types import SimpleNamespace

import numpy as np
from test_control import decision, raw

from dojo_astra.agent import Agent
from dojo_astra.process_policy import decode_observation, encode_observation


def client_for(payloads, captured):
    def create(**kwargs):
        captured.append(copy.deepcopy(kwargs['messages']))
        payload = next(payloads)
        msg = {'role': 'assistant', 'content': json.dumps(payload)}
        message = SimpleNamespace(content=msg['content'], model_dump=lambda **kw: msg)
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=message)])
        response.model_dump = lambda **kw: {'choices': [{'message': msg}]}
        return response
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def three_views():
    data = raw()
    data['vision'] = {name: {'color': np.full((8, 8, 3), i, dtype=np.uint8)}
                      for i, name in enumerate(['cam_head', 'cam_left_wrist', 'cam_right_wrist'])}
    return data


def act(steps=1):
    args = decision().model_dump()
    args['steps'] = steps
    return {'name': 'act', 'arguments': args}


def test_actual_requests_ignore_extra_truth_through_transport(tmp_path):
    captures = []
    for dirty in (False, True):
        data = three_views()
        if dirty:
            data.update(reward='HIDDEN', success='HIDDEN', object_pose='HIDDEN', step_lim='HIDDEN')
            data['state']['contact'] = 'HIDDEN'
            for camera in data['vision'].values():
                camera.update(depth='HIDDEN', segmentation='HIDDEN', intrinsic_matrix='HIDDEN')
        requests = []
        agent = Agent(client_for(iter([act()]), requests), tmp_path / str(dirty), protocol='json-high',
                      pin_current=True, report_steps=True)
        agent.update_obs(decode_observation(encode_observation(data)))
        agent.get_action()
        captures.append(requests)
    assert captures[0] == captures[1]
    assert 'HIDDEN' not in json.dumps(captures)


def test_recall_chain_keeps_all_current_views_and_budget(tmp_path):
    # Seven recalls reproduce the eviction pattern found in stack F171.
    requests = []
    recalls = [{'name': 'recall', 'arguments': {'sequence': 0, 'camera': 'cam_head'}}] * 7
    agent = Agent(client_for(iter([act(), *recalls, act(8)]), requests), tmp_path,
                  protocol='json-high', pin_current=True, report_steps=True)
    agent.update_obs(three_views()); agent.get_action()
    data = three_views()
    for c in data['vision'].values(): c['color'] += 10
    agent.update_obs(data); agent.get_action()
    current = {p['image_url']['url'] for p in agent.session.protected_images}
    for request in requests[1:]:
        images = [p for m in request if isinstance(m['content'], list)
                  for p in m['content'] if p['type'] == 'image_url']
        assert len(images) <= 6
        assert current <= {p['image_url']['url'] for p in images}
    assert agent.executed_steps == 1  # Seven recalls execute no robot actions.
    assert 'observed_robot_state' in json.dumps(requests[-1])
    agent.update_obs(data)
    assert agent.executed_steps == 2
    agent.get_action()  # Continue queued native actions: no harness step ceiling.
    agent.update_obs(data)
    assert agent.executed_steps == 3
    assert len(requests) == 9
    agent.reset()
    assert agent.executed_steps == 0


def test_protected_views_follow_new_observation(tmp_path):
    requests = []
    agent = Agent(client_for(iter([act(), act(), act()]), requests), tmp_path,
                  protocol='json-high', pin_current=True)
    for i in range(3):
        data = three_views()
        for c in data['vision'].values(): c['color'] += i * 10
        agent.update_obs(data); agent.get_action()
    images = [p for m in requests[-1] if isinstance(m['content'], list)
              for p in m['content'] if p['type'] == 'image_url']
    assert len(images) == 6
    assert agent.executed_steps == 2
