"""Mutate physical records to prove independent posture checks reject violations."""
import copy
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from egg_harness.kinematics import Kinematics
from posture_execution_checks import check_record

model = Kinematics(json.loads(Path('configs/x5-kinematics.json').read_text()))
q = np.array([.3, 3., 4.6, -1.9, .16, .48])
def pose(q):
    t = model.forward(q)
    return np.r_[t[:3, 3], Rotation.from_matrix(t[:3, :3]).as_quat()[[3, 0, 1, 2]]].tolist()
before = {}
for arm in ('left', 'right'):
    before[arm+'_arm_joint_state'] = q.tolist()
    before[arm+'_ee_pose'] = pose(q)
    before[arm+'_gripper_measured_joints'] = [.044, .044]
    before[arm+'_ee_joint_state'] = [.31] if arm == 'right' else [1.]
target = q.copy(); target[1] -= .01
row = {'action': {}, 'cartesian_target': {}}
after = copy.deepcopy(before)
for arm in ('left', 'right'):
    t = target if arm == 'left' else q
    row['action'][arm+'_arm_joint_state'] = t.tolist()
    row['action'][arm+'_ee_joint_state'] = [1.] if arm == 'left' else [0.]
    row['cartesian_target'][arm+'_ee_pose'] = pose(t)
    after[arm+'_arm_joint_state'] = t.tolist()
    after[arm+'_ee_pose'] = pose(t)
grip = {'left': [1.], 'right': [0.]}
assert check_record(model,before,after,row,{'left'},grip)['left']['max_point_displacement_m'] < .024
mutations = [
    ('nonshoulder', lambda r: r['action']['left_arm_joint_state'].__setitem__(0, .31)),
    ('large_joint', lambda r: r['action']['left_arm_joint_state'].__setitem__(1, 2.8)),
    ('inactive', lambda r: r['action']['right_arm_joint_state'].__setitem__(1, 2.99)),
    ('gripper', lambda r: r['action']['right_ee_joint_state'].__setitem__(0, 1.)),
    ('fk', lambda r: r['cartesian_target']['left_ee_pose'].__setitem__(0, 9.)),
]
for name, mutate in mutations:
    invalid = copy.deepcopy(row); mutate(invalid)
    try: check_record(model,before,after,invalid,{'left'},grip)
    except ValueError: pass
    else: raise AssertionError(name+' mutation accepted')
closed = copy.deepcopy(before); closed['left_gripper_measured_joints'] = [.02,.02]
try: check_record(model,closed,after,row,{'left'},grip)
except ValueError: pass
else: raise AssertionError('Closed hand accepted')
print('Independent posture audit rejects altered joints, inactive motion, grip intent, FK and closed hands')
for angle, expected in [(2.85, 'displacement'), (3.01, 'lowered')]:
    invalid = copy.deepcopy(row)
    changed = q.copy(); changed[1] = angle
    invalid['action']['left_arm_joint_state'] = changed.tolist()
    invalid['cartesian_target']['left_ee_pose'] = pose(changed)
    try: check_record(model,before,after,invalid,{'left'},grip)
    except ValueError as error: assert expected in str(error), str(error)
    else: raise AssertionError(expected+' violation accepted')
print('Geometrically consistent targets still fail oversized point motion and downward link2 rotation')
