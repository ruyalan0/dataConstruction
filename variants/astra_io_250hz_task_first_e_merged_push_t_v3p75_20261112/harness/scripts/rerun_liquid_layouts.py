"""Five native-initial Azure/joint episodes with fixed skills and durable evidence."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import audit_five_layout_completion as audit
import five_layout_suite as suite
from prepare_eight_eval import ASSET_REPO, BASE, REVISION, save
from report_eight_eval import end_reason, outcome_text
from restore_task_assets import restore

TASK = 'pour_liquid_into_cup'
LAYOUTS = list(range(20, 25))
NAME = 'liquid-rerun-b7d832b'
SCRATCH = Path('/tmp/astra-liquid-rerun-b7d832b-20260917')
HDFS = suite.HDFS + '/' + NAME
PROGRESS = 'LIQUID-RERUN-PROGRESS.md'


def prepare(root, output):
    assert not suite.git(root, 'status', '--porcelain')
    output.mkdir(parents=True, exist_ok=False)
    source = root/'artifacts/five-layout-suite/L24'
    config = suite.read(source/f'configs/{TASK}.json')
    names = config['skill_names']
    for name in names:
        target = output/'skills'/name/'SKILL.md'
        target.parent.mkdir(parents=True)
        shutil.copyfile(root/'skills'/name/'SKILL.md', target)
    shutil.copyfile(source/'calibration.json', output/'calibration.json')
    shutil.copytree(root/'scripts', output/'source/scripts', ignore=shutil.ignore_patterns('__pycache__'))
    config.update(skills_root=str(output/'skills'), memory=str(output/'calibration.json'))
    assert config['astra_provider'] == 'azure-astra' and config['action_interface'] == 'joint'
    assert config['max_model_calls'] == 400 and 'continuation_request' not in config
    save(output/f'configs/{TASK}.json', config)
    before = suite.read(root/'artifacts/five-layout-suite/coverage.json')
    parents = [row for row in before['rows'] if row['task'] == TASK]
    assert sorted(row['layout'] for row in parents) == LAYOUTS
    save(output/'previous-results.json', parents)
    pinned = {str(p.relative_to(output)): suite.base.digest(p)
              for folder in ('skills', 'configs', 'source')
              for p in (output/folder).rglob('*') if p.is_file()}
    pinned['calibration.json'] = suite.base.digest(output/'calibration.json')
    pinned['previous-results.json'] = suite.base.digest(output/'previous-results.json')
    save(output/'plan.json', {'at': suite.runner.now(), 'task': TASK, 'layouts': LAYOUTS,
         'source_commit': suite.git(root, 'rev-parse', 'HEAD'),
         'skill_commit': suite.git(root, 'log', '-1', '--format=%H', '--', 'skills'),
         'harness_commit': suite.base.HARNESS_COMMIT, 'provider': 'azure-astra',
         'action_interface': 'joint', 'native_initial_state': True, 'attempt': 2,
         'pinned_files': pinned, 'scope': 'Five new attempts; historical results remain unchanged.'})
    save(output/'preflight/local.json', suite.base.resources(root))
    for name in pinned:
        (output/name).chmod(0o444)
    report(output)


def report(output):
    rows = []
    parents = {r['layout']: r for r in suite.read(output/'previous-results.json')}
    lines = ['# Liquid rerun: L20–L24', '', 'Azure Astra; joint only; native initial states.', '',
             '| Layout | Previous result | New result |', '|---|---|---|']
    for layout in LAYOUTS:
        folder = output/'cases'/f'{TASK}-L{layout}-attempt2'
        row = {'layout': layout, 'phase': 'pending'}
        if (folder/'status.json').exists():
            row.update(suite.read(folder/'status.json'), phase='running')
        if (folder/'result.json').exists():
            row.update(suite.read(folder/'result.json'), phase='finished')
            row['end_reason'] = end_reason(folder, row)
        rows.append(row)
        current = outcome_text(row.get('native'), row['end_reason']) if row['phase'] == 'finished' else row['phase']
        old = parents[layout]
        lines.append(f"| L{layout} | {outcome_text(old.get('native'), old['end_reason'])} | {current} |")
    save(output/'summary.json', {'at': suite.runner.now(), 'rows': rows,
         'finished': sum(r['phase'] == 'finished' for r in rows), 'total': 5})
    (output/'summary.md').write_text('\n'.join(lines)+'\n')
    return rows


def verify_inputs(output):
    plan = suite.read(output/'plan.json')
    assert plan['task'] == TASK and plan['layouts'] == LAYOUTS
    for name, sha in plan['pinned_files'].items():
        assert suite.base.digest(output/name) == sha, name
    return plan


def verify_cases(output):
    plan = verify_inputs(output)
    runtime = audit.runtime_boundary()
    from egg_harness.native_responses import TOOL_INSTRUCTIONS
    from egg_harness.skill_policy import system_prompt
    config = suite.read(output/f'configs/{TASK}.json')
    expected_system = TOOL_INSTRUCTIONS+'\n'+system_prompt('joint')+'\n'+'\n'.join(
        (output/'skills'/name/'SKILL.md').read_text() for name in config['skill_names'])
    checked = []
    for layout in LAYOUTS:
        name = f'{TASK}-L{layout}-attempt2'
        case, folder = SCRATCH/'runs'/name, output/'cases'/name
        result, launch = suite.read(folder/'result.json'), suite.read(case/'launch.json')
        assert suite.read(case/'config.json') == config
        assert launch['provider'] == 'azure-astra' and launch['action_interface'] == 'joint'
        assert launch['native_initial_state'] and launch['layout_index'] == layout
        assert launch['harness_commit'] == suite.base.HARNESS_COMMIT
        assert launch['orchestration_commit'] == plan['source_commit']
        native = suite.read(case/'episode/provenance.json')
        assert native['seeds'] == [layout] and native['task'] == TASK
        assert native['action_interface'] == 'joint' and not native['object_teleportation'] and not native['depth_calibration']
        model = case/'policy/model'
        tools = suite.read(model/'native-tools.json')
        assert tools['provider'] == 'azure-astra' and tools['provider_allocation'] is None
        control = next(t for t in tools['tools'] if t['name'] == 'control_arms')
        assert set(control['parameters']['$defs']) == {'JointArm'}
        assert control['parameters']['$defs']['JointArm']['properties']['control']['const'] == 'joint'
        assert suite.read(case/'policy/skills.json') == {
            str(output/'skills'/name/'SKILL.md'): plan['pinned_files'][f'skills/{name}/SKILL.md']
            for name in config['skill_names']}
        requests = sorted(model.glob('*.request.json'))
        assert len(requests) == result['model_calls'] > 0
        assert [p.name for p in requests] == [f'{i:04d}.request.json' for i in range(1,len(requests)+1)]
        assert suite.read(requests[0])['step'] == 0
        for path in requests:
            req = suite.read(path)
            assert req['provider'] == 'azure-astra' and req['base_url'] == tools['base_url']
            assert req['system'] == expected_system
            context = json.loads(req['question'])
            assert context['public_task_requirements'] == config['public_task_requirements']
            assert context['action_interface'] == 'joint'
        observation = audit.public_observation_boundary(case)
        policy = suite.read(model/'transport-policy.json')
        assert policy['max_retries'] == 3 and policy['delays_seconds'] == [10,30,60]
        assert policy['same_request_and_scene'] and policy['robot_action_retries'] == 0 and not policy['provider_switching']
        retries = model/'transport-retries.jsonl'
        retried = audit.retry_records([json.loads(line) for line in retries.read_text().splitlines()] if retries.exists() else [])
        shards = audit.case_durability(folder, HDFS)
        checked.append({'layout': layout, 'requests': len(requests), 'public_observations': observation,
                        'retried_calls': retried, 'shards': len(shards), 'result_sha256': suite.base.digest(folder/'result.json')})
    save(output/'completion-audit.json', {'at': suite.runner.now(), 'requirements_verified': True,
         'source_commit': plan['source_commit'], 'runtime': runtime, 'cases': checked})
    return checked


def run(root, output):
    assert os.environ['ARNOLD_WORKER_ID'] == '0000000'
    assert not (output/'campaign.json').exists(), 'Inspect the existing campaign; do not duplicate'
    plan = verify_inputs(output)
    assert not suite.git(root, 'status', '--porcelain')
    assert plan['source_commit'] == suite.git(root, 'rev-parse', 'HEAD')
    worker, local = suite.base.resources(root), suite.read(output/'preflight/local.json')
    assert worker['cpu'] > local['cpu'] and worker['memory_limit'] > local['memory_limit'] and worker['nas']
    assert worker['memory_used'] < worker['memory_limit'] * .7
    save(output/'preflight/worker.json', worker)
    raw = subprocess.check_output(['nvidia-smi','--query-gpu=index,memory.total,memory.used','--format=csv,noheader,nounits'], text=True)
    devices = {int(v[0]): [int(v[1]),int(v[2])] for line in raw.splitlines() if (v := line.split(','))}
    selected = [gpu for gpu in sorted(devices, key=lambda g: devices[g][1]) if devices[gpu][0]-devices[gpu][1] >= 18000][:5]
    assert len(selected) == 5, 'Five GPUs with enough free memory are required'
    cases = [(TASK, layout, gpu) for layout,gpu in zip(LAYOUTS, selected)]
    save(output/'preflight/admission.json', {'at': suite.runner.now(), 'inventory': devices, 'cases': cases,
         'reserved_mib_per_case': 10000, 'remaining_mib_per_gpu': 8000})
    SCRATCH.mkdir(parents=True, exist_ok=False)
    assert shutil.disk_usage(SCRATCH).free > 100*1024**3
    config = suite.read(output/f'configs/{TASK}.json')
    materialized = Path(config['assets']).parent
    # Verify all five layouts' assets, reusing existing verified copies where possible.
    route, evidence = suite.base.route_probe(f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{REVISION}/README.md')
    save(output/'preflight/download-route.json', evidence)
    restore(SimpleNamespace(source_repo=ASSET_REPO, destination=materialized,
        curriculum=suite.base.HARNESS/'configs/curriculum.json',
        layouts=materialized/'Assets/Eval_Layout/RoboDojo/arx_x5/0', task=TASK, indices=LAYOUTS,
        proxy=route, workers=32, receipts=output/'assets',
        reuse_root=[Path('/tmp/astra-eight-tasks-5e17233/materialized'), suite.base.SCRATCH/'materialized',
                    BASE/'astra-Robodojo/artifacts/task-assets-v1', BASE/'astra-Robodojo/artifacts/runtime']))
    audit.runtime_boundary()
    suite.runner.HDFS = HDFS
    subprocess.run(['hdfs','dfs','-mkdir','-p',HDFS], check=True)
    archive = SCRATCH/'source-inputs.tar.gz'
    with tarfile.open(archive,'w:gz') as bundle:
        for name in ('skills','configs','source','calibration.json','previous-results.json','plan.json','preflight','assets'):
            bundle.add(output/name, arcname=name)
        bundle.add(suite.base.HARNESS, arcname='frozen-harness')
        bundle.add(root/PROGRESS, arcname=PROGRESS)
    suite.runner.hdfs_archive(archive,HDFS+'/source-inputs.tar.gz',output/'source-inputs-receipt.json')
    save(output/'campaign.json', {'at': suite.runner.now(), 'pid': os.getpid(), 'worker': '0000000',
         'cases': cases, 'source_commit': plan['source_commit'], 'hdfs_root': HDFS, 'scratch': str(SCRATCH)})
    args = SimpleNamespace(root=output/'source', scratch=SCRATCH, indices=LAYOUTS, timeout=10800, commit=plan['source_commit'])
    results = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(suite.runner.run_case,args,task,layout,gpu,campaign=output,attempt=2,
                   baseline=suite.base.HARNESS,harness_commit=suite.base.HARNESS_COMMIT,
                   provider='azure-astra',skill_revision=NAME) for task,layout,gpu in cases]
        for future in as_completed(futures):
            results.append(future.result())
            save(output/'results.json', results)
            report(output)
            print(json.dumps(results[-1]), flush=True)
    suite.runner.BACKUPS.shutdown(wait=True)
    save(output/'completed.json', {'at': suite.runner.now(), 'cases': len(results)})
    verify_cases(output)
    report(output)
    archive = SCRATCH/'final-report.tar.gz'
    with tarfile.open(archive,'w:gz') as bundle:
        bundle.add(output, arcname=NAME)
    suite.runner.hdfs_archive(archive,HDFS+'/final-report.tar.gz',output/'final-report-receipt.json')
    print('FIVE LIQUID RERUNS COMPLETED AND VERIFIED', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare','run','report','audit'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = root/'artifacts'/NAME
    if args.stage == 'prepare': prepare(root, output)
    elif args.stage == 'run': run(root, output)
    elif args.stage == 'report': report(output)
    else: verify_cases(output)
