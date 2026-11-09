"""Archive actual initial request evidence for an already-running layout round."""
import argparse
import os
from pathlib import Path
import tarfile

import five_layout_suite as suite
from prepare_eight_eval import save


def archive(root, layout):
    output = root / 'artifacts/five-layout-suite'
    folder = output / f'L{layout}'
    campaign = suite.read(folder / 'campaign.json')
    assert os.environ['ARNOLD_WORKER_ID'] == campaign['worker']
    assert not suite.git(root, 'status', '--porcelain'), 'Commit archive tooling first'
    receipt = folder / 'startup-audit-receipt.json'
    assert not receipt.exists(), 'Startup evidence already archived'
    assert not (folder / 'completed.json').exists(), 'This round has already ended'
    assert suite.read(folder / 'source-inputs-receipt.json')['verified_readback']
    checked = suite.audit_round(root, output, layout, final=False)
    assert len(checked) == 16 and all(row['started'] and row['azure_requests'] > 0 for row in checked)
    record = {
        **suite.read(folder / 'live-audit.json'),
        'scope': 'Startup and requests observed so far only; final outcomes and complete request evidence remain unaudited.',
        'execution_source_commit': campaign['source_commit'],
        'audit_source_commit': suite.git(root, 'rev-parse', 'HEAD'),
    }
    save(folder / 'startup-audit.json', record)
    archive_path = suite.SCRATCH / f'L{layout}/startup-audit.tar.gz'
    with tarfile.open(archive_path, 'w:gz') as target:
        for name in ('startup-audit.json', 'campaign.json', 'ready.json',
                     'skills-manifest.json', 'source-inputs-receipt.json'):
            target.add(folder / name, arcname=name)
        target.add(Path(__file__), arcname='archive_five_layout_startup.py')
        for row in checked:
            name = f"{row['task']}-L{layout}-attempt1"
            case = suite.SCRATCH / f'L{layout}/runs' / name
            for relative in ('config.json', 'launch.json', 'episode/provenance.json',
                             'policy/skills.json', 'policy/model/native-tools.json',
                             'policy/model/0001.request.json'):
                target.add(case / relative, arcname=f'cases/{name}/{relative}')
    suite.runner.hdfs_archive(archive_path, suite.HDFS + f'/L{layout}/startup-audit.tar.gz', receipt)
    print(receipt.read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--layout', type=int, choices=suite.LAYOUTS, required=True)
    args = parser.parse_args()
    archive(args.root.resolve(), args.layout)
