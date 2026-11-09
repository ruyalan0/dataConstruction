"""Run one explicit skill revision against failed native-initial development cases.

This is external evaluation orchestration. Simulator, controller and transport
adapter remain the same as the baseline campaign.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import traceback

import run_eight_eval as runner
from prepare_eight_eval import TASKS, save

CASES = [('make_kong', 20, 0), ('make_kong', 21, 0),
         ('insert_tubes', 20, 2),
         ('pour_liquid_into_cup', 20, 5), ('pour_liquid_into_cup', 21, 5),
         ('fill_pen_holder', 20, 7), ('fill_pen_holder', 21, 7)]
HARNESS = '5e172339220b5a63b4d83a7c1b6220f9ff7f99d0'
PROMPT_HARNESS = '64998f80b0afe3bc5af9d0e06417445296de5c2b'


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*')) if path.is_file()}


def resources(root):
    raw = subprocess.check_output([sys.executable, str(root/'scripts/resources.py'), '--cpu-only'], text=True)
    return json.loads(raw)


def prepare(args, output):
    baseline = args.root/'artifacts/eight-task-eval'
    parents = []
    cases = CASES if args.suite == 'failed-v1' else [(task, layout, gpu) for gpu, task in enumerate(TASKS) for layout in (20, 21)]
    for task, layout, gpu in cases:
        path = baseline/'cases'/f'{task}-L{layout}-attempt1'/'result.json'
        if args.suite == 'failed-v1':
            result = json.loads(path.read_text())
            assert not result['native']['success'], f'Case already succeeded: {path}'
        if not path.exists(): path = path.parent/'status.json'
        parents.append({'task': task, 'layout': layout, 'gpu': gpu,
                        'record_at_preparation': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(args.root/'skills', output/'skills', ignore=shutil.ignore_patterns('__pycache__'))
    skill_commit = subprocess.check_output(['git', '-C', str(args.root), 'log', '-1', '--format=%H', '--', 'skills'], text=True).strip()
    save(output/'skills-manifest.json', {'revision': args.revision, 'skill_commit': skill_commit,
                                      'harness_commit': args.harness_commit, 'files': hashes(output/'skills')})
    selection = json.loads((output/'skills/eight-task-selection.json').read_text()) if args.suite == 'all-eight' else None
    for task in sorted({task for task, _, _ in cases}):
        config = json.loads((baseline/'configs'/f'{task}.json').read_text())
        config['skills_root'] = str(output/'skills')
        if selection is not None:
            config['skill_names'] = selection['common']+[selection['tasks'][task]]
        elif task == 'pour_liquid_into_cup': config['skill_names'].append('pour-liquid-into-cup')
        for name in config['skill_names']: assert (output/'skills'/name/'SKILL.md').is_file()
        save(output/'configs'/f'{task}.json', config)
    save(output/'preflight/local.json', resources(args.root))
    save(output/'plan.json', {'revision': args.revision, 'skill_commit': skill_commit,
         'harness_commit': args.harness_commit, 'harness_base_commit': HARNESS,
         'baseline': str(args.baseline), 'suite': args.suite,
         'prepared_at': runner.now(), 'parents': parents,
         'cases': cases, 'attempt': args.attempt, 'config_hashes': hashes(output/'configs'),
         'comparison': 'Adaptive development follow-up; keep every baseline and revision result.'})
    for path in [*list((output/'skills').rglob('*')), *list((output/'configs').glob('*.json')),
                 output/'skills-manifest.json', output/'plan.json']:
        if path.is_file(): path.chmod(0o444)
    print(output, flush=True)


def run(args, output):
    assert os.environ.get('ARNOLD_WORKER_ID') == '0000000', 'Run on the checked GPU worker'
    assert not (output/'campaign.json').exists(), 'Revision already launched; do not duplicate'
    manifest = json.loads((output/'skills-manifest.json').read_text())
    plan = json.loads((output/'plan.json').read_text())
    cases = plan['cases']
    assert str(args.baseline) == plan['baseline'] and args.harness_commit == plan['harness_commit']
    assert hashes(output/'skills') == manifest['files'], 'Skill snapshot changed'
    assert hashes(output/'configs') == plan['config_hashes'], 'Iteration configuration changed'
    worker = resources(args.root)
    local = json.loads((output/'preflight/local.json').read_text())
    assert worker['cpu'] > local['cpu'] and worker['memory_limit'] > local['memory_limit'] and worker['nas']
    assert worker['memory_used'] < worker['memory_limit'] * 0.7
    save(output/'preflight/worker.json', worker)
    inventory = subprocess.check_output(['nvidia-smi', '--query-gpu=index,memory.total,memory.used', '--format=csv,noheader,nounits'], text=True)
    (output/'preflight/gpus.csv').write_text(inventory)
    devices = {int(fields[0]): (int(fields[1]), int(fields[2]))
               for line in inventory.splitlines() if (fields := line.split(','))}
    demand = {gpu: sum(item[2] == gpu for item in cases) for _, _, gpu in cases}
    assert all(devices[gpu][0] >= 40000 and devices[gpu][1]+10000*count <= devices[gpu][0]-8000
               for gpu, count in demand.items()), 'Insufficient GPU headroom for selected cases'
    save(output/'preflight/admission.json', {'cases_per_gpu': demand, 'reserved_mib_per_case': 10000,
         'remaining_mib_per_gpu': 8000, 'existing_processes_preserved': True})
    args.scratch = Path('/tmp/astra-eight-tasks-5e17233/skill-iterations')/args.revision
    args.scratch.mkdir(parents=True, exist_ok=False)
    assert shutil.disk_usage(args.scratch).free > 100*1024**3
    args.indices = [20, 21]
    runner.HDFS += '/skill-iterations/'+args.revision
    runner.check(['hdfs', 'dfs', '-mkdir', '-p', runner.HDFS])
    archive = args.scratch/'source-inputs.tar.gz'
    with tarfile.open(archive, 'w:gz') as target:
        for name in ['skills', 'configs', 'preflight', 'skills-manifest.json', 'plan.json']:
            target.add(output/name, arcname=name)
        for name in ['run_skill_iteration.py', 'run_eight_eval.py', 'eval_retry_policy.py', 'prepare_eight_eval.py',
                     'resources.py', 'watch_skill_eval.py', 'report_eight_eval.py']:
            target.add(args.root/'scripts'/name, arcname='orchestration/'+name)
        target.add(args.baseline, arcname='harness')
        for name in ['EIGHT-TASK-EVAL-PROGRESS.md', 'TASK-SKILL-ITERATION.md']:
            target.add(args.root/name, arcname=name)
        if args.suite == 'all-eight':
            target.add(args.root/'artifacts/task-skill-review/public-demo-manifest.json', arcname='public-demo-manifest.json')
        target.add(args.root/'artifacts/eight-task-eval/source-inputs-receipt.json', arcname='baseline-source-receipt.json')
    runner.hdfs_archive(archive, runner.HDFS+'/source-inputs.tar.gz', output/'source-inputs-receipt.json')
    save(output/'campaign.json', {'started_at': runner.now(), 'revision': args.revision,
         'skill_commit': manifest['skill_commit'], 'source_commit': args.harness_commit, 'harness_base_commit': HARNESS,
         'orchestration_commit': args.commit, 'worker': '0000000', 'pid': os.getpid(),
         'hdfs_root': runner.HDFS, 'cases': cases, 'indices': args.indices,
         'attempt': plan['attempt'], 'concurrency': len(cases), 'native_initial_state': True})
    results = []
    with ThreadPoolExecutor(max_workers=len(cases)) as pool:
        futures = {pool.submit(runner.run_case, args, task, layout, gpu,
                    campaign=output, attempt=plan['attempt'], skill_revision=args.revision,
                    baseline=args.baseline, harness_commit=args.harness_commit): (task, layout)
                   for task, layout, gpu in cases}
        for future in as_completed(futures):
            task, layout = futures[future]
            try: result = future.result()
            except Exception:
                result = {'task': task, 'layout_index': layout, 'skill_revision': args.revision,
                          'status': 'orchestration_or_durability_error', 'error': traceback.format_exc()}
            results.append(result)
            save(output/'results.json', results)
            print(json.dumps(result), flush=True)
    runner.BACKUPS.shutdown(wait=True)
    save(output/'completed.json', {'at': runner.now(), 'cases': len(results), 'results': results})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['prepare', 'run'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--suite', choices=['failed-v1', 'all-eight'], default='failed-v1')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--attempt', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=10800)
    args = parser.parse_args()
    assert re.fullmatch('[a-z0-9-]+', args.revision), 'Use a simple revision identifier'
    args.root = args.root.resolve()
    assert not subprocess.check_output(['git', '-C', str(args.root), 'status', '--porcelain']).strip(), 'Commit before preparing or launching'
    args.commit = subprocess.check_output(['git', '-C', str(args.root), 'rev-parse', 'HEAD'], text=True).strip()
    args.baseline = (args.root/'artifacts/baseline-5e17233') if args.baseline is None else args.baseline.resolve()
    sys.path.insert(0, str(args.baseline))
    from egg_harness.freeze import verify
    args.harness_commit = verify(args.baseline)['commit']
    assert args.harness_commit in (HARNESS, PROMPT_HARNESS), 'Unexpected Harness revision'
    output = args.root/'artifacts/skill-iterations'/args.revision
    if args.stage == 'prepare': prepare(args, output)
    else: run(args, output)


if __name__ == '__main__': main()
