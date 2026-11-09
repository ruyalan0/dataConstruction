"""Development-only simultaneous motion probe; never task acceptance."""
import argparse
import json
import time
import traceback
from pathlib import Path

import numpy as np

from .client import Robot
from .skill_policy import compile_motion, execute_bilateral, validate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--episode', type=Path, required=True)
    parser.add_argument('--trace', type=Path, required=True)
    args = parser.parse_args()
    args.trace.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = {'status': 'probe_failed', 'not_task_success': True, 'model_calls': 0}
    robot = None
    try:
        robot = Robot(args.episode, args.trace)
        before = robot.obs
        if before['step'] != 0 or before['ended']:
            raise ValueError('Probe requires a fresh development scene')
        arms = {}
        for arm in ('left', 'right'):
            pose = before['state'][arm + '_ee_pose']
            position = list(pose[:3])
            position[2] += .01
            arms[arm] = {'toward': {'position': position, 'quaternion': pose[3:]}, 'gripper': 1}
        action = {'arms': arms, 'steps': 4, 'settle': 0}
        (args.trace / 'probe-action.json').write_text(json.dumps(action, indent=2))
        compiled, steps, settle = compile_motion(action, 'fine', before, validate)
        feedback = execute_bilateral(robot, 'move_local', compiled, steps, settle, {'left': 1, 'right': 1}, 'fine')
        (args.trace / 'motion-feedback.json').write_text(json.dumps(feedback, indent=2))
        if feedback['steps'] != steps:
            raise RuntimeError('Bilateral segment did not complete its four native steps')
        for arm, item in feedback['arms'].items():
            if item['achieved_displacement'][2] < .005 or item['wrist_error_m'] > .005:
                raise RuntimeError('Bilateral measured wrist motion failed: ' + arm)
        rows = [json.loads(line) for line in (args.episode / 'actions.jsonl').read_text().splitlines()]
        simultaneous = []
        previous = before['state']
        for row in rows:
            deltas = {arm: float(np.max(np.abs(np.asarray(row['action'][arm + '_arm_joint_state']) - previous[arm + '_arm_joint_state']))) for arm in arms}
            if any(value > .150001 for value in deltas.values()):
                raise RuntimeError('Native joint bound exceeded')
            if all(value > 1e-5 for value in deltas.values()):
                simultaneous.append(row['step'])
            previous = json.loads((args.episode / 'frames' / ('step-%04d.json' % row['step'])).read_text())['state']
        if not simultaneous:
            raise RuntimeError('No native step commanded motion of both arms')
        result.update(status='probe_completed', simultaneous_native_steps=simultaneous, feedback=feedback)
    except BaseException:
        result['error'] = traceback.format_exc()
        raise
    finally:
        result.update(seconds=time.monotonic() - started, steps=robot.obs['step'] if robot else None)
        (args.trace / 'policy-result.json').write_text(json.dumps(result, indent=2))
        if robot is not None:
            robot.command('finish')


if __name__ == '__main__':
    main()
