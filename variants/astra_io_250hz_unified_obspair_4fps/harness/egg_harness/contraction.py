"""Finite shared Cartesian contraction; no physical action or branch jump."""
import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from .kinematics import pose_matrix


def solve_contracted(model, state, targets, active_arms):
    if not set(active_arms) <= {'left', 'right'} or set(targets) != {'left', 'right'}:
        raise ValueError('Invalid contraction arms')
    for arm in targets:
        model.joints_array(state[arm + '_arm_joint_state'])
        pose_matrix(state[arm + '_ee_pose'])
        pose_matrix(targets[arm])
    attempts = []
    for scale in (1., .5, .25, .125):
        solutions, selected, errors = {}, {}, {}
        for arm, target in targets.items():
            measured = np.asarray(state[arm + '_ee_pose'])
            target = np.asarray(target)
            rotations = Rotation.from_quat(np.asarray([measured[3:], target[3:]])[:, [1, 2, 3, 0]])
            pose = np.r_[measured[:3] + scale * (target[:3] - measured[:3]),
                         Slerp([0, 1], rotations)(scale).as_quat()[[3, 0, 1, 2]]]
            selected[arm] = pose
            joints = state[arm + '_arm_joint_state']
            try:
                solutions[arm] = model.solve_local(joints, measured, pose) if arm in active_arms and not np.allclose(pose, measured, atol=1e-7) else model.joints_array(joints)
            except ValueError as error:
                errors[arm] = str(error)
        attempts.append({'scale': scale, 'errors': errors})
        if not errors:
            return {'scale': scale, 'solutions': solutions, 'targets': selected, 'attempts': attempts}
    return {'scale': None, 'solutions': None, 'targets': None, 'attempts': attempts}
