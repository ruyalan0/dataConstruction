"""Jointly admit ten fixed-skill episodes, reusing the audited single-task runner."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
from types import SimpleNamespace

import rerun_liquid_layouts as single
from prepare_eight_eval import ASSET_REPO, BASE, REVISION, save
from restore_task_assets import restore

suite = single.suite
TASKS = ('pour_liquid_into_cup', 'make_kong')
NAME = 'pour-kong-rerun-8c4b85f'
SCRATCH = Path('/tmp/astra-pour-kong-rerun-8c4b85f-20260917')
HDFS = suite.HDFS + '/' + NAME
PROGRESS = 'POUR-KONG-RERUN-PROGRESS.md'


def select_task(task):
    # These module settings are only used by serial preparation/report/audit calls.
    # Concurrent episodes call run_case directly with explicit task/campaign paths.
    assert task in TASKS
    single.TASK, single.NAME = task, NAME + '/' + task
    single.SCRATCH, single.HDFS = SCRATCH, HDFS


def prepare(root, output):
    assert not suite.git(root, 'status', '--porcelain')
    output.mkdir(parents=True, exist_ok=False)
    selection = suite.read(root/'skills/suite-task-selection.json')
    for task in TASKS:
        select_task(task)
        single.prepare(root, output/task)
        config = suite.read(output/task/f'configs/{task}.json')
        assert config['skill_names'] == selection['common'] + selection['tasks'][task]
    save(output/'plan.json', {'at': suite.runner.now(), 'tasks': list(TASKS),
         'layouts': single.LAYOUTS, 'total': len(TASKS)*len(single.LAYOUTS),
         'source_commit': suite.git(root, 'rev-parse', 'HEAD'),
         'task_plans': {task: suite.base.digest(output/task/'plan.json') for task in TASKS},
         'scope': 'New native-initial attempts; historical results and fixed inputs are preserved.'})
    report(output)


def verify_inputs(output):
    plan = suite.read(output/'plan.json')
    assert plan['tasks'] == list(TASKS) and plan['layouts'] == single.LAYOUTS
    assert plan['total'] == 10
    for task in TASKS:
        assert suite.base.digest(output/task/'plan.json') == plan['task_plans'][task]
        select_task(task)
        assert single.verify_inputs(output/task)['source_commit'] == plan['source_commit']
    return plan


def report(output):
    rows = []
    for task in TASKS:
        select_task(task)
        rows.extend({**row, 'task': task} for row in single.report(output/task))
        child_report = output/task/'summary.md'
        child_report.write_text(child_report.read_text().replace(
            '# Liquid rerun: L20–L24', f'# {task}: L20–L24', 1))
    assert len(rows) == 10 and len({(r['task'], r['layout']) for r in rows}) == 10
    save(output/'summary.json', {'at': suite.runner.now(), 'rows': rows,
         'finished': sum(r['phase'] == 'finished' for r in rows), 'total': len(rows)})
    lines = ['# Pour Liquid / Make Kong rerun', '',
             '| Task | L20 | L21 | L22 | L23 | L24 |', '|---|---|---|---|---|---|']
    for task in TASKS:
        cells = []
        for row in (r for r in rows if r['task'] == task):
            if row['phase'] == 'finished':
                cells.append(single.outcome_text(row.get('native'), row['end_reason']))
            elif row['phase'] == 'running':
                cells.append(f"step {row['step']}")
            else:
                cells.append('pending')
        lines.append('| ' + task + ' | ' + ' | '.join(cells) + ' |')
    (output/'summary.md').write_text('\n'.join(lines)+'\n')
    return rows


def verify_cases(output):
    plan = verify_inputs(output)
    cases = []
    for task in TASKS:
        select_task(task)
        cases.extend({**case, 'task': task} for case in single.verify_cases(output/task))
    assert len(cases) == 10
    save(output/'completion-audit.json', {'at': suite.runner.now(),
         'requirements_verified': True, 'source_commit': plan['source_commit'], 'cases': cases})


def run(root, output):
    assert os.environ['ARNOLD_WORKER_ID'] == '0000000'
    assert not (output/'campaign.json').exists(), 'Inspect the existing campaign; do not duplicate'
    plan = verify_inputs(output)
    assert not suite.git(root, 'status', '--porcelain')
    assert plan['source_commit'] == suite.git(root, 'rev-parse', 'HEAD')
    worker = suite.base.resources(root)
    local = suite.read(output/TASKS[0]/'preflight/local.json')
    assert worker['cpu'] > local['cpu'] and worker['memory_limit'] > local['memory_limit'] and worker['nas']
    assert worker['memory_used'] < worker['memory_limit'] * .7
    save(output/'preflight/worker.json', worker)
    raw = subprocess.check_output(['nvidia-smi', '--query-gpu=index,memory.total,memory.used',
                                   '--format=csv,noheader,nounits'], text=True)
    devices = {int(v[0]): [int(v[1]), int(v[2])] for line in raw.splitlines() if (v := line.split(','))}
    projected = {gpu: values[1] for gpu, values in devices.items()}
    cases = []
    for task in TASKS:
        for layout in single.LAYOUTS:
            available = [g for g, (total, _) in devices.items() if projected[g]+10000 <= total-8000]
            assert available, 'Insufficient measured GPU memory for ten episodes'
            gpu = min(available, key=lambda g: projected[g])
            projected[gpu] += 10000
            cases.append((task, layout, gpu))
    save(output/'preflight/admission.json', {'at': suite.runner.now(), 'inventory': devices,
         'projected_mib': projected, 'cases': cases, 'reserved_mib_per_case': 10000,
         'remaining_mib_per_gpu': 8000})
    SCRATCH.mkdir(parents=True, exist_ok=False)
    assert shutil.disk_usage(SCRATCH).free > 100*1024**3
    route, evidence = suite.base.route_probe(
        f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{REVISION}/README.md')
    save(output/'preflight/download-route.json', evidence)
    for task in TASKS:
        config = suite.read(output/task/f'configs/{task}.json')
        materialized = Path(config['assets']).parent
        restore(SimpleNamespace(source_repo=ASSET_REPO, destination=materialized,
            curriculum=suite.base.HARNESS/'configs/curriculum.json',
            layouts=materialized/'Assets/Eval_Layout/RoboDojo/arx_x5/0', task=task, indices=single.LAYOUTS,
            proxy=route, workers=32, receipts=output/'assets'/task,
            reuse_root=[Path('/tmp/astra-eight-tasks-5e17233/materialized'), suite.base.SCRATCH/'materialized',
                        BASE/'astra-Robodojo/artifacts/task-assets-v1', BASE/'astra-Robodojo/artifacts/runtime']))
    single.audit.runtime_boundary()
    suite.runner.HDFS = HDFS
    subprocess.run(['hdfs', 'dfs', '-mkdir', '-p', HDFS], check=True)
    archive = SCRATCH/'source-inputs.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        bundle.add(output, arcname=NAME)
        bundle.add(suite.base.HARNESS, arcname='frozen-harness')
        bundle.add(root/PROGRESS, arcname=PROGRESS)
    suite.runner.hdfs_archive(archive, HDFS+'/source-inputs.tar.gz', output/'source-inputs-receipt.json')
    save(output/'campaign.json', {'at': suite.runner.now(), 'pid': os.getpid(), 'worker': '0000000',
         'cases': cases, 'source_commit': plan['source_commit'], 'hdfs_root': HDFS, 'scratch': str(SCRATCH)})
    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = []
        for task, layout, gpu in cases:
            args = SimpleNamespace(root=output/task/'source', scratch=SCRATCH, indices=single.LAYOUTS,
                                   timeout=10800, commit=plan['source_commit'])
            futures.append(pool.submit(suite.runner.run_case, args, task, layout, gpu,
                campaign=output/task, attempt=2, baseline=suite.base.HARNESS,
                harness_commit=suite.base.HARNESS_COMMIT, provider='azure-astra', skill_revision=NAME))
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
    with tarfile.open(archive, 'w:gz') as bundle:
        bundle.add(output, arcname=NAME)
    suite.runner.hdfs_archive(archive, HDFS+'/final-report.tar.gz', output/'final-report-receipt.json')
    print('TEN POUR/KONG RERUNS COMPLETED AND VERIFIED', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'run', 'report', 'audit'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = root/'artifacts'/NAME
    if args.stage == 'prepare': prepare(root, output)
    elif args.stage == 'run': run(root, output)
    elif args.stage == 'report': report(output)
    else: verify_cases(output)
