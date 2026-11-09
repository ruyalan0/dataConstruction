"""Run the requested 26 tasks in independent, durable L20/L21 episodes."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from types import SimpleNamespace

import diagnose_suite as base
from five_layout_suite import public_requirements
from prepare_eight_eval import ASSET_REPO, BASE, REVISION, Tables, save
from restore_task_assets import restore
import run_eight_eval as runner
from report_eight_eval import end_reason, outcome_text

NAME = 'remain-26-two-layouts-v2-20260917'
TASK_FILE = BASE / 'Astra-diagnose/tasks-remain.md'
SCRATCH = Path('/tmp/astra-remain-26-two-layouts-20260917')
HDFS = 'hdfs://storage.example.invalid/REDACTED' + NAME
LAYOUTS = (20, 21)
PHASE = 'remaining'
LAYOUT_REVISIONS = {
    'swap_T': '91f76c28d93dd20c5fa46ce6a5a1d96a4f384acd',
    'store_tools_in_toolbox': '91f76c28d93dd20c5fa46ce6a5a1d96a4f384acd',
}


def read(path):
    return json.loads(path.read_text())


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def tasks():
    if PHASE == 'priority':
        return ['make_kong', 'imitate_sorting_sequence']
    if PHASE == 'pouring':
        return ['pour_liquid_into_cup']
    names = re.findall(r'^\|\s*\d+\s*\|\s*([A-Za-z][A-Za-z0-9_]+)\s*\|', TASK_FILE.read_text(), re.M)
    assert len(names) == len(set(names)) == 26, 'Unexpected task list'
    return names


def cases():
    return [(task, layout) for task in tasks()
            for layout in (range(20, 25) if task in ('make_kong', 'pour_liquid_into_cup') else LAYOUTS)]


def provider(layout):
    return 'azure-astra' if layout % 2 == 0 else 'zzz-astra'


def fetch_asset(url, route):
    # LFS redirects may be cached beyond the signed URL expiry; request a fresh redirect.
    return base.get(url + '?download=true&fresh=' + str(time.time_ns()), route, 120)


def inventory(root, output):
    assert not (output / 'plan.json').exists(), 'Inventory already exists'
    assert not git(root, 'status', '--porcelain'), 'Commit before fixing inputs'
    names = tasks()
    common = ['observation-grounding', 'visual-contact-control', 'upright-grasp-approach', 'transport-retention']
    selection = {}
    for task in names:
        skill = task.replace('_', '-').lower()
        selected = list(common)
        if (root / 'skills' / skill / 'SKILL.md').exists():
            selected.append(skill)
            if 'cross-side-handoff' in (root / 'skills' / skill / 'SKILL.md').read_text():
                selected.append('cross-side-handoff')
        selection[task] = selected
    save(output / 'plan.json', {
        'at': runner.now(), 'tasks': names, 'layouts': sorted({l for _, l in cases()}), 'total': len(cases()),
        'cases': [{'task': t, 'layout': l, 'provider': provider(l)} for t, l in cases()], 'phase': PHASE,
        'task_file': str(TASK_FILE), 'tasks_sha256': base.digest(TASK_FILE),
        'source_commit': git(root, 'rev-parse', 'HEAD'), 'harness_commit': base.HARNESS_COMMIT,
        'action_interface': 'joint', 'native_initial_state': True,
        'provider_by_layout': {str(l): provider(l) for _, l in cases()}, 'azure_concurrency_limit': 3,
        'skill_selection': selection, 'hdfs_root': HDFS,
        'gpu_slots_per_device': 3, 'reserved_mib_per_episode': 10000, 'gpu_headroom_mib': 8000,
        'scope': 'Fresh native episodes; fixed skills per task; global Azure limit three; no provider failover.'})
    shutil.copyfile(TASK_FILE, output / 'tasks-remain.md')
    save(output / 'preflight/local.json', base.resources(root))
    report(output)


def worker_check(root, output, label):
    assert os.environ.get('ARNOLD_WORKER_ID') == '0000000'
    local = read(output / 'preflight/local.json')
    worker = base.resources(root)
    assert worker['cpu'] > local['cpu'] and worker['memory_limit'] > local['memory_limit']
    assert worker['nas'] and worker['memory_used'] < worker['memory_limit'] * .7
    assert shutil.disk_usage('/tmp').free > 100 * 1024**3
    save(output / f'preflight/{label}-worker.json', worker)
    sys.path.insert(0, str(base.HARNESS))
    from egg_harness.freeze import verify
    assert verify(base.HARNESS)['commit'] == base.HARNESS_COMMIT


def prepare(root, output):
    worker_check(root, output, 'prepare')
    plan = read(output / 'plan.json')
    assert base.digest(TASK_FILE) == plan['tasks_sha256']
    assert git(root, 'rev-parse', 'HEAD') == plan['source_commit']
    assert not git(root, 'status', '--porcelain')
    assert not (output / 'ready.json').exists(), 'Preparation already complete'
    assert git(ASSET_REPO, 'rev-parse', 'HEAD') == REVISION
    prep = output / 'preparation'
    prep.mkdir(exist_ok=True)
    route, evidence = base.route_probe(f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{REVISION}/README.md')
    save(prep / 'asset-download-route.json', evidence)
    doc_route, evidence = base.route_probe('https://robodojo-benchmark.com/doc/sim-tasks/stack-bowls/')
    save(prep / 'documentation-route.json', evidence)

    def document(task):
        url = 'https://robodojo-benchmark.com/doc/sim-tasks/' + task.replace('_', '-').lower() + '/'
        path = prep / (task + '.json')
        if path.exists():
            record = read(path)
            assert record['url'] == url and base.digest(prep / (task + '.html')) == record['sha256']
            return task, record
        candidates = [BASE / 'Astra-Robodojo-RSI-0914-remain-two/artifacts/remain-26-two-layouts-20260917/preparation',
                      BASE / 'Astra-Robodojo-RSI-0914-azure-suite/artifacts/five-layout-suite/preparation']
        cached = next((p for p in candidates if (p / (task + '.json')).exists() and
                       (p / (task + '.html')).exists() and read(p / (task + '.json'))['url'] == url and
                       base.digest(p / (task + '.html')) == read(p / (task + '.json'))['sha256']), None)
        data = (cached / (task + '.html')).read_bytes() if cached else base.get(url, doc_route)
        parser = Tables(); parser.feed(data.decode())
        record = {'task': task, 'url': url, 'fetched_at': runner.now(), 'rows': parser.rows}
        public_requirements(record)
        (prep / (task + '.html')).write_bytes(data)
        record['sha256'] = base.digest(prep / (task + '.html'))
        save(path, record)
        return task, record

    with ThreadPoolExecutor(max_workers=32) as pool:
        documents = dict(pool.map(document, plan['tasks']))
    materialized = SCRATCH / 'materialized'
    caches = [ASSET_REPO, Path('/tmp/astra-five-layouts-azure-20260917/materialized'),
              Path('/tmp/astra-eight-tasks-5e17233/materialized'),
              BASE / 'astra-Robodojo/artifacts/task-assets-v1', BASE / 'astra-Robodojo/artifacts/runtime']

    def layout(pair):
        task, index = pair
        name = f'Assets/Eval_Layout/RoboDojo/arx_x5/0/{task}_{index}.json'
        revision = LAYOUT_REVISIONS.get(task, REVISION)
        pointer = (subprocess.check_output(['git', '-C', str(ASSET_REPO), 'show', f'{REVISION}:{name}'])
                   if revision == REVISION else base.get(
                       f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/raw/{revision}/{name}', route, 120))
        import hashlib
        if pointer.startswith(b'version https://git-lfs'):
            expected = next(x.split('sha256:')[1] for x in pointer.decode().splitlines() if x.startswith('oid '))
            candidates = [p / name for p in [materialized, *caches]]
            candidates += [ASSET_REPO / '.git/lfs/objects' / expected[:2] / expected[2:4] / expected,
                BASE / 'Astra-Robodojo-RSI-0914/artifacts/suite-assets-v4' / name.removeprefix('Assets/')]
            cached = next((p for p in candidates if p.is_file() and base.digest(p) == expected), None)
            data = cached.read_bytes() if cached else fetch_asset(f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{revision}/{name}', route)
        else:
            data = pointer; expected = hashlib.sha256(data).hexdigest()
        assert hashlib.sha256(data).hexdigest() == expected, name
        json.loads(data)
        target = materialized / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {'path': name, 'sha256': expected, 'bytes': len(data), 'layout_revision': revision}

    with ThreadPoolExecutor(max_workers=32) as pool:
        records = list(pool.map(layout, [(t, i) for t in plan['tasks']
            for i in range(1 + max(c['layout'] for c in plan['cases'] if c['task'] == t))]))
    save(prep / 'layout-manifest.json', {'asset_revision': REVISION, 'layout_revision_overrides':
        {t: r for t, r in LAYOUT_REVISIONS.items() if t in plan['tasks']}, 'files': records})
    for task in plan['tasks']:
        receipt = prep / ('assets-' + task)
        if receipt.exists():
            assert (receipt / 'manifest.json').exists(), 'Incomplete asset preparation: ' + task
            continue
        restore(SimpleNamespace(source_repo=ASSET_REPO, destination=materialized,
            curriculum=base.HARNESS / 'configs/curriculum.json',
            layouts=materialized / 'Assets/Eval_Layout/RoboDojo/arx_x5/0', task=task,
            indices=[c['layout'] for c in plan['cases'] if c['task'] == task], proxy=route, workers=32,
            receipts=receipt, reuse_root=caches[1:]))
    assets = materialized / 'Assets'
    for name in ('Robots', 'Sensor'):
        link = assets / name
        target = (BASE / 'Astra-Robodojo-RSI-0914/artifacts/suite-assets-v4' / name).resolve(strict=True)
        if not link.exists():
            link.symlink_to(target, target_is_directory=True)
        assert link.resolve() == target
    if PHASE == 'priority':
        for task in plan['tasks']:
            source = caches[1] / 'Assets/Traj/RoboDojo' / task
            target = assets / 'Traj/RoboDojo' / task
            target.parent.mkdir(parents=True, exist_ok=True)
            assert source.is_dir(), 'Missing original support trajectories: ' + task
            if not target.exists(): target.symlink_to(source, target_is_directory=True)
    for name in sorted({s for names in plan['skill_selection'].values() for s in names} | {'make-kong'}):
        target = output / 'skills' / name / 'SKILL.md'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / 'skills' / name / 'SKILL.md', target)
    shutil.copytree(root / 'scripts', output / 'source/scripts', ignore=shutil.ignore_patterns('__pycache__'))
    calibration = read(base.HARNESS / 'memory/skills.json')['head_camera']
    save(output / 'calibration.json', {'head_camera': {k: calibration[k] for k in ('intrinsic', 'world_from_camera', 'convention')}})
    for task in plan['tasks']:
        config = read(base.HARNESS / 'configs/classify-language-joint-development.json')
        config.update(task=task, astra_provider='azure-astra', action_interface='joint',
            assets=str(assets), object_assets=str(assets / 'Object/RoboDojo'),
            memory=str(output / 'calibration.json'), skills_root=str(output / 'skills'),
            skill_names=plan['skill_selection'][task], public_task_requirements=public_requirements(documents[task]),
            max_model_calls=500 if task == 'imitate_sorting_sequence' else 400, request_timeout=300)
        save(output / f'configs/{task}.json', config)
    pinned = {str(p.relative_to(output)): base.digest(p) for prefix in ('skills', 'configs', 'source')
              for p in (output / prefix).rglob('*') if p.is_file()}
    pinned['calibration.json'] = base.digest(output / 'calibration.json')
    save(output / 'ready.json', {'at': runner.now(), 'source_commit': plan['source_commit'],
        'tasks': plan['tasks'], 'layouts': list(LAYOUTS), 'pinned_files': pinned, 'assets': str(assets)})
    for name in pinned:
        (output / name).chmod(0o444)
    print(f"{plan['total']} CASE INPUTS READY", flush=True)


def report(output):
    plan = read(output / 'plan.json'); rows = []
    for task in plan['tasks']:
        for layout in [c['layout'] for c in plan['cases'] if c['task'] == task]:
            folder = output / 'cases' / f'{task}-L{layout}-attempt1'
            row = {'task': task, 'layout': layout, 'provider': plan['provider_by_layout'][str(layout)], 'phase': 'queued'}
            if (folder / 'launch.json').exists(): row['phase'] = 'starting'
            if (folder / 'status.json').exists(): row.update(phase='running', status=read(folder / 'status.json'))
            if (folder / 'result.json').exists():
                result = read(folder / 'result.json')
                row.update(phase='finished', result=result, native=result.get('native'), end_reason=end_reason(folder, result))
            rows.append(row)
    summary = {'at': runner.now(), 'total': plan['total'], 'finished': sum(r['phase'] == 'finished' for r in rows), 'rows': rows}
    save(output / 'summary.json', summary)
    lines = ['# ' + plan['phase'] + ' campaign', '', f"Updated {summary['at']}; finished {summary['finished']}/{plan['total']}.", '',
             '| Task | Layout | API | Result |', '|---|---|---|---|']
    for r in rows:
        cell = outcome_text(r['native'], r['end_reason']) if r['phase'] == 'finished' else (
            f"step {r['status']['step']}, calls {r['status']['model_calls']}" if r['phase'] == 'running' else r['phase'])
        lines.append(f"| {r['task']} | L{r['layout']} | {r['provider']} | {cell} |")
    lines += ['', 'Prefix scores are nonterminal results. API/infrastructure errors are reported separately from native outcomes.']
    (output / 'summary.md').write_text('\n'.join(lines) + '\n')
    return summary


def require_remaining_progress(root):
    release = read(root / 'artifacts/remaining-52-hold.json')
    assert release['status'] == 'released_after_verified_progress', (
        'Remaining 52 are on hold until both make_kong and pour_liquid_into_cup show verified progress')
    for task in ('make_kong', 'pour_liquid_into_cup'):
        record = release['tasks'][task]
        assert record['progress_verified'] and record['evidence_report'], task
        report = root / record['evidence_report']
        assert base.digest(report) == record['evidence_sha256'], task


def run(root, output):
    if PHASE == 'remaining':
        require_remaining_progress(root)
    worker_check(root, output, 'run')
    plan, ready = read(output / 'plan.json'), read(output / 'ready.json')
    assert not (output / 'campaign.json').exists(), 'Campaign already launched'
    if PHASE in ('pouring', 'remaining'):
        prerequisite = root / 'artifacts/priority-kong-sort-20260917'
        assert read(prerequisite / 'completed.json')['cases'] == 7, 'Priority seven episodes must finish first'
    if PHASE == 'remaining':
        reviewed = read(root / 'artifacts/pouring-inertia-v1-20260917/experiment-review.json')
        assert reviewed['reviewed'] and reviewed['next_action'] == 'proceed_remaining', 'Pouring experiment must be reviewed before remaining tasks'
    assert plan['source_commit'] == ready['source_commit']
    for name, expected in ready['pinned_files'].items(): assert base.digest(output / name) == expected, name
    inventory = subprocess.check_output(['nvidia-smi', '--query-gpu=index,memory.total,memory.used', '--format=csv,noheader,nounits'], text=True)
    devices = [[int(x.strip()) for x in line.split(',')] for line in inventory.splitlines()]
    assert len(devices) == 8
    assert all(used + 3 * 10000 <= total - 8000 for _, total, used in devices), 'Insufficient GPU headroom'
    save(output / 'preflight/gpu-admission.json', {'at': runner.now(), 'devices': devices, 'slots_per_gpu': 3,
        'reserved_mib_per_episode': 10000, 'headroom_mib': 8000, 'max_simultaneous_episodes': 24})
    runner.HDFS = HDFS
    subprocess.run(['hdfs', 'dfs', '-mkdir', '-p', HDFS], check=True)
    archive = SCRATCH / 'source-inputs.tar.gz'
    with tarfile.open(archive, 'w:gz', compresslevel=1) as bundle:
        bundle.add(output, arcname=NAME)
        bundle.add(base.HARNESS, arcname='frozen-harness')
        bundle.add(root / 'REMAIN-TWO-LAYOUT-PROGRESS.md', arcname='REMAIN-TWO-LAYOUT-PROGRESS.md')
    runner.hdfs_archive(archive, HDFS + '/source-inputs.tar.gz', output / 'source-inputs-receipt.json')
    save(output / 'campaign.json', {'at': runner.now(), 'pid': os.getpid(), 'worker': '0000000', 'total': plan['total'],
        'max_simultaneous_episodes': 24, 'max_simulator_startups': 4, 'source_commit': plan['source_commit'],
        'provider_by_layout': plan['provider_by_layout'], 'azure_concurrency_limit': 3, 'hdfs_root': HDFS, 'scratch': str(SCRATCH)})
    args = SimpleNamespace(root=output / 'source', scratch=SCRATCH, indices=LAYOUTS, timeout=10800, commit=plan['source_commit'])
    gates = {gpu: threading.Semaphore(3) for gpu, _, _ in devices}
    azure = threading.Semaphore(3)
    def case(task, layout, gpu):
        from contextlib import nullcontext
        with azure if provider(layout) == 'azure-astra' else nullcontext():
            with gates[gpu]:
                return runner.run_case(args, task, layout, gpu, campaign=output,
                    baseline=base.HARNESS, harness_commit=base.HARNESS_COMMIT, skill_revision=NAME)
    results = []
    with ThreadPoolExecutor(max_workers=32) as pool:
        pending = {pool.submit(case, c['task'], c['layout'], devices[i % 8][0])
                   for i, c in enumerate(plan['cases'])}
        while pending:
            done, pending = wait(pending, timeout=20, return_when=FIRST_COMPLETED)
            for future in done:
                result = future.result()
                results.append(result); save(output / 'results.json', results)
                print(json.dumps(result), flush=True)
            report(output)
    runner.BACKUPS.shutdown(wait=True)
    assert len(results) == plan['total']
    save(output / 'completed.json', {'at': runner.now(), 'cases': len(results)})
    report(output)
    print(f"ALL {len(results)} EPISODES FINISHED; NATIVE RESULTS READY FOR AUDIT", flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('inventory', 'prepare', 'run', 'report'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--phase', choices=('priority', 'pouring', 'remaining'), default='remaining')
    args = parser.parse_args(); PHASE = args.phase
    if PHASE == 'priority': NAME = 'priority-kong-sort-20260917'
    if PHASE == 'pouring': NAME = 'pouring-inertia-v1-20260917'
    HDFS = 'hdfs://storage.example.invalid/REDACTED' + NAME
    root = args.root.resolve(); output = root / 'artifacts' / NAME
    if args.stage == 'report': report(output)
    else: globals()[args.stage](root, output)
