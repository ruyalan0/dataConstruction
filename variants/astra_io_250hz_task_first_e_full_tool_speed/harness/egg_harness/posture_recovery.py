"""One-call bounded shoulder recovery with an explicit public-space envelope."""
import numpy as np
from .posture_action import upright_joint_target, posture_increment


def validate_recovery(action, phase):
    if phase != 'transit' or set(action) != {'recover_posture'}:
        raise ValueError('Recovery requires transit and only recover_posture')
    spec = action['recover_posture']
    if not isinstance(spec, dict) or set(spec) != {'arm', 'reason', 'clearance_box', 'max_steps'}:
        raise ValueError('Recovery requires arm, reason, clearance_box, max_steps')
    if spec['arm'] not in ('left', 'right'):
        raise ValueError('Select one recovery arm')
    if spec['reason'] not in ('approach_blocked', 'descent_blocked', 'withdrawal_blocked'):
        raise ValueError('Recovery requires an obstructed next motion')
    if type(spec['max_steps']) is not int or not 1 <= spec['max_steps'] <= 40:
        raise ValueError('Recovery budget must be 1..40 native steps')
    box = np.asarray(spec['clearance_box'], dtype=float)
    if box.shape != (2, 3) or not np.isfinite(box).all() or np.any(box[0] >= box[1]):
        raise ValueError('clearance_box must be finite ordered world-space bounds')
    if np.any(box[1] - box[0] > 1.):
        raise ValueError('Recovery clearance box exceeds 1m per axis')
    return spec


def points(model, joints, wrist):
    from scipy.spatial.transform import Rotation
    transform = model.observed_base(joints, wrist)
    result = []
    for origin, axis, angle in zip(model.origins, model.axes, joints):
        rotation = np.eye(4)
        rotation[:3, :3] = Rotation.from_rotvec(axis * angle).as_matrix()
        transform = transform @ origin @ rotation
        result.append(transform[:3, 3].copy())
    result.append(transform[:3, 3] + transform[:3, :3] @ np.array([.138, 0, 0]))
    return np.asarray(result[2:])


def check_envelope(model, state, arm, box):
    geometry = points(model, state[arm + '_arm_joint_state'], state[arm + '_ee_pose'])
    low, high = np.asarray(box)
    # 3cm envelope margin is explicit, not a link-mesh collision model.
    if np.any(geometry < low + .03) or np.any(geometry > high - .03):
        raise ValueError('Recovery leaves declared clear envelope (3cm margin)')


def plan_recovery(model, state, spec):
    arm = spec['arm']
    target = upright_joint_target(model, state[arm + '_arm_joint_state'], state[arm + '_ee_pose'])
    simulated = {k: list(v) for k, v in state.items()}
    check_envelope(model, simulated, arm, spec['clearance_box'])
    path = []
    for _ in range(spec['max_steps']):
        if abs(simulated[arm + '_arm_joint_state'][1] - target[1]) < .01:
            break
        joints, poses = posture_increment(model, simulated, {arm: target})
        simulated[arm + '_arm_joint_state'] = joints[arm].tolist()
        simulated[arm + '_ee_pose'] = poses[arm].tolist()
        check_envelope(model, simulated, arm, spec['clearance_box'])
        path.append(poses[arm].tolist())
    return {'target': target, 'path': path, 'planned_steps': len(path),
            'target_reachable_within_budget': abs(simulated[arm + '_arm_joint_state'][1] - target[1]) < .01}
