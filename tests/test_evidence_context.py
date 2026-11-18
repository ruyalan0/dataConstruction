import json

from test_control import decision, raw
from test_harness_boundary import act, client_for, three_views

from dojo_astra.agent import Agent
from dojo_astra.evidence_context import compact_history
from dojo_astra.executor import Executor
from dojo_astra.observation_adapter import adapt


def test_compaction_retains_uncertainty_and_correction_verbatim():
    notes = ['F0: grasp uncertain, no contact proven.', 'F1: prior grasp claim contradicted; object stayed.']
    messages = [{'role': 'system', 'content': 'allowed inputs only'}]
    for i in range(30):
        payload = act()
        payload['arguments']['note'] = notes[i % 2]
        messages.extend([
            {'role': 'user', 'content': [{'type': 'text', 'text': json.dumps({'poses': [0]*200})}]},
            {'role': 'assistant', 'content': json.dumps(payload)},
            {'role': 'user', 'content': json.dumps({'operation_result': {'before': i, 'after': i+1,
                'pose_reached': False, 'interrupted': False, 'error_mm': {'left': 4}, 'error_deg': {'left': 0}}})},
        ])
    compact, _ = compact_history(messages)
    assert len(json.dumps(compact)) < len(json.dumps(messages))
    for note in notes: assert note in json.dumps(compact)
    compact2, _ = compact_history(compact)
    assert 'contradicted' in json.dumps(compact2)
    assert 'model_note_verbatim_unverified' in json.dumps(compact2)


def test_tracking_streak_uses_completed_segments_and_resets():
    ex = Executor(detailed_feedback=True)
    ex.observe(adapt(raw(), 0))
    seq = 0
    for segment in range(2):
        ex.load(decision())
        while ex.remaining:
            ex.next_action(); seq += 1
            ex.observe(adapt(raw(), seq))
        assert ex.feedback['consecutive_completed_segments_not_reached'] == segment + 1
        assert not ex.feedback['interrupted']
    ex.load(decision())
    target = ex.target
    while ex.remaining:
        ex.next_action(); seq += 1
        data = raw()
        for arm in ('left', 'right'): data['state'][f'{arm}_ee_pose'] = target[f'{arm}_ee_pose']
        ex.observe(adapt(data, seq))
    assert ex.feedback['consecutive_completed_segments_not_reached'] == 0
    assert ex.feedback['evidence_scope'].endswith('object_state_unknown')


def test_compacted_agent_keeps_current_rgb_and_source_archive(tmp_path):
    requests=[]
    agent=Agent(client_for(iter([act() for _ in range(12)]),requests),tmp_path,
                protocol='json-high',pin_current=True,report_steps=True,
                detailed_feedback=True,compact_evidence=True)
    for _ in range(12):
        agent.update_obs(three_views());agent.get_action()
    assert (agent.trace.root/'context-archive.jsonl').is_file()
    assert 'evidence_ledger' in json.dumps(requests[-1])
    images=[p for m in requests[-1] if isinstance(m['content'],list)
            for p in m['content'] if p['type']=='image_url']
    assert 3 <= len(images) <= 6
    assert agent.executed_steps==11
