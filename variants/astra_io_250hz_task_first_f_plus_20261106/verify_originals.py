"""Verify f and inherited parent sources with the pinned upstream skill updates."""
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.append(str(ROOT / 'scripts'))
from seal_release import inventory
from physevo_release import module

SOURCE_UPDATES = {
    'release/harness/skills/play-tic-tac-toe/SKILL.md',
    'variants/astra_io_250hz_task_first_e/harness/skills/stack-bowls/SKILL.md',
}


def source_updates():
    updates = json.loads((HERE / 'merge-provenance.json').read_text())['source_updates']
    if set(updates) != SOURCE_UPDATES:
        raise ValueError('Only the two merged upstream skills may update source pins')
    return updates


def updated_pins(hashes):
    hashes = hashes.copy()
    for name, record in source_updates().items():
        if name not in hashes:
            continue
        if hashes[name] != record['previous_sha256']:
            raise ValueError('Upstream skill update does not match its original pin: ' + name)
        hashes[name] = record['sha256']
    return hashes


def verify():
    hashes = json.loads((HERE / 'originals-sha256.json').read_text())
    hashes.update(json.loads((HERE / 'baseline-sha256.json').read_text()))
    hashes.update(json.loads((HERE / 'parent-d-sha256.json').read_text()))
    hashes.update(json.loads((HERE / 'parent-e-sha256.json').read_text()))
    hashes.update(json.loads((HERE / 'parent-speed-sha256.json').read_text()))
    hashes.update(json.loads((HERE / 'parent-f-sha256.json').read_text()))
    hashes = updated_pins(hashes)
    changed = [name for name, expected in hashes.items() if not (ROOT / name).is_file()
               or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected]
    return dict(passed=not changed, protected_files=len(hashes), differences=changed)


def verify_release():
    """Check the complete release inventory with the exact recorded skill update."""
    if not verify()['passed']:
        raise ValueError('Protected source files changed outside the pinned skill updates')
    release = ROOT / 'release'
    expected = updated_pins(json.loads((release / 'manifest.json').read_text())['files'])
    if inventory(ROOT) != expected:
        raise ValueError('Release inventory differs outside the pinned skill update')
    harness = release / 'harness'
    frozen = json.loads((harness / 'manifest.json').read_text())
    prefix = 'release/harness/'
    expected = updated_pins({prefix + name: value for name, value in frozen['files'].items()})
    actual = {prefix + name: value for name, value in
              module(harness / 'egg_harness/freeze.py').digest(harness).items()}
    if actual != expected:
        raise ValueError('Frozen harness differs outside the pinned skill update')
    dependencies = json.loads((release / 'dependencies.json').read_text())
    if frozen['commit'] != dependencies['harness_commit']:
        raise ValueError('Harness provenance differs from release pin')
    return dependencies


if __name__ == '__main__':
    report = verify()
    if report['passed']:
        verify_release()
        report.update(release_verified=True, pinned_source_updates=sorted(source_updates()))
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 1)
