"""Evict only byte-verified RGB caches from closed HDFS-backed episodes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, record):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(record, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def evict(run, archive, backup, receipt):
    if receipt.exists():
        raise FileExistsError(receipt)
    metadata = json.loads(backup.read_text())
    if metadata.get('verified_readback') is not True:
        raise ValueError('Verified HDFS readback required')
    if not metadata['uri'].startswith('hdfs://'):
        raise ValueError('HDFS provenance required')
    results = json.loads((run / 'results.json').read_text())
    if not results:
        raise ValueError('No completed episode results')
    for result in results:
        exit_record = run / ('seed-' + str(result['seed_index'])) / 'exit-status.json'
        if json.loads(exit_record.read_text())['exit_code'] != 0:
            raise ValueError('Clean simulator exit required')
    if archive.stat().st_size != metadata['bytes'] or digest(archive) != metadata['sha256']:
        raise ValueError('Archive differs from HDFS receipt')
    verified = {}
    with tarfile.open(archive, 'r|gz') as source:
        for member in source:
            parts = Path(member.name)
            if parts.is_absolute() or '..' in parts.parts or parts.parts[0] != run.name:
                raise ValueError('Unsafe archive member')
            if not (member.isfile() or member.isdir()):
                raise ValueError('Links and special members forbidden')
            relative = Path(*parts.parts[1:])
            if member.isfile() and relative.suffix == '.jpg':
                if relative in verified:
                    raise ValueError('Duplicate RGB member')
                local = run / relative
                if local.is_symlink() or local.resolve() != run.resolve() / relative:
                    raise ValueError('Symlinked RGB path')
                checksum = hashlib.file_digest(source.extractfile(member), 'sha256').hexdigest()
                if local.stat().st_size != member.size or digest(local) != checksum:
                    raise ValueError('RGB differs from durable archive: ' + str(relative))
                verified[relative] = {'path': str(relative), 'bytes': member.size, 'sha256': checksum}
    actual = set(p.relative_to(run) for p in run.rglob('*.jpg'))
    if not verified or actual != set(verified):
        raise ValueError('Archive RGB inventory mismatch')
    record = {'run': str(run.resolve()), 'archive': str(archive.resolve()),
              'uri': metadata['uri'], 'archive_sha256': metadata['sha256'],
              'files': list(verified.values()), 'bytes': sum(v['bytes'] for v in verified.values()),
              'status': 'verified_before_eviction', 'removed': 0,
              'restore': 'Read archive from uri, verify archive_sha256, then restore listed JPG members under run.'}
    save(receipt, record)
    for relative, item in verified.items():
        local = run / relative
        if digest(local) != item['sha256']:
            raise ValueError('RGB changed after verification')
        local.unlink()
        record['removed'] += 1
    record['status'] = 'evicted_recoverable_rgb_cache'
    save(receipt, record)
    return {key: value for key, value in record.items() if key != 'files'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('run', 'archive', 'backup', 'receipt'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evict(args.run, args.archive, args.backup, args.receipt)))
