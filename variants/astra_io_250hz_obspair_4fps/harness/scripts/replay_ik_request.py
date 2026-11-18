"""Replay one recorded local-IK boundary using public measured robot state."""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from egg_harness.kinematics import Kinematics

p = argparse.ArgumentParser()
p.add_argument('episode', type=Path)
p.add_argument('--command', required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--step-multipliers', type=int, nargs='+', default=[1])
p.add_argument('--joint-bound', type=float, default=.15)
a = p.parse_args()
if a.output.exists():
    raise FileExistsError(a.output)
command = json.loads((a.episode / 'commands' / (a.command + '.json')).read_text())
events = [json.loads(line) for line in (a.episode / 'interruptions.jsonl').read_text().splitlines()]
event = next(row for row in events if row['command'] == a.command and row['reason'] == 'local_ik_rejected')
actions = [json.loads(line) for line in (a.episode / 'actions.jsonl').read_text().splitlines()]
executed = [row for row in actions if row['command'] == a.command]
start_step = event['step'] - len(executed)
def state(step):
    name = 'initial.json' if step == 0 else 'step-%04d.json' % step
    return json.loads((a.episode / 'frames' / name).read_text())['state']
start, current = state(start_step), state(event['step'])
model = Kinematics(json.loads(Path('configs/x5-kinematics.json').read_text()))
rows = []
for multiplier in a.step_multipliers:
    if multiplier < 1:
        raise ValueError('Positive step multiplier required')
    fraction = (len(executed) + 1) / (command['steps'] * multiplier)
    for arm, spec in command['arms'].items():
        before = np.asarray(start[arm + '_ee_pose'])
        target = before.copy()
        target[:3] = spec.get('position', before[:3])
        target[3:] = spec.get('quaternion', before[3:])
        rotations = Rotation.from_quat(np.asarray([before[3:], target[3:]])[:, [1, 2, 3, 0]])
        pose = np.r_[before[:3] * (1-fraction) + target[:3] * fraction, Slerp([0, 1], rotations)(fraction).as_quat()[[3, 0, 1, 2]]]
        row = dict(arm=arm, multiplier=multiplier, target=pose.tolist(), joint_bound=a.joint_bound)
        try:
            solution = model.solve_local(current[arm + '_arm_joint_state'], current[arm + '_ee_pose'], pose, max_joint_delta=a.joint_bound)
            row.update(status='solved', max_joint_delta=float(np.max(np.abs(solution-current[arm + '_arm_joint_state']))))
        except ValueError as error:
            row.update(status='rejected', error=str(error))
        rows.append(row)
report = dict(command=command, recorded_event=event, start_step=start_step, observed_step=event['step'], executed_steps=len(executed), rows=rows, scope='One IK boundary only; longer-duration counterfactual is not a simulated trajectory or proof of collision freedom')
a.output.write_text(json.dumps(report, indent=2))
print(json.dumps(report))
