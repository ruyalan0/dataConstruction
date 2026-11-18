"""Export permitted observations from a completed, archived case for skill review."""
import argparse
import json
import os
from pathlib import Path
import shutil

import five_layout_suite as suite
from audit_five_layout_completion import case_durability
from prepare_eight_eval import save

ROBOT_FIELDS = {f'{arm}_{field}' for arm in ('left', 'right') for field in (
    'arm_joint_state', 'ee_pose', 'ee_joint_state', 'gripper_measured_joints')}
FRAME_FIELDS = {'step', 'state', 'cameras', 'camera_calibration_id',
                'action_interface', 'instruction', 'ended', 'budget'}
CAMERAS = ('cam_head', 'cam_left_wrist', 'cam_right_wrist')


def export(root, layout, task, steps):
    folder = root / f'artifacts/five-layout-suite/L{layout}'
    campaign = suite.read(folder / 'campaign.json')
    assert os.environ['ARNOLD_WORKER_ID'] == campaign['worker']
    assert task in {t for t, index, _ in campaign['cases'] if index == layout}
    name = f'{task}-L{layout}-attempt1'
    archived = folder / 'cases' / name
    assert suite.read(archived / 'result.json')['finished_at']
    case_durability(archived, suite.HDFS + f'/L{layout}')
    case = suite.SCRATCH / f'L{layout}/runs' / name
    latest = suite.read(case / 'episode/latest.json')['step']
    assert steps and len(steps) == len(set(steps)) and all(0 <= step <= latest for step in steps)
    out = folder / 'review' / task
    out.mkdir(parents=True, exist_ok=True)
    for step in steps:
        filename = 'initial.json' if step == 0 else f'step-{step:04d}.json'
        frame = suite.read(case / 'episode/frames' / filename)
        assert frame['step'] == step and set(frame) <= FRAME_FIELDS
        assert set(frame['state']) == ROBOT_FIELDS and set(frame['cameras']) == set(CAMERAS)
        save(out / f'{step:04d}-public-frame.json', frame)
        for camera in CAMERAS:
            path = Path(frame['cameras'][camera])
            path.relative_to(case)
            shutil.copyfile(path, out / f'{step:04d}-{camera}.jpg')
    decisions = []
    for line in (case / 'policy.log').read_text().splitlines():
        step, separator, body = line.partition(' ')
        if separator and step.isdigit() and body.startswith('{'):
            row = json.loads(body)
            decisions.append({'step': int(step), **{key: row[key] for key in (
                'assessment', 'plan', 'phase', 'memory')}})
    save(out / 'policy-decisions.json', decisions)
    save(out / 'export.json', {'at': suite.runner.now(), 'task': task, 'layout': layout,
         'steps': steps, 'exporter_sha256': suite.base.digest(Path(__file__)),
         'source': 'Recorded permitted RGB/robot state and same-episode policy summaries only; outside policy inputs.'})
    print(json.dumps({'folder': str(out), 'steps': steps, 'decisions': len(decisions)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--layout', type=int, choices=suite.LAYOUTS, required=True)
    parser.add_argument('--task', choices=suite.base.task_names(), required=True)
    parser.add_argument('--steps', nargs='+', type=int, required=True)
    args = parser.parse_args()
    export(args.root.resolve(), args.layout, args.task, args.steps)
