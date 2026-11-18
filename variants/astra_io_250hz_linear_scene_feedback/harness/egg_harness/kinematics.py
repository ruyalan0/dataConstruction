"""Public-URDF kinematics and local, bounded IK; no environment state access."""
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation


def pose_matrix(pose):
    pose = np.asarray(pose, dtype=float)
    if pose.shape != (7,) or not np.isfinite(pose).all():
        raise ValueError("Invalid wrist pose")
    if abs(np.linalg.norm(pose[3:]) - 1) > .003:
        raise ValueError("Invalid wrist quaternion")
    matrix = np.eye(4)
    matrix[:3, 3] = pose[:3]
    matrix[:3, :3] = Rotation.from_quat(pose[[4, 5, 6, 3]]).as_matrix()
    return matrix


class Kinematics:
    def __init__(self, model):
        self.joints = model["joints"]
        if len(self.joints) != 6:
            raise ValueError("Expected six public arm joints")
        self.origins = []
        self.axes = []
        for joint in self.joints:
            origin = np.eye(4)
            origin[:3, 3] = joint["xyz"]
            origin[:3, :3] = Rotation.from_euler("xyz", joint["rpy"]).as_matrix()
            self.origins.append(origin)
            self.axes.append(np.asarray(joint["axis"], dtype=float))
        self.lower = np.array([j["lower"] for j in self.joints])
        self.upper = np.array([j["upper"] for j in self.joints])

    def joints_array(self, joints):
        joints = np.asarray(joints, dtype=float)
        if joints.shape != (6,) or not np.isfinite(joints).all():
            raise ValueError("Invalid arm joint vector")
        if np.any(joints < self.lower) or np.any(joints > self.upper):
            raise ValueError("Arm joint limit exceeded")
        return joints

    def forward(self, joints):
        joints = self.joints_array(joints)
        matrix = np.eye(4)
        for origin, axis, angle in zip(self.origins, self.axes, joints):
            rotation = np.eye(4)
            rotation[:3, :3] = Rotation.from_rotvec(axis * angle).as_matrix()
            matrix = matrix @ origin @ rotation
        return matrix

    def observed_base(self, joints, wrist_pose):
        return pose_matrix(wrist_pose) @ np.linalg.inv(self.forward(joints))

    def solve_local(self, joints, wrist_pose, target_pose, max_joint_delta=.15):
        """Return one bounded local solution or fail; does not plan collisions."""
        if not 0 < max_joint_delta <= .25:
            raise ValueError("Invalid local joint bound")
        current = self.joints_array(joints)
        base = self.observed_base(current, wrist_pose)
        target = pose_matrix(target_pose)
        def residual(candidate):
            predicted = base @ self.forward(candidate)
            position = predicted[:3, 3] - target[:3, 3]
            rotation = Rotation.from_matrix(target[:3, :3].T @ predicted[:3, :3]).as_rotvec()
            return np.r_[position, .15 * rotation]
        # Solve increments from exact zero: TRF otherwise initializes a vanishing
        # trust region when physical home joints contain tiny nonzero readings.
        result = least_squares(lambda delta: residual(current+delta), np.zeros(6), bounds=(np.maximum(self.lower-current, -max_joint_delta), np.minimum(self.upper-current, max_joint_delta)), max_nfev=60, ftol=1e-9, xtol=1e-9, gtol=1e-9)
        result.x = current + result.x
        error = residual(result.x)
        position_error = float(np.linalg.norm(error[:3]))
        rotation_error = float(np.linalg.norm(error[3:])/.15)
        if not result.success or position_error > .001 or rotation_error > np.deg2rad(1):
            raise ValueError("No bounded local IK solution: %.6fm %.3fdeg" % (position_error, np.rad2deg(rotation_error)))
        return result.x
