from dataclasses import dataclass

import numpy as np

ARMS = ("left", "right")


def vector(value, size):
    out = np.asarray(value, dtype=float)
    if out.shape != (size,) or not np.isfinite(out).all():
        raise ValueError(f"Expected finite vector of length {size}")
    return out.copy()


@dataclass(frozen=True)
class Observation:
    sequence: int
    instruction: str
    poses: dict
    gripper_commands: dict
    images: dict


def adapt(raw, sequence):
    if raw["data_format_version"] != "v1.0":
        raise ValueError("Unsupported observation version")
    if not isinstance(raw["instruction"], str) or not raw["instruction"].strip():
        raise ValueError("Missing task instruction")
    poses, grippers, images = {}, {}, {}
    for arm in ARMS:
        poses[arm] = vector(raw["state"][f"{arm}_ee_pose"], 7)
        if not np.isclose(np.linalg.norm(poses[arm][3:]), 1, atol=0.002):
            raise ValueError("Invalid observed quaternion")
        grippers[arm] = float(vector(raw["state"][f"{arm}_ee_joint_state"], 1)[0])
        if not 0 <= grippers[arm] <= 1:
            raise ValueError("Invalid normalized gripper command")
    for name, camera in raw["vision"].items():
        image = np.asarray(camera["color"])
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise ValueError("Expected decoded uint8 RGB")
        images[name] = image.copy()
    if not images:
        raise ValueError("No official RGB cameras")
    # Never copy raw action, metadata, depth, calibration, or simulator truth.
    return Observation(sequence, raw["instruction"], poses, grippers, images)
