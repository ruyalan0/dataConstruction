"""Deduplicate one immutable static asset cache with durable recovery records."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import stat

BASE = Path('/workspace')
TARGET = BASE / 'astra-Robodojo/artifacts/all-task-assets-20260913'
SOURCES = [BASE / 'astra-Robodojo/artifacts/task-assets-v1', BASE / 'RoboDojo/.cache/robodojo_assets_repo']
MANIFEST_SHA = 'e988eb07aa2996829b0ba81797ab11ccc1bd677b254a8a868d1c51bfbb130b45'
BACKUP = BASE / 'astra-Robodojo/artifacts/all-astra-plan-20260913/assets-backup.json'
MAX_RECLAIM_BYTES = 4043628544


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def snapshot(path, sha):
    if path.resolve() != path:
        raise ValueError(f'Symlink asset path: {path}')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f'Nonregular asset: {path}')
    actual = digest(path)
    after = path.stat()
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_uid', 'st_gid', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')
    if any(getattr(before, key) != getattr(after, key) for key in fields) or actual != sha:
        raise ValueError(f'Changed/corrupt asset: {path}')
    return {**{key: getattr(after, key) for key in fields}, 'allocated_bytes': after.st_blocks * 512,
            'sha256': actual, 'xattrs': {key: os.getxattr(path, key).hex() for key in sorted(os.listxattr(path))}}


def metadata(record):
    return tuple(record[key] for key in ('st_dev', 'st_mode', 'st_uid', 'st_gid', 'xattrs'))


def build_plan(target, sources, manifest):
    groups = {}
    for row in manifest['files']:
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'Assets' or 'Eval_Layout' in relative.parts:
            raise ValueError('Manifest includes non-static or escaping asset path')
        groups.setdefault((row['sha256'], row['bytes']), []).append(target / relative)
    for row in manifest['files']:
        for source in sources:
            path = source / row['path']
            resolved = path.resolve()
            allowed = resolved.is_relative_to(source.resolve()) or (source == SOURCES[0] and resolved.is_relative_to(BASE / 'astra-Robodojo/artifacts/coverage-assets-v1'))
            if allowed and resolved.is_file() and resolved.stat().st_size == row['bytes']:
                groups[(row['sha256'], row['bytes'])].append(resolved)
    candidates = {path: sha for (sha, size), paths in groups.items() if len(set(paths)) > 1 for path in paths}
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = dict(pool.map(lambda pair: (pair[0], snapshot(*pair)), candidates.items()))
    plan = []
    for (sha, size), paths in groups.items():
        paths = list(dict.fromkeys(paths))
        if len(paths) < 2:
            continue
        own = sorted(path for path in paths if path.is_relative_to(target))
        external = [path for path in paths if not path.is_relative_to(target)]
        canonical = external[0] if external else own[0]
        reference = records[canonical]
        for path in own:
            current = records[path]
            if path == canonical or (current['st_dev'], current['st_ino']) == (reference['st_dev'], reference['st_ino']):
                continue
            if current['st_nlink'] != 1 or metadata(current) != metadata(reference):
                raise ValueError(f'Incompatible asset metadata: {path}')
            if current['st_size'] != size:
                raise ValueError(f'Manifest asset size mismatch: {path}')
            plan.append({'target': str(path), 'canonical': str(canonical), 'before': current, 'canonical_before': reference})
    return plan


def append_record(stream, record):
    stream.write(json.dumps(record, sort_keys=True) + '\n')
    stream.flush()
    os.fsync(stream.fileno())


def replace_duplicate(item, journal):
    target, canonical = Path(item['target']), Path(item['canonical'])
    expected, reference = item['before'], item['canonical_before']
    current = snapshot(target, expected['sha256'])
    source = snapshot(canonical, reference['sha256'])
    # Canonical link count and ctime change after previous links; contents,
    # inode, metadata and mtime must remain exactly the planned values.
    keys = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_uid', 'st_gid', 'st_mtime_ns', 'sha256', 'xattrs')
    if current != expected or any(source[key] != reference[key] for key in keys) or metadata(current) != metadata(source):
        raise ValueError(f'Asset changed since planning: {target}')
    temporary = target.with_name(target.name + '.rsi-dedup.tmp')
    append_record(journal, {'status': 'prepared', **item, 'temporary': str(temporary)})
    os.link(canonical, temporary, follow_symlinks=False)
    try:
        latest = target.stat()
        if latest.st_ino != expected['st_ino'] or latest.st_mtime_ns != expected['st_mtime_ns'] or latest.st_ctime_ns != expected['st_ctime_ns']:
            raise ValueError(f'Asset changed before atomic replace: {target}')
        os.replace(temporary, target)
        sync_directory(target.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
            sync_directory(temporary.parent)
    after = snapshot(target, expected['sha256'])
    if after['st_ino'] != source['st_ino'] or metadata(after) != metadata(expected):
        raise ValueError(f'Invalid replacement: {target}')
    append_record(journal, {'status': 'completed', 'target': str(target), 'canonical': str(canonical),
                            'sha256': after['sha256'], 'reclaimed_bytes': expected['allocated_bytes']})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if digest(TARGET / 'manifest.json') != MANIFEST_SHA:
        raise ValueError('Asset manifest changed')
    manifest = json.loads((TARGET / 'manifest.json').read_text())
    backup = json.loads(BACKUP.read_text())
    if backup.get('verified') is not True or backup.get('asset_manifest_sha256') != MANIFEST_SHA or not all(row.get('verified') for row in backup['shards']):
        raise ValueError('Missing matching verified HDFS recovery receipt')
    plan = build_plan(TARGET, SOURCES, manifest)
    bounded_plan = []
    planned_bytes = 0
    for item in plan:
        size = item['before']['allocated_bytes']
        if planned_bytes + size <= MAX_RECLAIM_BYTES:
            bounded_plan.append(item)
            planned_bytes += size
    plan = bounded_plan
    args.receipt.mkdir(parents=True, exist_ok=False)
    sync_directory(args.receipt.parent)
    space_before = os.statvfs(TARGET).f_bavail * os.statvfs(TARGET).f_frsize
    with (args.receipt / 'plan.json').open('x') as stream:
        json.dump({'target_root': str(TARGET), 'manifest_sha256': MANIFEST_SHA, 'backup': backup, 'items': plan,
                   'immutable_contract': 'All asset paths are read-only; in-place changes would affect linked paths.',
                   'recovery': 'Copy canonical verified bytes to a new inode and atomically replace target; or restore the matching HDFS shard using the pinned manifest hash.'}, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(args.receipt)
    completed = 0
    if args.apply:
        with (args.receipt / 'journal.jsonl').open('x') as journal:
            sync_directory(args.receipt)
            for item in plan:
                replace_duplicate(item, journal)
                completed += 1
                if completed % 200 == 0:
                    print(json.dumps({'completed': completed, 'total': len(plan)}), flush=True)
    summary = {'applied': args.apply, 'planned_files': len(plan), 'completed_files': completed,
               'planned_reclaim_bytes': sum(item['before']['allocated_bytes'] for item in plan),
               'available_before_bytes': space_before, 'available_after_bytes': os.statvfs(TARGET).f_bavail * os.statvfs(TARGET).f_frsize}
    with (args.receipt / 'summary.json').open('x') as stream:
        json.dump(summary, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(args.receipt)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
