"""Compile an explicitly requested bounded prefix of a model-selected pose goal."""
import numpy as np
from scipy.spatial.transform import Rotation, Slerp


def compile_segment(action, phase, obs):
    if "toward" not in action:
        return dict(action), None
    if phase not in ("fine", "transit"):
        raise ValueError("Invalid control phase")
    if set(action) - {"arm", "toward", "gripper", "steps", "settle"}:
        raise ValueError("toward cannot be mixed with direct pose fields")
    arm = action["arm"]
    if arm not in ("left", "right"):
        raise ValueError("Unknown arm")
    goal = action["toward"]
    if not isinstance(goal, dict) or not goal or set(goal) - {"position", "quaternion"}:
        raise ValueError("Invalid toward goal")
    pose = np.asarray(obs["state"][arm + "_ee_pose"], dtype=float)
    position = np.asarray(goal.get("position", pose[:3]), dtype=float)
    quaternion = np.asarray(goal.get("quaternion", pose[3:]), dtype=float)
    if position.shape != (3,) or quaternion.shape != (4,) or not np.isfinite(np.r_[position, quaternion]).all():
        raise ValueError("Invalid toward pose")
    norm = np.linalg.norm(quaternion)
    if not .5 <= norm <= 1.5:
        raise ValueError("toward quaternion norm outside [.5,1.5]")
    quaternion = quaternion / norm
    rotations = Rotation.from_quat(np.asarray([pose[3:], quaternion])[:, [1, 2, 3, 0]])
    distance = float(np.linalg.norm(position - pose[:3]))
    angle = float((rotations[1] * rotations[0].inv()).magnitude() * 180 / np.pi)
    max_distance, max_angle = (.024, 11.) if phase == "fine" else (.29, 85.)
    fraction = min(1., max_distance / distance if distance else 1., max_angle / angle if angle else 1.)
    segment = {key: value for key, value in action.items() if key != "toward"}
    segment["position"] = (pose[:3] + fraction * (position - pose[:3])).tolist()
    segment["quaternion"] = Slerp([0, 1], rotations)(fraction).as_quat()[[3, 0, 1, 2]].tolist()
    return segment, {"goal": goal, "fraction": fraction, "goal_completed_by_requested_segment": fraction == 1.}
