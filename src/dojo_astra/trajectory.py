"""Bounded model-generated EE waypoints sampled at native observation boundaries."""

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.spatial.transform import Rotation, Slerp

from .arx_x5_interpreter import rotation
from .evidence_tools import ToolInputError
from .observation_adapter import ARMS


class PoseDelta(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    translation_mm: tuple[float, float, float]
    rotation_vector_deg: tuple[float, float, float]
    frame: Literal['world', 'tool']
    reference: Literal['current', 'latched'] = 'current'
    gripper: Literal['hold', 'open', 'close']

    @model_validator(mode='after')
    def bounded_delta(self):
        if np.linalg.norm(self.translation_mm) > 30 + 1e-9:
            raise ValueError('Translation vector norm exceeds 30 mm')
        if np.linalg.norm(self.rotation_vector_deg) > 15 + 1e-9:
            raise ValueError('Rotation vector norm exceeds 15 degrees')
        return self


class Waypoint(BaseModel):
    model_config = ConfigDict(extra='forbid')
    left: PoseDelta
    right: PoseDelta


class Trajectory(BaseModel):
    model_config = ConfigDict(extra='forbid')
    waypoints: list[Waypoint] = Field(min_length=1, max_length=4)
    motion_steps: int = Field(ge=1, le=8, strict=True)
    settle_steps: int = Field(ge=0, le=7, strict=True)
    note: str = Field(max_length=2000)

    @model_validator(mode='after')
    def bounded_duration(self):
        if self.motion_steps < len(self.waypoints) or self.motion_steps + self.settle_steps > 8:
            raise ValueError('Visit each waypoint; motion_steps + settle_steps must be <= 8')
        return self


def plan_trajectory(command, obs, latched):
    targets, previous = [], {arm: obs.poses[arm].copy() for arm in ARMS}
    grips = dict(obs.gripper_commands)
    distance, angle = dict.fromkeys(ARMS, 0.0), dict.fromkeys(ARMS, 0.0)
    count = len(command.waypoints)
    for index, waypoint in enumerate(command.waypoints):
        endpoints = {}
        for arm in ARMS:
            move = getattr(waypoint, arm)
            base = obs.poses[arm] if move.reference == 'current' else latched[arm]
            pose, rot = base.copy(), rotation(base)
            delta = np.asarray(move.translation_mm) / 1000
            pose[:3] += rot.apply(delta) if move.frame == 'tool' else delta
            turn = Rotation.from_rotvec(np.deg2rad(move.rotation_vector_deg))
            pose[3:] = (rot * turn if move.frame == 'tool' else turn * rot).as_quat(scalar_first=True)
            distance[arm] += float(np.linalg.norm(pose[:3] - previous[arm][:3]) * 1000)
            angle[arm] += float(np.rad2deg((rotation(previous[arm]).inv() * rotation(pose)).magnitude()))
            if distance[arm] > 30 + 1e-6 or angle[arm] > 15 + 1e-6:
                raise ToolInputError('Total per-arm waypoint path exceeds 30 mm / 15 degrees')
            endpoints[arm] = pose
            if move.gripper != 'hold':
                grips[arm] = float(move.gripper == 'open')
        segment_steps = command.motion_steps // count + int(index < command.motion_steps % count)
        for step in range(1, segment_steps + 1):
            fraction = step / segment_steps
            target = {}
            for arm in ARMS:
                a, b = previous[arm], endpoints[arm]
                pose = np.empty(7)
                pose[:3] = a[:3] + fraction * (b[:3] - a[:3])
                pose[3:] = Slerp([0, 1], Rotation.from_quat([a[3:], b[3:]], scalar_first=True))(
                    fraction).as_quat(scalar_first=True)
                target[f'{arm}_ee_pose'] = pose
                target[f'{arm}_ee_joint_state'] = np.array([grips[arm]])
            targets.append(target)
        previous = endpoints
    targets.extend([{k: v.copy() for k, v in targets[-1].items()} for _ in range(command.settle_steps)])
    return targets
