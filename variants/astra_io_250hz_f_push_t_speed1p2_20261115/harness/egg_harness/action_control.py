"""Joint goals executed by the native position controller, with measured arrival."""
import numpy as np
from scipy.spatial.transform import Rotation
from .joint_goal import execute_control

DEFAULT_ACTION_INTERFACE = 'joint'


def action_interface(config):
    value = config.get('action_interface', DEFAULT_ACTION_INTERFACE)
    if value != 'joint':
        raise ValueError('The joint-goal variant requires action_interface=joint')
    return value


def matrix_pose(matrix):
    return np.r_[matrix[:3, 3], Rotation.from_matrix(matrix[:3, :3]).as_quat()[[3, 0, 1, 2]]]
