import base64
import io
import json

import pytest
from PIL import Image
from test_harness_boundary import act, client_for, three_views

from dojo_astra.agent import Agent
from dojo_astra.evidence_context import compact_history, text_bytes
from dojo_astra.evidence_tools import Inspect
from dojo_astra.recovery import restore_agent
from dojo_astra.trace_store import TraceStore


def test_thousand_decisions_keep_bounded_text_and_latest_result():
    messages = [{'role': 'system', 'content': 'S' * 4096}]
    sizes = []
    for i in range(1000):
        decision = act()
        decision['arguments']['note'] = f'hypothesis-{i} ' + 'x' * 1800
        messages.extend([
            {'role': 'user', 'content': [{'type': 'text', 'text': f'frame {i}'}]},
            {'role': 'assistant', 'content': json.dumps(decision)},
            {'role': 'user', 'content': json.dumps({'operation_result': {
                'before': i, 'after': i + 1, 'pose_reached': False, 'interrupted': False}})},
        ])
        messages, _ = compact_history(messages, max_bytes=32768, ledger_bytes=4096)
        sizes.append(text_bytes(messages))
        assert json.loads(messages[-1]['content'])['operation_result']['after'] == i + 1
    assert max(sizes) <= 32768
    assert max(sizes[700:]) - min(sizes[700:]) < 1000
    assert 'hypothesis-0 ' not in json.dumps(messages)


def test_request_manifest_reconstructs_pixels_and_messages_without_base64_snapshots(tmp_path):
    requests = []
    agent = Agent(client_for(iter([act()] * 10), requests), tmp_path, protocol='json-high',
                  pin_current=True, compact_evidence=True)
    for _ in range(10):
        agent.update_obs(three_views())
        agent.get_action()
    records = [json.loads(line) for line in (agent.trace.root / 'requests.jsonl').read_text().splitlines()]
    assert len(records) == 10
    for request, record in zip(requests, records, strict=True):
        assert TraceStore.load_messages(agent.trace.root, record['messages']) == request
        assert record['active_text_bytes'] <= 65536
    for file in ['requests.jsonl', 'session.json', 'context-archive.jsonl']:
        assert 'data:image' not in (agent.trace.root / file).read_text()
    assert len(list((agent.trace.root / 'blobs').iterdir())) == 3


def claim(identifier, text, supersedes=()):
    return {'claim_id': identifier, 'text': text, 'model_assessed_status': 'hypothesis',
            'evidence_refs': [{'sequence': 0, 'camera': 'cam_head'}], 'supersedes': list(supersedes)}


def test_superseded_claim_leaves_active_state_but_remains_queryable_and_recoverable(tmp_path):
    operations = [
        {'name': 'remember', 'arguments': {'subgoal': 'inspect grip', 'updates': [claim('grip', 'May be held')]}},
        {'name': 'remember', 'arguments': {'subgoal': 'recheck', 'updates': [claim('empty', 'Not held', ['grip'])]}},
        {'name': 'history', 'arguments': {'kinds': ['memory_update']}}, act(),
    ]
    requests = []
    source = tmp_path / 'source'
    agent = Agent(client_for(iter(operations), requests), source / 'agent', protocol='json-high', compact_evidence=True)
    agent.update_obs(three_views())
    agent.get_action()
    agent.update_obs(three_views())
    assert [c['claim_id'] for c in agent.evidence.snapshot()['active_claims']] == ['empty']
    assert 'May be held' in json.dumps(requests[-1])  # Available as archived history, not active truth.
    active = [json.loads(m['content'])['current_working_state'] for m in requests[-1]
              if isinstance(m['content'], str) and m['content'].startswith('{"current_working_state":')]
    assert len(active) == 1
    assert [c['claim_id'] for c in active[0]['active_claims']] == ['empty']
    restored = Agent(None, tmp_path / 'restored', protocol='json-high', compact_evidence=True)
    restore_agent(restored, source)
    assert restored.evidence.snapshot() == agent.evidence.snapshot()


def test_paired_crop_inspection_keeps_current_views_and_correct_pixels(tmp_path):
    requests = []
    inspect = {'name': 'inspect', 'arguments': {'frames': [0, 1], 'cameras': ['cam_head', 'cam_right_wrist'],
               'roi': [1, 1, 6, 6], 'compare': 'before_after', 'scale': 2}}
    agent = Agent(client_for(iter([act(), act(), inspect, act()]), requests), tmp_path, protocol='json-high',
                  pin_current=True, compact_evidence=True)
    for value in (0, 20, 40):
        obs = three_views()
        for camera in obs['vision'].values():
            camera['color'] += value
        agent.update_obs(obs)
        agent.get_action()
    images = [p for m in requests[-1] if isinstance(m['content'], list)
              for p in m['content'] if p.get('type') == 'image_url']
    assert len(images) <= 6
    current = {agent.trace.image_content(2, c)['image_url']['url'] for c in obs['vision']}
    assert current <= {p['image_url']['url'] for p in images}
    sheets = [Image.open(io.BytesIO(base64.b64decode(p['image_url']['url'].split(',')[1]))) for p in images]
    sheets = [im for im in sheets if im.size == (20, 30)]
    assert len(sheets) == 2
    assert sheets[0].getpixel((3, 25)) == (0, 0, 0)
    assert sheets[0].getpixel((13, 25)) == (20, 20, 20)
    assert 'observed_robot_state' in json.dumps(requests[-1])
    assert agent.executed_steps == 2  # Inspection dispatched no physical action.


@pytest.mark.parametrize('kwargs', [{'frames': [True]}, {'frames': [-1]}, {'roi': [0, 0, 1.5, 2]}])
def test_inspection_rejects_noninteger_references(kwargs):
    with pytest.raises(ValueError):
        Inspect.model_validate({'frames': [0], 'cameras': ['cam_head'], **kwargs})


def test_duplicate_recall_does_not_consume_distinct_image_slots(tmp_path):
    requests = []
    recall = {'name': 'recall', 'arguments': {'sequence': 0, 'camera': 'cam_head'}}
    agent = Agent(client_for(iter([act(), *([recall] * 7), act()]), requests), tmp_path,
                  protocol='json-high', pin_current=True, compact_evidence=True)
    agent.update_obs(three_views())
    agent.get_action()
    obs = three_views()
    for c in obs['vision'].values():
        c['color'] += 20
    agent.update_obs(obs)
    agent.get_action()
    urls = [p['image_url']['url'] for m in requests[-1] if isinstance(m['content'], list)
            for p in m['content'] if p.get('type') == 'image_url']
    assert len(urls) == len(set(urls))
    assert agent.trace.image_content(0, 'cam_head')['image_url']['url'] in urls


def test_action_before_views_return_after_recall_chain(tmp_path):
    requests = []
    recalls = [{'name': 'recall', 'arguments': {'sequence': 0, 'camera': camera}}
               for camera in three_views()['vision']]
    agent = Agent(client_for(iter([act(), act(), *recalls, act(), act()]), requests), tmp_path,
                  protocol='json-high', pin_current=True, compact_evidence=True)
    for frame in range(4):
        obs = three_views()
        for camera in obs['vision'].values():
            camera['color'] += frame * 20
        agent.update_obs(obs)
        agent.get_action()
    urls = [p['image_url']['url'] for m in requests[-1] if isinstance(m['content'], list)
            for p in m['content'] if p.get('type') == 'image_url']
    expected = {agent.trace.image_content(frame, camera)['image_url']['url']
                for frame in (2, 3) for camera in obs['vision']}
    assert set(urls) == expected
    assert 'observed_robot_state_before' in json.dumps(requests[-1])
