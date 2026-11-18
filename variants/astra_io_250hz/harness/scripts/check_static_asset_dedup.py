"""Check corruption refusal, atomic preservation and durable dedup receipts."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from deduplicate_static_assets import build_plan, replace_duplicate


def setup(base):
    target, source = base / 'cache', base / 'source'
    for root in (target, source):
        (root / 'Assets').mkdir(parents=True)
        (root / 'Assets/a.bin').write_bytes(b'original static bytes' * 100)
    data = (target / 'Assets/a.bin').read_bytes()
    manifest = {'files': [{'path': 'Assets/a.bin', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]}
    return target, source, manifest


with tempfile.TemporaryDirectory() as directory:
    base = Path(directory)
    target, source, manifest = setup(base)
    plan = build_plan(target, [source], manifest)
    assert len(plan) == 1
    original = (target / 'Assets/a.bin').read_bytes()
    with (base / 'journal').open('w+') as journal:
        replace_duplicate(plan[0], journal)
        journal.seek(0)
        records = [json.loads(line) for line in journal]
    assert [row['status'] for row in records] == ['prepared', 'completed']
    assert (target / 'Assets/a.bin').read_bytes() == original
    assert (target / 'Assets/a.bin').stat().st_ino == (source / 'Assets/a.bin').stat().st_ino
    assert not build_plan(target, [source], manifest)

for changed in ('target', 'canonical'):
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        target, source, manifest = setup(base)
        plan = build_plan(target, [source], manifest)
        Path(plan[0][changed]).write_bytes(b'corrupt')
        before = (target / 'Assets/a.bin').read_bytes()
        with (base / 'journal').open('w') as journal:
            try:
                replace_duplicate(plan[0], journal)
            except ValueError:
                pass
            else:
                raise AssertionError('Corruption was accepted')
        assert (target / 'Assets/a.bin').read_bytes() == before
        assert not list(target.rglob('*.tmp'))

with tempfile.TemporaryDirectory() as directory:
    base = Path(directory)
    target, source, manifest = setup(base)
    plan = build_plan(target, [source], manifest)
    before = (target / 'Assets/a.bin').stat().st_ino
    with (base / 'journal').open('w+') as journal, patch('deduplicate_static_assets.os.replace', side_effect=OSError('simulated replace failure')):
        try:
            replace_duplicate(plan[0], journal)
        except OSError:
            pass
        else:
            raise AssertionError('Replace failure was swallowed')
        journal.seek(0)
        assert json.loads(journal.readline())['status'] == 'prepared'
    assert (target / 'Assets/a.bin').stat().st_ino == before
    assert not list(target.rglob('*.tmp'))

for mode in ('symlink', 'permissions', 'layout'):
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        target, source, manifest = setup(base)
        path = target / 'Assets/a.bin'
        if mode == 'symlink':
            path.unlink()
            path.symlink_to(source / 'Assets/a.bin')
        elif mode == 'permissions':
            path.chmod(0o600)
        else:
            manifest['files'][0]['path'] = 'Assets/Eval_Layout/seed.json'
        try:
            build_plan(target, [source], manifest)
        except ValueError:
            pass
        else:
            raise AssertionError(f'Invalid {mode} accepted')

print('Static dedup preserves bytes atomically, journals recovery, rejects corruption/symlink/metadata/layout changes')
