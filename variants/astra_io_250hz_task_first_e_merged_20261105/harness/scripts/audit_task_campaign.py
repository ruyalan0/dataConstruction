"""Verify completed campaign scope, actual requests, native results and durability."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

import audit_five_layout_completion as audit
import diagnose_suite as base
from prepare_eight_eval import save
import run_eight_eval as runner
from report_eight_eval import end_reason
from verify_provider_harness import verify_runtime


def read(path):
    return json.loads(path.read_text())


def verify(output):
    plan, ready, campaign = [read(output / name) for name in ('plan.json', 'ready.json', 'campaign.json')]
    expected = {(c['task'], c['layout']) for c in plan['cases']}
    assert len(expected) == plan['total'] == read(output / 'completed.json')['cases']
    assert campaign['source_commit'] == ready['source_commit'] == plan['source_commit']
    assert campaign['azure_concurrency_limit'] == 3
    for name, sha in ready['pinned_files'].items(): assert base.digest(output / name) == sha, name
    approved_runtime = audit.runtime_boundary()
    harness = Path(plan['harness']) if 'harness' in plan else base.HARNESS
    runtime = verify_runtime(harness)
    assert runtime['commit'] == plan['harness_commit']
    if 'runtime_boundary' in plan:
        assert runtime == plan['runtime_boundary']
    from egg_harness.native_responses import TOOL_INSTRUCTIONS
    from egg_harness.skill_policy import system_prompt
    checked, intervals = [], []
    scratch = Path(campaign['scratch'])
    for spec in plan['cases']:
        task, layout, provider = spec['task'], spec['layout'], spec['provider']
        name = f'{task}-L{layout}-attempt1'; folder = output / 'cases' / name; case = scratch / 'runs' / name
        result, launch, native = read(folder / 'result.json'), read(case / 'launch.json'), read(case / 'episode/runner-result.json')
        assert result['native'] == native and native['steps'] > 0
        assert result['model_calls'] > 0 and result['status'] != 'execution_error'
        reason = end_reason(folder, result)
        assert reason in {'native_success', 'native_unsuccessful_end', 'astra_stopped',
                          'model_call_budget_exhausted'}, (name, reason)
        config = read(output / f'configs/{task}.json'); config['astra_provider'] = provider
        assert config == read(case / 'config.json') == read(folder / 'config.json')
        assert launch['task'] == task and launch['layout_index'] == layout and launch['native_initial_state']
        assert launch['provider'] == provider and launch['action_interface'] == 'joint'
        assert launch['orchestration_commit'] == plan['source_commit'] and launch['harness_commit'] == plan['harness_commit']
        assert Path(launch['baseline']).resolve() == harness.resolve()
        provenance = read(case / 'episode/provenance.json')
        assert provenance['seeds'] == [layout] and provenance['task'] == task
        assert provenance['action_interface'] == 'joint' and not provenance['object_teleportation'] and not provenance['depth_calibration']
        model = case / 'policy/model'; tools = read(model / 'native-tools.json')
        assert tools['provider'] == provider and tools['provider_allocation'] is None
        assert tools['api'] == 'responses'
        if provider == 'zzy-astra':
            assert runtime['zzy_supported'] and tools['base_url'] == 'https://zzy.example.invalid'
            assert config['model'] == 'gpt-6-astra'
        control = next(t for t in tools['tools'] if t['name'] == 'control_arms')
        assert set(control['parameters']['$defs']) == {'JointArm'}
        assert control['parameters']['$defs']['JointArm']['properties']['control']['const'] == 'joint'
        assert read(case / 'policy/skills.json') == {
            str(output / 'skills' / s / 'SKILL.md'): ready['pinned_files'][f'skills/{s}/SKILL.md'] for s in config['skill_names']}
        expected_system = TOOL_INSTRUCTIONS + '\n' + system_prompt('joint') + '\n' + '\n'.join(
            (output / 'skills' / s / 'SKILL.md').read_text() for s in config['skill_names'])
        requests = sorted(model.glob('*.request.json'))
        assert len(requests) == result['model_calls']
        assert [p.name for p in requests] == [f'{i:04d}.request.json' for i in range(1, len(requests) + 1)]
        assert read(requests[0])['step'] == 0
        for p in requests:
            request = read(p); context = json.loads(request['question'])
            assert request['provider'] == provider and request['base_url'] == tools['base_url']
            assert request['api'] == 'responses' and request['model'] == config['model']
            assert request['reasoning_effort'] == config['reasoning_effort']
            assert request['system'] == expected_system
            assert context['public_task_requirements'] == config['public_task_requirements'] and context['action_interface'] == 'joint'
        observation = audit.public_observation_boundary(case)
        policy = read(model / 'transport-policy.json')
        assert policy['azure_request_limit'] == 3 and policy['max_retries'] == 3
        assert policy['same_request_and_scene'] and policy['robot_action_retries'] == 0 and not policy['provider_switching']
        retry_path = model / 'transport-retries.jsonl'; groups = defaultdict(list)
        if retry_path.exists():
            for line in retry_path.read_text().splitlines():
                row = json.loads(line); assert row['provider'] == provider; groups[row['call']].append(row)
        for rows in groups.values():
            assert [r['attempt'] for r in rows] == list(range(1, len(rows) + 1)) and len(rows) <= 4
            assert len({r['request_sha256'] for r in rows}) == 1 and len({r['step'] for r in rows}) == 1
            assert len({r['scene_sha256'] for r in rows if 'scene_sha256' in r}) <= 1
            for row in rows:
                if row.get('will_retry'):
                    status = row.get('http_status')
                    assert row['type'] in ('APIConnectionError', 'APITimeoutError') or status == 429 or (status is not None and 500 <= status < 600)
        if provider == 'azure-astra':
            cap = [json.loads(line) for line in (model / 'api-concurrency.jsonl').read_text().splitlines()]
            assert len(cap) >= len(requests)
            for row in cap:
                assert row['limit'] == 3 and row['slot'] in range(3) and row['worker'] == campaign['worker']
                intervals.extend([(row['acquired_ns'], 1), (row['released_ns'], -1)])
        shards = audit.case_durability(folder, campaign['hdfs_root'])
        checked.append({'task': task, 'layout': layout, 'provider': provider, 'requests': len(requests),
            'native': native, 'end_reason': reason, 'public_observations': observation,
            'retried_calls': len(groups), 'shards': len(shards),
            'result_sha256': base.digest(folder / 'result.json')})
    assert {(r['task'], r['layout']) for r in checked} == expected
    live = peak = 0
    for _, delta in sorted(intervals): live += delta; peak = max(peak, live)
    assert live == 0 and peak <= 3
    record = {'at': runner.now(), 'requirements_verified': True, 'scope': plan['phase'], 'cases': checked,
        'runtime': runtime, 'original_runtime': approved_runtime, 'azure_observed_peak': peak, 'total_requests': sum(r['requests'] for r in checked),
        'provider_counts': dict(Counter(r['provider'] for r in checked))}
    save(output / 'completion-audit.json', record)
    print(json.dumps({k: v for k, v in record.items() if k not in ('cases', 'runtime')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--campaign', type=Path, required=True)
    verify(parser.parse_args().campaign.resolve())
