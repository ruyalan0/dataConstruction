"""Independent geometric limits for recorded posture commands."""
import numpy as np
from scipy.spatial.transform import Rotation


def points(model, joints, base):
    transform = base.copy()
    result = []
    for origin, axis, angle in zip(model.origins, model.axes, joints):
        rotation = np.eye(4)
        rotation[:3, :3] = Rotation.from_rotvec(axis * angle).as_matrix()
        transform = transform @ origin @ rotation
        result.append(transform[:3, 3].copy())
    result.append(transform[:3, 3] + transform[:3, :3] @ np.array([.138, 0, 0]))
    return np.asarray(result)


def check_record(model, before, after, row, selected, previous_grip):
    report = {}
    for arm in ('left', 'right'):
        start = np.asarray(before[arm + '_arm_joint_state'], dtype=float)
        target = np.asarray(row['action'][arm + '_arm_joint_state'], dtype=float)
        actual = np.asarray(after[arm + '_arm_joint_state'], dtype=float)
        if any(v.shape != (6,) or not np.all(np.isfinite(v)) for v in (start, target, actual)):
            raise ValueError('Invalid recorded joints')
        delta = target - start
        if np.max(np.abs(delta)) > .15 + 1e-9:
            raise ValueError('Posture joint increment exceeded')
        if np.max(np.abs(delta[[0, 2, 3, 4, 5]])) > 1e-9:
            raise ValueError('Posture changed a non-shoulder target')
        if arm not in selected and np.max(np.abs(delta)) > 1e-9:
            raise ValueError('Inactive arm target changed')
        if np.any(target < model.lower - 1e-9) or np.any(target > model.upper + 1e-9):
            raise ValueError('Posture target outside joint limits')
        base = model.observed_base(start, before[arm + '_ee_pose'])
        initial = points(model, start, base)
        commanded = points(model, target, base)
        swept = float(np.max(np.linalg.norm(commanded[2:] - initial[2:], axis=1)))
        if swept > .024 + 1e-8:
            raise ValueError('Posture downstream point displacement exceeded')
        expected = base @ model.forward(target)
        pose = np.asarray(row['cartesian_target'][arm + '_ee_pose'], dtype=float)
        if pose.shape != (7,) or not np.all(np.isfinite(pose)):
            raise ValueError('Invalid recorded Cartesian target')
        rotation = Rotation.from_quat(pose[[4, 5, 6, 3]]).as_matrix()
        if not np.allclose(expected[:3, 3], pose[:3], atol=1e-8, rtol=0) or not np.allclose(expected[:3, :3], rotation, atol=1e-8, rtol=0):
            raise ValueError('Recorded Cartesian target is not commanded FK')
        echo = np.asarray(row['action'][arm + '_ee_joint_state'], dtype=float)
        if echo.shape != (1,) or not np.all(np.isfinite(echo)):
            raise ValueError('Invalid gripper command')
        if not np.allclose(echo, previous_grip[arm], atol=1e-8, rtol=0):
            raise ValueError('Posture changed gripper intent')
        if arm in selected:
            opening = np.asarray(before[arm + '_gripper_measured_joints'], dtype=float)
            if echo[0] < .8 or opening.size == 0 or not np.all(np.isfinite(opening)) or np.min(opening) < .035:
                raise ValueError('Posture started with a closed selected hand')
            if commanded[2, 2] - commanded[1, 2] < initial[2, 2] - initial[1, 2] - 1e-8:
                raise ValueError('Posture command lowered link2 direction')
        report[arm] = {'selected': arm in selected, 'max_point_displacement_m': swept,
                       'joint_tracking_rad': float(np.max(np.abs(actual - target))),
                       'wrist_tracking_m': float(np.linalg.norm(np.asarray(after[arm + '_ee_pose'][:3]) - pose[:3]))}
    return report
