"""Three explicit reference generators; native calls RoboDojo's own function."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import inspect
import math
import os
from pathlib import Path

import numpy as np

PROFILES = ('native', 'linear', 'quintic')
PROFILE_NAMES = {'native': 'robodojo_native_80_20', 'linear': 'linear_auto_duration',
                 'quintic': 'synchronized_quintic'}


def selected_profile():
    value = os.environ.get('ASTRA_INTERPOLATION_PROFILE', 'quintic')
    if value not in PROFILES:
        raise ValueError('Unknown interpolation profile: ' + value)
    return value


@dataclass
class LinearTrajectory:
    starts: dict
    targets: dict
    duration_ticks: int
    goal_fraction: float

    def positions(self, tick):
        progress = min(max(tick / self.duration_ticks, 0.), 1.) if self.duration_ticks else 1.
        return {arm: start + progress * (self.targets[arm] - start)
                for arm, start in self.starts.items()}

    def record(self):
        return dict(profile='linear_auto_duration', duration_seconds=self.duration_ticks / 250,
                    goal_fraction=self.goal_fraction,
                    segment_joint_targets={arm: target.tolist() for arm, target in self.targets.items()})


class NativeTrajectory(LinearTrajectory):
    frames = None
    source = None

    def bind_native(self, env, final_frame):
        """Generate native target frames without prematurely consuming support motion."""
        original_support = env.support_arm_action[0] if hasattr(env, 'support_arm_action') else None
        if original_support is not None:
            env.support_arm_action[0] = deepcopy(original_support)
        try:
            frames = env.process_control_info(deepcopy(final_frame), env_idx=0)
        finally:
            if original_support is not None:
                env.support_arm_action[0] = original_support
        expected = int(env.obs_manager.collect_interval)
        if len(frames) != expected or expected != 10:
            raise ValueError('Native comparison requires ten original 4ms frames')
        self.frames = [{key: deepcopy(frame[key]) for key in final_frame} for frame in frames]
        filename = Path(inspect.getsourcefile(env.process_control_info))
        self.source = dict(function=env.process_control_info.__qualname__, source_file=str(filename),
                           source_sha256=hashlib.sha256(filename.read_bytes()).hexdigest(),
                           frame_count=len(frames), native_function_calls=1,
                           native_collect_interval=env.obs_manager.collect_interval,
                           support_consumed_during_generation=False)

    def frame(self, tick):
        if self.frames is None:
            raise RuntimeError('Native trajectory has not been generated')
        return self.frames[min(max(tick - 1, 0), len(self.frames) - 1)]

    def positions(self, tick):
        frame = self.frame(tick)
        return {arm: np.asarray(frame[arm + '_arm_joint_state']['position'], dtype=float)
                for arm in self.starts}

    def gripper_frames(self, tick):
        return {key: value for key, value in self.frame(tick).items() if key.endswith('_ee_joint_state')}

    def record(self):
        return dict(profile='robodojo_native_80_20', duration_seconds=.04, goal_fraction=1.,
                    segment_joint_targets={arm: target.tolist() for arm, target in self.targets.items()})


def make_trajectory(plan, settings, profile=None):
    from .joint_trajectory import JointTrajectory
    profile = selected_profile() if profile is None else profile
    if profile not in PROFILES:
        raise ValueError('Unknown interpolation profile: ' + str(profile))
    if profile == 'quintic':
        return JointTrajectory.from_plan(plan, settings)
    starts = {arm: item['start'].copy() for arm, item in plan.items()}
    targets = {arm: item['target'].copy() for arm, item in plan.items()}
    if profile == 'native':
        return NativeTrajectory(starts, targets, 10, 1.)
    distance = max(float(np.max(np.abs(targets[arm] - starts[arm]))) for arm in starts)
    available = (math.floor(settings['max_motion_seconds'] * 250 + 1e-9)
                 - math.ceil(settings['settle_window_seconds'] * 250))
    velocity = settings['joint_velocity_limit_rad_s']
    fraction = min(1., velocity * available / 250 / distance) if distance else 1.
    ticks = min(available, max(0, math.ceil(distance * fraction / velocity * 250 - 1e-9)))
    targets = {arm: starts[arm] + fraction * (target - starts[arm]) for arm, target in targets.items()}
    return LinearTrajectory(starts, targets, ticks, fraction)
