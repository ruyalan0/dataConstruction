"""Export public observations from newly completed pour/kong rerun cases."""
import argparse
import json
import os
from pathlib import Path
import shutil

import rerun_pouring_and_kong as run
from export_public_case_review import CAMERAS, FRAME_FIELDS, ROBOT_FIELDS
from prepare_eight_eval import save

# Review checkpoints come from this campaign's recorded public observations.
# They are reporting metadata, never policy inputs or action targets.
CHECKPOINTS = {
    ('pour_liquid_into_cup', 20): [100, 212, 252, 271],
    ('pour_liquid_into_cup', 21): [219, 240, 276],
    ('pour_liquid_into_cup', 22): [81, 89, 128, 181, 211],
    ('pour_liquid_into_cup', 23): [104, 169, 271, 290],
    ('pour_liquid_into_cup', 24): [243, 249, 260, 264],
    ('make_kong', 20): [205, 291, 309],
    ('make_kong', 21): [300, 332, 405],
    ('make_kong', 22): [103, 274, 338],
    ('make_kong', 23): [262, 293, 305, 306, 308],
    ('make_kong', 24): [158, 182, 243, 355, 375],
}


def export(root):
    output = root/'artifacts'/run.NAME
    campaign = run.suite.read(output/'campaign.json')
    assert os.environ['ARNOLD_WORKER_ID'] == campaign['worker']
    for task, layout, _ in campaign['cases']:
        name = f'{task}-L{layout}-attempt2'
        folder = output/task/'cases'/name
        record = folder/'durability.json'
        if not record.exists() or not run.suite.read(record)['final']:
            continue
        out = output/task/'review'/f'L{layout}'
        if (out/'export.json').exists():
            continue
        result = run.suite.read(folder/'result.json')
        run.single.audit.case_durability(folder, run.HDFS)
        case = run.SCRATCH/'runs'/name
        latest = run.suite.read(case/'episode/latest.json')['step']
        assert latest == result['native']['steps']
        steps = sorted({0, latest, *CHECKPOINTS.get((task, layout), [])})
        assert all(0 <= step <= latest for step in steps)
        out.mkdir(parents=True, exist_ok=False)
        hashes = {}
        for step in steps:
            filename = 'initial.json' if step == 0 else f'step-{step:04d}.json'
            frame = run.suite.read(case/'episode/frames'/filename)
            assert frame['step'] == step and set(frame) <= FRAME_FIELDS
            assert set(frame['state']) == ROBOT_FIELDS and set(frame['cameras']) == set(CAMERAS)
            save(out/f'{step:04d}-public-frame.json', frame)
            for camera in CAMERAS:
                source = Path(frame['cameras'][camera])
                assert not source.is_symlink() and source.resolve().parent == (case/'episode/frames').resolve()
                target = out/f'{step:04d}-{camera}.jpg'
                shutil.copyfile(source, target)
                hashes[target.name] = run.suite.base.digest(target)
                assert hashes[target.name] == run.suite.base.digest(source)
        decisions = []
        for line in (case/'policy.log').read_text().splitlines():
            step, separator, body = line.partition(' ')
            if separator and step.isdigit() and body.startswith('{'):
                row = json.loads(body)
                decisions.append({'step': int(step), **{key: row[key] for key in (
                    'assessment', 'plan', 'phase', 'memory')}})
        save(out/'policy-decisions.json', decisions)
        save(out/'export.json', {'at': run.suite.runner.now(), 'task': task, 'layout': layout,
             'steps': steps, 'rgb_sha256': hashes, 'result_sha256': run.suite.base.digest(folder/'result.json'),
             'exporter_sha256': run.suite.base.digest(Path(__file__)),
             'source': 'Recorded permitted RGB/robot state and same-episode policy summaries; outside policy inputs.'})
        print(json.dumps({'task': task, 'layout': layout, 'steps': steps, 'decisions': len(decisions)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    export(parser.parse_args().root.resolve())
