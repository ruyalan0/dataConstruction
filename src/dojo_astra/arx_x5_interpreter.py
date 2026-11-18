from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.spatial.transform import Rotation

from .observation_adapter import ARMS, Observation


class ArmAction(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    operation: Literal["hold", "hold_current", "hold_latched", "latch", "translate", "rotate"]
    frame: Literal["world", "tool"]
    axis: Literal["+x", "-x", "+y", "-y", "+z", "-z"]
    amount: float = Field(
        ge=0,
        le=30,
        description="translate: millimeters, 0 < amount <= 30; rotate: degrees, 0 < amount <= 15; hold: exactly 0",
    )
    gripper: Literal["hold", "open", "close"]

    @model_validator(mode="after")
    def valid_amount(self):
        if self.operation in {'hold', 'hold_current', 'hold_latched', 'latch'} and self.amount != 0:
            raise ValueError("hold requires zero amount")
        if self.operation in {'translate', 'rotate'} and self.amount == 0:
            raise ValueError("motion requires positive amount")
        if self.operation == "rotate" and self.amount > 15:
            raise ValueError("rotation limited to 15 degrees per decision")
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: ArmAction
    right: ArmAction
    steps: int = Field(ge=1, le=8, strict=True)
    note: str = Field(max_length=2000)


def rotation(pose):
    return Rotation.from_quat(pose[3:], scalar_first=True)


def interpret(decision: Decision, obs: Observation, latched=None):
    targets = {}
    for arm in ARMS:
        command = getattr(decision, arm)
        pose = obs.poses[arm].copy()
        if command.operation == 'hold_latched':
            if latched is None:
                raise ValueError('A latched reference is required')
            pose = latched[arm].copy()
        rot = rotation(pose)
        axis = np.eye(3)["xyz".index(command.axis[1])] * (1 if command.axis[0] == "+" else -1)
        if command.operation == "translate":
            direction = rot.apply(axis) if command.frame == "tool" else axis
            pose[:3] += direction * command.amount / 1000
        elif command.operation == "rotate":
            delta = Rotation.from_rotvec(axis * np.deg2rad(command.amount))
            rot = rot * delta if command.frame == "tool" else delta * rot
            pose[3:] = rot.as_quat(scalar_first=True)
        targets[f"{arm}_ee_pose"] = pose
        grip = obs.gripper_commands[arm] if command.gripper == "hold" else float(command.gripper == "open")
        targets[f"{arm}_ee_joint_state"] = np.array([grip])
    return targets
