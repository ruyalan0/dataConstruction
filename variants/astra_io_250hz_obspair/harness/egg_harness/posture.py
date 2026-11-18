"""Measured link posture from public arm joints and wrist pose only."""
import numpy as np
from scipy.spatial.transform import Rotation


def posture_context(model, state):
    result = {'definition': 'link2 is joint2-origin to joint3-origin; tilt0 vertical,90 horizontal, direction sign ignored',
              'scope': 'Measured public robot geometry; not object state, contact sensing or path feasibility',
              'arms': {}}
    for arm in ('left', 'right'):
        joints = model.joints_array(state[arm + '_arm_joint_state'])
        transform = model.observed_base(joints, state[arm + '_ee_pose'])
        points = []
        for origin, axis, angle in zip(model.origins, model.axes, joints):
            rotation = np.eye(4)
            rotation[:3, :3] = Rotation.from_rotvec(axis * angle).as_matrix()
            transform = transform @ origin @ rotation
            points.append(transform[:3, 3].copy())
        direction = points[2] - points[1]
        length = np.linalg.norm(direction)
        if length <= 0:
            raise ValueError('Degenerate public link2 geometry')
        result['arms'][arm] = {
            'link2_tilt_from_vertical_deg': float(np.degrees(np.arccos(np.clip(abs(direction[2]) / length, 0, 1)))),
            'joint2_origin_world_m': points[1].tolist(),
            'joint3_origin_world_m': points[2].tolist()}
    return result
