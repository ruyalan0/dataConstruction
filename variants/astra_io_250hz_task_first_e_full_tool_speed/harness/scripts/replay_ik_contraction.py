"""Offline fixed-count Cartesian contraction at recorded development IK failures."""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from egg_harness.kinematics import Kinematics


def interpolate(before, after, fraction):
    rotations = Rotation.from_quat(np.asarray([before[3:], after[3:]])[:, [1, 2, 3, 0]])
    return np.r_[before[:3] + fraction * (after[:3] - before[:3]),
                 Slerp([0, 1], rotations)(fraction).as_quat()[[3, 0, 1, 2]]]


def replay(episode, model):
    provenance = json.loads((episode / 'provenance.json').read_text())
    if provenance['mode'] != 'development':
        raise ValueError('Diagnostic accepts development records only')
    events = [json.loads(line) for line in (episode / 'interruptions.jsonl').read_text().splitlines()]
    actions = [json.loads(line) for line in (episode / 'actions.jsonl').read_text().splitlines()]
    by_command = {}
    for action in actions:
        by_command.setdefault(action['command'], []).append(action)
    def state(step):
        name = 'initial' if step == 0 else f'step-{step:04d}'
        return json.loads((episode / 'frames' / (name + '.json')).read_text())['state']
    rows = []
    for event in events:
        if event['reason'] != 'local_ik_rejected':
            continue
        command = json.loads((episode / 'commands' / (event['command'] + '.json')).read_text())
        if command['op'] != 'move_local':
            raise ValueError('Unexpected rejected operation')
        executed = by_command.get(event['command'], [])
        start = state(event['step'] - len(executed))
        current = state(event['step'])
        fraction = min((len(executed) + 1) / command['steps'], 1)
        nominal = {}
        for arm, spec in command['arms'].items():
            if set(spec) - {'position', 'quaternion', 'gripper'}:
                raise ValueError('Unsupported command pose representation')
            before = np.asarray(start[arm + '_ee_pose'])
            goal = before.copy()
            goal[:3] = spec.get('position', before[:3])
            goal[3:] = spec.get('quaternion', before[3:])
            nominal[arm] = interpolate(before, goal, fraction)
        attempts = []
        for scale in (1., .5, .25, .125):
            arms = {}
            for arm, target in nominal.items():
                measured = np.asarray(current[arm + '_ee_pose'])
                contracted = interpolate(measured, target, scale)
                joints = np.asarray(current[arm + '_arm_joint_state'])
                record = {'target': contracted.tolist()}
                try:
                    solved = model.solve_local(joints, measured, contracted)
                    record.update(solved=True, max_joint_delta=float(np.max(np.abs(solved - joints))))
                except ValueError as error:
                    record.update(solved=False, error=str(error))
                arms[arm] = record
            attempts.append({'scale': scale, 'all_arms_solved': all(r['solved'] for r in arms.values()), 'arms': arms})
        rows.append({'command': event['command'], 'step': event['step'], 'executed_steps': len(executed),
                     'attempts': attempts, 'first_solved_scale': next((r['scale'] for r in attempts if r['all_arms_solved']), None)})
    if not rows:
        raise ValueError('No recorded local IK rejection')
    return {'episode': str(episode), 'rows': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('episodes', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    model = Kinematics(json.loads((Path(__file__).resolve().parents[1] / 'configs/x5-kinematics.json').read_text()))
    reports = [replay(episode, model) for episode in args.episodes]
    report = {'scope': 'Offline boundary solves only; no physical execution, no collision guarantee, no task success. Each scale contracts from current measured pose toward the original next target; both arms share the scale. Four fixed candidates, unchanged 0.15rad bound. Not installed in runtime.', 'reports': reports}
    args.output.write_text(json.dumps(report, indent=2))
    for item in reports:
        print(json.dumps({'episode': item['episode'], 'boundaries': len(item['rows']),
            'nominal_rejections_reproduced': sum(not r['attempts'][0]['all_arms_solved'] for r in item['rows']),
            'solved_with_contraction': sum(r['first_solved_scale'] is not None and r['first_solved_scale'] < 1 for r in item['rows']),
            'still_rejected': sum(r['first_solved_scale'] is None for r in item['rows'])}))
