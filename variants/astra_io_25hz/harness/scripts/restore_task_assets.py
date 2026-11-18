"""Materialize pinned assets for selected saved layouts, with resumable verified files."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def selected_prefixes(layouts):
    prefixes = {'Assets/Material/Garment/', 'Assets/Material/Fluid/'}
    for layout in layouts:
        for kind in ('Rigid', 'Dynamic', 'Geometry', 'Articulation', 'Garment', 'Fluid'):
            for category, instances in layout.get(kind, {}).items():
                for instance in instances:
                    group = 'Clutter' if instance.get('type') == 'cluttered' else kind
                    prefixes.add(f'Assets/Object/RoboDojo/{group}/{category}/{instance["category_idx"]:05d}/')
        if 'Room' in layout:
            prefixes.add(f'Assets/Room/{layout["Room"]["default"]}/')
        if 'Table' in layout:
            prefixes.add(f'Assets/Material/{layout["Table"]["default"]}/')
        if 'Ground' in layout:
            prefixes.add(f'Assets/Material/{layout["Ground"]["materials"]["default"]}/')
        if 'Background' in layout:
            prefixes.add(f'Assets/Background/{layout["Background"]["category_name"]}')
    return sorted(prefixes)


def restore(args):
    source, destination = args.source_repo, args.destination
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    revision_expected = json.loads(args.curriculum.read_text())["asset_revision"]
    if revision != revision_expected:
        raise ValueError("Asset repository revision mismatch")
    paths = [args.layouts / f"{args.task}_{index}.json" for index in args.indices]
    layouts = [json.loads(path.read_text()) for path in paths]
    prefixes = selected_prefixes(layouts)
    files = subprocess.check_output(['git', '-C', str(source), 'ls-tree', '-r', '--name-only', revision,
                                     *prefixes], text=True).splitlines()
    if any(not any(name.startswith(prefix) for name in files) for prefix in prefixes):
        raise ValueError('A requested asset prefix has no pinned files')
    roots = [source, *args.reuse_root]
    proxies = {} if args.proxy == 'direct' else {'https': args.proxy, 'http': args.proxy}
    args.receipts.mkdir(parents=True, exist_ok=False)
    receipts = args.receipts / 'restore-receipts.jsonl'

    def materialize(name):
        pointer = subprocess.check_output(['git', '-C', str(source), 'show', f'{revision}:{name}'])
        lfs = pointer.startswith(b'version https://git-lfs.github.com/spec/v1')
        if lfs:
            fields = pointer.decode().splitlines()
            expected = next(x.split('sha256:')[1] for x in fields if x.startswith('oid '))
            size = int(next(x.split()[1] for x in fields if x.startswith('size ')))
        else:
            expected, size = hashlib.sha256(pointer).hexdigest(), len(pointer)
        target = destination / name
        if target.exists():
            if target.stat().st_size != size or sha256(target) != expected:
                raise ValueError('Existing destination differs from pinned asset: ' + name)
            return {'path': name, 'sha256': expected, 'bytes': size, 'source': 'verified_existing'}
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.partial')
        origin = 'git'
        if lfs:
            candidates = [root / name for root in roots]
            candidates.append(source / '.git/lfs/objects' / expected[:2] / expected[2:4] / expected)
            cached = next((p for p in candidates if p.is_file()
                           and p.stat().st_size == size and sha256(p) == expected), None)
            if cached is not None:
                shutil.copyfile(cached, temporary)
                origin = 'verified_local_copy'
            else:
                url = (f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{revision}/{name}'
                       f'?download=true&fresh={time.time_ns()}')
                opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
                with opener.open(url, timeout=120) as response, temporary.open('wb') as stream:
                    shutil.copyfileobj(response, stream, length=1024 * 1024)
                origin = 'download'
        else:
            temporary.write_bytes(pointer)
        if temporary.stat().st_size != size or sha256(temporary) != expected:
            raise ValueError('Asset integrity failure: ' + name)
        temporary.replace(target)
        return {'path': name, 'sha256': expected, 'bytes': size, 'source': origin}

    records = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool, receipts.open('a') as stream:
        futures = {pool.submit(materialize, name): name for name in files}
        for future in as_completed(futures):
            try:
                record = future.result()
            except Exception as error:
                for pending in futures:
                    pending.cancel()
                (args.receipts / 'restore-error.json').write_text(json.dumps({
                    'path': futures[future], 'error': str(error), 'completed_files': len(records)}, indent=2))
                raise RuntimeError('Asset materialization failed: ' + futures[future]) from error
            records.append(record)
            stream.write(json.dumps(record) + '\n')
            stream.flush()
            if len(records) % 100 == 0:
                print(json.dumps({'completed_files': len(records), 'total_files': len(files)}), flush=True)
        os.fsync(stream.fileno())
    manifest = {'revision': revision, 'tasks': [p.stem.removesuffix('_0') for p in paths],
                'prefixes': prefixes, 'files': sorted(records, key=lambda r: r['path'])}
    (args.receipts / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'files': len(records), 'bytes': sum(r['bytes'] for r in records),
                      'downloaded_files': sum(r['source'] == 'download' for r in records)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-repo', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--reuse-root', type=Path, action='append', default=[])
    parser.add_argument('--proxy', required=True, help='Measured winning proxy URL, or direct')
    parser.add_argument('--workers', type=int, default=32)
    parser.add_argument('--task', required=True)
    parser.add_argument('--indices', type=int, nargs='+', required=True)
    parser.add_argument('--layouts', type=Path, required=True)
    parser.add_argument('--curriculum', type=Path, default=Path('configs/curriculum.json'))
    parser.add_argument('--receipts', type=Path, required=True)
    restore(parser.parse_args())
