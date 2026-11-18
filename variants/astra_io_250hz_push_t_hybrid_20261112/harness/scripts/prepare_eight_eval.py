"""Materialize official inputs outside the immutable 5e17233 harness."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
from types import SimpleNamespace
import urllib.request

TASKS = ['make_kong', 'build_tower', 'insert_tubes', 'play_tic_tac_toe',
         'pour_balls_into_vase', 'pour_liquid_into_cup',
         'store_laptop_and_headphones', 'fill_pen_holder']
BASE = Path('/workspace')
ASSET_REPO = BASE / 'RoboDojo/.cache/robodojo_assets_repo'
REVISION = 'cfb06d1dadcdf03ccc923102b0e3f3b0a68dfc43'
PROXY = 'http://internal.example.invalid:8118'


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name+'.',
                            suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(data, indent=2) + '\n')
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def download(url, route):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(
        {} if route == 'direct' else {'http': route, 'https': route}))
    with opener.open(url, timeout=120) as response:
        return response.read()


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.row = []
        if tag in ('td', 'th'): self.cell = []
        if tag == 'br' and self.cell is not None: self.cell.append(' ')

    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--indices', type=int, nargs='+', default=[20, 21])
    args = parser.parse_args()
    root, scratch = args.root.resolve(), args.scratch.resolve()
    baseline = root / 'artifacts/baseline-5e17233'
    evidence = root / 'artifacts/eight-task-eval/preparation'
    evidence.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(scratch).free < 100 * 1024**3:
        raise RuntimeError('Worker scratch needs at least100GiB free')
    sys.path[:0] = [str(baseline), str(baseline / 'scripts')]
    from egg_harness.freeze import verify
    from restore_task_assets import restore
    manifest = verify(baseline)
    assert manifest['commit'] == '5e172339220b5a63b4d83a7c1b6220f9ff7f99d0'
    actual = subprocess.check_output(['git', '-C', str(ASSET_REPO), 'rev-parse', 'HEAD'], text=True).strip()
    assert actual == REVISION

    def document(task):
        url = 'https://robodojo-benchmark.com/doc/sim-tasks/' + task.replace('_', '-') + '/'
        existing = evidence / (task + '.json')
        if existing.exists():
            record = json.loads(existing.read_text())
            assert record['url'] == url
            assert record['sha256'] == hashlib.sha256((evidence/(task+'.html')).read_bytes()).hexdigest()
            return task, record
        # Direct was measured faster on this workspace before preparation.
        data = download(url, 'direct')
        parser = Tables(); parser.feed(data.decode())
        assert any(row[0] == 'Instruction' for row in parser.rows if row), task
        assert any(row[0] == '100' for row in parser.rows if row), task
        (evidence / (task + '.html')).write_bytes(data)
        record = {'task': task, 'url': url, 'sha256': hashlib.sha256(data).hexdigest(),
                  'fetched_at': datetime.now(timezone.utc).isoformat(), 'rows': parser.rows}
        save(evidence / (task + '.json'), record)
        return task, record

    with ThreadPoolExecutor(max_workers=8) as pool:
        docs = dict(pool.map(document, TASKS))
    save(evidence / 'public-tasks.json', docs)
    # Compare both routes for the actual asset host, not only documentation.
    sample = f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{REVISION}/README.md'
    def measure(route):
        started = time.monotonic()
        try:
            data = download(sample, route)
            return {'route': route, 'seconds': time.monotonic()-started, 'bytes': len(data)}
        except Exception as error:
            return {'route': route, 'seconds': time.monotonic()-started, 'error': str(error)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        routes = list(pool.map(measure, ['direct', PROXY]))
    save(evidence / 'download-route.json', routes)
    valid = [row for row in routes if 'error' not in row]
    if not valid: raise RuntimeError('Neither download route reaches the pinned asset host')
    route = min(valid, key=lambda row: row['seconds'])['route']

    layouts = scratch / 'assets/Eval_Layout/RoboDojo/arx_x5/0'
    layouts.mkdir(parents=True, exist_ok=True)

    def layout(pair):
        task, index = pair
        name = f'Assets/Eval_Layout/RoboDojo/arx_x5/0/{task}_{index}.json'
        pointer = subprocess.check_output(['git', '-C', str(ASSET_REPO), 'show', f'{REVISION}:{name}'])
        if pointer.startswith(b'version https://git-lfs'):
            expected = next(line.split('sha256:')[1] for line in pointer.decode().splitlines() if line.startswith('oid '))
            candidate = ASSET_REPO / name
            data = candidate.read_bytes() if candidate.exists() else download(
                f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{REVISION}/{name}', route)
            assert hashlib.sha256(data).hexdigest() == expected, name
        else:
            data = pointer
        json.loads(data)
        (layouts / f'{task}_{index}.json').write_bytes(data)
        return {'task': task, 'index': index, 'sha256': hashlib.sha256(data).hexdigest()}

    with ThreadPoolExecutor(max_workers=32) as pool:
        records = list(pool.map(layout, [(task, i) for task in TASKS for i in range(max(args.indices) + 1)]))
    save(evidence / 'layouts.json', {'revision': REVISION, 'files': records, 'unaltered': True})

    assets_repo = scratch / 'materialized'
    for task in TASKS:
        receipt = evidence / ('assets-' + task)
        if receipt.exists():
            if not (receipt / 'manifest.json').exists(): raise RuntimeError('Incomplete asset preparation: ' + task)
            continue
        restore(SimpleNamespace(source_repo=ASSET_REPO, destination=assets_repo,
            curriculum=baseline/'configs/curriculum.json', layouts=layouts, task=task,
            indices=args.indices, proxy=route, workers=32, receipts=receipt,
            reuse_root=[BASE/'astra-Robodojo/artifacts/task-assets-v1',
                        BASE/'astra-Robodojo/artifacts/runtime']))
        print(json.dumps({'task': task, 'assets': 'ready'}), flush=True)
    assets = scratch / 'assets'
    shared_runtime = BASE/'Astra-Robodojo-RSI-0914/artifacts/suite-assets-v4'
    for name in ['Background', 'Material', 'Object', 'Room']:
        link = assets/name
        if not link.exists(): link.symlink_to(assets_repo/'Assets'/name, target_is_directory=True)
    for name in ['Robots', 'Sensor', 'Traj']:
        link = assets/name
        if not link.exists(): link.symlink_to((shared_runtime/name).resolve(), target_is_directory=True)
    configs = root/'artifacts/eight-task-eval/configs'
    for task in TASKS:
        original = baseline/'configs'/('play-tic-tac-toe-development.json' if task=='play_tic_tac_toe' else 'classify-language-joint-development.json')
        config = json.loads(original.read_text())
        config.update(task=task, action_interface='joint', assets=str(assets),
            object_assets=str(assets/'Object/RoboDojo'), memory=str(baseline/'memory/skills.json'),
            skills_root=str(baseline/'skills'), max_model_calls=400, request_timeout=300,
            public_task_requirements=docs[task])
        if task != 'play_tic_tac_toe':
            config['skill_names']=['visual-contact-control', 'upright-grasp-approach', 'transport-retention']
        save(configs/(task+'.json'), config)
    save(evidence/'ready.json', {'tasks': TASKS, 'indices': args.indices,
        'harness_commit': manifest['commit'], 'asset_revision': REVISION,
        'assets': str(assets), 'scratch': str(scratch), 'native_initial_states': True,
        'skills_policy': 'Existing baseline general skills; existing tic-tac-toe task skill retained; no new task strategy tuning'})
    print('PREPARATION COMPLETE', flush=True)


if __name__ == '__main__': main()
