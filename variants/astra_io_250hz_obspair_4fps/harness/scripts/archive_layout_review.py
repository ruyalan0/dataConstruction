"""Archive a committed skill review separately from an active round's inputs."""
import argparse
import os
from pathlib import Path
import tarfile

import five_layout_suite as suite
from audit_five_layout_completion import case_durability
from prepare_eight_eval import save


def archive(root, layout, stage):
    assert stage > 0
    output = root / 'artifacts/five-layout-suite'
    folder = output / f'L{layout}'
    campaign = suite.read(folder / 'campaign.json')
    assert os.environ['ARNOLD_WORKER_ID'] == campaign['worker']
    assert not suite.git(root, 'status', '--porcelain'), 'Commit review and source first'
    label = f'review-{stage:02d}'
    receipt = folder / f'{label}-receipt.json'
    assert not receipt.exists(), 'This review stage is already archived'
    selection = suite.read(root / 'skills/suite-task-selection.json')
    selected = set(selection['common'] + [name for names in selection['tasks'].values() for name in names])
    names = [f'L{layout}-SKILL-REVIEW.md', 'AZURE-FIVE-LAYOUT-PROGRESS.md',
             'SKILL-INPUT-AUDIT.md', 'skills/suite-task-selection.json',
             'scripts/archive_layout_review.py', 'scripts/export_public_case_review.py',
             'scripts/observe_layout_round.py', 'scripts/archive_five_layout_startup.py']
    names += [f'skills/{name}/SKILL.md' for name in sorted(selected)]
    cases = sorted(path for path in (folder / 'cases').iterdir() if (path / 'result.json').exists())
    for case in cases:
        case_durability(case, suite.HDFS + f'/L{layout}')
    checked = suite.audit_round(root, output, layout, final=False)
    manifest = {'at': suite.runner.now(), 'source_commit': suite.git(root, 'rev-parse', 'HEAD'),
        'execution_source_commit': campaign['source_commit'], 'completed_cases': [p.name for p in cases],
        'azure_requests_checked': sum(row['azure_requests'] for row in checked),
        'files': {name: suite.base.digest(root / name) for name in names},
        'scope': 'Public-observation review and candidate source skills only; running inputs remain fixed.'}
    save(folder / f'{label}-manifest.json', manifest)
    archive_path = suite.SCRATCH / f'L{layout}/{label}.tar.gz'
    with tarfile.open(archive_path, 'w:gz') as target:
        for name in names:
            target.add(root / name, arcname=name)
        target.add(folder / f'{label}-manifest.json', arcname='manifest.json')
        target.add(folder / 'review', arcname='public-review')
        target.add(folder / 'live-audit.json', arcname='live-input-audit.json')
        for case in cases:
            for path in [case / 'result.json', case / 'durability.json', *sorted(case.glob('shard-*.json'))]:
                target.add(path, arcname=f'cases/{case.name}/{path.name}')
    suite.runner.hdfs_archive(archive_path, suite.HDFS + f'/L{layout}/{label}.tar.gz', receipt)
    print(receipt.read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--layout', type=int, choices=suite.LAYOUTS, required=True)
    parser.add_argument('--stage', type=int, required=True)
    args = parser.parse_args()
    archive(args.root.resolve(), args.layout, args.stage)
