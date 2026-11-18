"""Run isolated skill iterations for the two focal tasks while remaining tasks stay held."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import threading
from types import SimpleNamespace

import diagnose_suite as base
from prepare_eight_eval import save
import remaining_two_layouts as shared
import run_eight_eval as runner
from verify_provider_harness import verify_runtime


def prepare(root, output, template, task, layouts, provider_override, harness):
    assert not shared.git(root, 'status', '--porcelain'), 'Commit before freezing an iteration'
    assert task in ('make_kong', 'pour_liquid_into_cup')
    assert provider_override in (None, 'azure-astra', 'zzz-astra', 'zzy-astra')
    assert layouts and len(layouts) == len(set(layouts)) and set(layouts) <= set(range(20, 25))
    assert not output.exists(), 'Each iteration needs a new campaign directory'
    previous = shared.read(template / 'ready.json')
    for name, sha in previous['pinned_files'].items(): assert base.digest(template / name) == sha, name
    config = shared.read(template / f'configs/{task}.json')
    harness = harness.resolve() if harness is not None else base.HARNESS
    runtime = verify_runtime(harness)
    if provider_override == 'zzy-astra':
        assert runtime['zzy_supported'], 'ZZY requires the qualified provider Harness'
    output.mkdir(parents=True)
    commit = shared.git(root, 'rev-parse', 'HEAD')
    plan = {'at': runner.now(), 'source_commit': commit, 'harness_commit': runtime['commit'],
        'harness': str(harness), 'runtime_boundary': runtime,
        'phase': 'focus', 'tasks': [task], 'layouts': layouts, 'total': len(layouts),
        'cases': [{'task': task, 'layout': layout, 'provider': provider_override or shared.provider(layout)} for layout in layouts],
        'provider_by_layout': {str(layout): provider_override or shared.provider(layout) for layout in layouts},
        'provider_override': provider_override,
        'skill_selection': {task: config['skill_names']}, 'template': str(template),
        'template_ready_sha256': base.digest(template / 'ready.json'),
        'native_initial_state': True, 'action_interface': 'joint', 'azure_concurrency_limit': 3,
        'scope': 'Independent focal-task iteration. Does not release or launch the remaining 52.'}
    save(output / 'plan.json', plan)
    save(output / 'preflight/local.json', base.resources(root))
    shutil.copyfile(template / 'calibration.json', output / 'calibration.json')
    for name in config['skill_names']:
        target = output / 'skills' / name / 'SKILL.md'
        target.parent.mkdir(parents=True)
        shutil.copyfile(root / 'skills' / name / 'SKILL.md', target)
    shutil.copytree(root / 'scripts', output / 'source/scripts', ignore=shutil.ignore_patterns('__pycache__'))
    config.update(skills_root=str(output / 'skills'), memory=str(output / 'calibration.json'))
    if provider_override is not None: config['astra_provider'] = provider_override
    save(output / f'configs/{task}.json', config)
    pinned = {str(path.relative_to(output)): base.digest(path) for prefix in ('configs', 'skills', 'source')
        for path in (output / prefix).rglob('*') if path.is_file()}
    pinned['calibration.json'] = base.digest(output / 'calibration.json')
    save(output / 'ready.json', {'at': runner.now(), 'source_commit': commit, 'tasks': [task],
        'layouts': layouts, 'pinned_files': pinned, 'assets': config['assets']})
    for name in pinned: (output / name).chmod(0o444)
    shared.report(output)
    print(f'FROZEN {len(layouts)} FOCUS CASES: {output}', flush=True)


def run(root, output, gpu_ids):
    assert not (output / 'campaign.json').exists(), 'Campaign already launched'
    plan, ready = shared.read(output / 'plan.json'), shared.read(output / 'ready.json')
    runtime = verify_runtime(Path(plan['harness']))
    assert runtime == plan['runtime_boundary'] and runtime['commit'] == plan['harness_commit']
    base.HARNESS, base.HARNESS_COMMIT = Path(plan['harness']), plan['harness_commit']
    shared.worker_check(root, output, 'run')
    assert plan['source_commit'] == ready['source_commit']
    for name, sha in ready['pinned_files'].items(): assert base.digest(output / name) == sha, name
    assert len(gpu_ids) == plan['total'] and all(gpu in range(8) for gpu in gpu_ids)
    inventory = subprocess.check_output(['nvidia-smi', '--query-gpu=index,memory.total,memory.used',
        '--format=csv,noheader,nounits'], text=True)
    devices = [[int(x.strip()) for x in line.split(',')] for line in inventory.splitlines()]
    for gpu, total, used in devices:
        assert used + gpu_ids.count(gpu) * 10000 <= total - 8000, f'Insufficient GPU {gpu} headroom'
    scratch = Path('/tmp') / output.name
    scratch.mkdir(exist_ok=False)
    hdfs = 'hdfs://storage.example.invalid/REDACTED' + output.name
    save(output / 'preflight/gpu-admission.json', {'at': runner.now(), 'devices': devices,
        'assigned_gpus': gpu_ids, 'reserved_mib_per_episode': 10000, 'headroom_mib': 8000})
    runner.HDFS = hdfs
    subprocess.run(['hdfs', 'dfs', '-mkdir', '-p', hdfs], check=True)
    archive = scratch / 'source-inputs.tar.gz'
    with tarfile.open(archive, 'w:gz', compresslevel=1) as bundle:
        bundle.add(output, arcname=output.name)
        bundle.add(base.HARNESS, arcname='frozen-harness')
        for name in ('REMAIN-TWO-LAYOUT-PROGRESS.md', 'POURING-INERTIA-EXPERIMENT.md',
                     'artifacts/remaining-52-hold.json'):
            bundle.add(root / name, arcname=name)
    runner.hdfs_archive(archive, hdfs + '/source-inputs.tar.gz', output / 'source-inputs-receipt.json')
    import os
    save(output / 'campaign.json', {'at': runner.now(), 'pid': os.getpid(), 'worker': '0000000',
        'total': plan['total'], 'source_commit': plan['source_commit'], 'hdfs_root': hdfs,
        'scratch': str(scratch), 'azure_concurrency_limit': 3, 'assigned_gpus': gpu_ids})
    args = SimpleNamespace(root=output / 'source', scratch=scratch, indices=plan['layouts'],
        timeout=10800, commit=plan['source_commit'])
    azure = threading.Semaphore(3)
    def case(spec, gpu):
        from contextlib import nullcontext
        with azure if spec['provider'] == 'azure-astra' else nullcontext():
            return runner.run_case(args, spec['task'], spec['layout'], gpu, campaign=output,
                baseline=base.HARNESS, harness_commit=base.HARNESS_COMMIT, skill_revision=output.name,
                provider=plan['provider_override'])
    results = []
    with ThreadPoolExecutor(max_workers=plan['total']) as pool:
        pending = {pool.submit(case, spec, gpu) for spec, gpu in zip(plan['cases'], gpu_ids)}
        while pending:
            done, pending = wait(pending, timeout=20, return_when=FIRST_COMPLETED)
            for job in done:
                result = job.result(); results.append(result)
                save(output / 'results.json', results); print(json.dumps(result), flush=True)
            shared.report(output)
    runner.BACKUPS.shutdown(wait=True)
    assert len(results) == plan['total']
    save(output / 'completed.json', {'at': runner.now(), 'cases': len(results)})
    shared.report(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'run', 'report'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--template', type=Path)
    parser.add_argument('--task', choices=('make_kong', 'pour_liquid_into_cup'))
    parser.add_argument('--layouts', type=int, nargs='+')
    parser.add_argument('--gpus', type=int, nargs='+')
    parser.add_argument('--provider', choices=('azure-astra', 'zzz-astra', 'zzy-astra'), help='Pin fresh episodes to this provider; never switch a running episode')
    parser.add_argument('--harness', type=Path, help='Verified frozen Harness for newly prepared episodes')
    args = parser.parse_args()
    assert re.fullmatch(r'[a-z][a-z0-9-]+', args.name)
    root = args.root.resolve(); output = root / 'artifacts' / args.name
    if args.stage == 'prepare':
        assert args.template is not None and args.task is not None and args.layouts is not None
        prepare(root, output, args.template.resolve(), args.task, args.layouts, args.provider, args.harness)
    elif args.stage == 'run':
        assert args.gpus is not None
        run(root, output, args.gpus)
    else:
        shared.report(output)
