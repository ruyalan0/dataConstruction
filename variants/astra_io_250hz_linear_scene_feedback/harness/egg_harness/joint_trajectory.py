"""Synchronized, bounded joint references for one model-selected goal.

Only the controller chooses time. A long goal is shortened along the same joint
line so the reference can accelerate and decelerate before the next observation.
These bounds describe references, not measured actuator velocity or contact force.
"""
from dataclasses import dataclass
import math

import numpy as np

from .clocks import PHYSICS_HZ

ARMS = ('left', 'right')
# Maxima of the first and second derivatives of 10u^3 - 15u^4 + 6u^5.
PEAK_SPEED = 1.875
PEAK_ACCELERATION = 10 / math.sqrt(3)


@dataclass(frozen=True)
class JointTrajectory:
    starts: dict
    targets: dict
    duration_ticks: int
    goal_fraction: float

    @classmethod
    def from_plan(cls, plan, settings):
        starts = {arm: plan[arm]['start'].copy() for arm in ARMS}
        deltas = {arm: plan[arm]['target'] - starts[arm] for arm in ARMS}
        distance = max(float(np.max(np.abs(delta))) for delta in deltas.values())
        # Leave one measured settling window after the reference stops.
        available_ticks = (math.floor(settings['max_motion_seconds'] * PHYSICS_HZ + 1e-9)
                           - math.ceil(settings['settle_window_seconds'] * PHYSICS_HZ))
        available_seconds = available_ticks / PHYSICS_HZ
        velocity = settings['joint_velocity_limit_rad_s']
        acceleration = settings['joint_acceleration_limit_rad_s2']
        capacity = min(velocity * available_seconds / PEAK_SPEED,
                       acceleration * available_seconds ** 2 / PEAK_ACCELERATION)
        fraction = min(1., capacity / distance) if distance else 1.
        segment_distance = distance * fraction
        seconds = max(PEAK_SPEED * segment_distance / velocity,
                      math.sqrt(PEAK_ACCELERATION * segment_distance / acceleration))
        ticks = min(available_ticks, max(0, math.ceil(seconds * PHYSICS_HZ - 1e-9)))
        targets = {arm: starts[arm] + fraction * deltas[arm] for arm in ARMS}
        return cls(starts, targets, ticks, fraction)

    def positions(self, tick):
        u = min(max(tick / self.duration_ticks, 0.), 1.) if self.duration_ticks else 1.
        progress = u ** 3 * (10 + u * (-15 + 6 * u))
        return {arm: self.starts[arm] + progress * (self.targets[arm] - self.starts[arm]) for arm in ARMS}

    def record(self):
        return dict(profile='synchronized_quintic', duration_seconds=self.duration_ticks / PHYSICS_HZ,
                    goal_fraction=self.goal_fraction,
                    segment_joint_targets={arm: self.targets[arm].tolist() for arm in ARMS})
