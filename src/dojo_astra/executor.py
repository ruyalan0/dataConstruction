import numpy as np

from .arx_x5_interpreter import interpret, rotation
from .evidence_tools import ToolInputError
from .observation_adapter import ARMS
from .trajectory import Trajectory, plan_trajectory


class Executor:
    def __init__(self, detailed_feedback=False, stall_boundaries=3):
        self.detailed_feedback = detailed_feedback
        self.consecutive_not_reached = 0
        self.obs = None
        self.target = None
        self.start = None
        self.remaining = 0
        self.steps = 0
        self.pending = False
        self.feedback = None
        self.latched = None
        self.targets = []
        self.stall_boundaries = stall_boundaries
        self.low_progress = dict.fromkeys(ARMS, 0)

    def observe(self, obs, terminated=False):
        if self.obs is not None and obs.sequence <= self.obs.sequence:
            raise ValueError("Stale observation")
        self.obs = obs
        if self.latched is None:
            self.latched = {arm: obs.poses[arm].copy() for arm in ARMS}
        if not self.pending:
            return
        self.pending = False
        errors, angles, displacement, progress = {}, {}, {}, {}
        for arm in ARMS:
            target = self.target[f"{arm}_ee_pose"]
            errors[arm] = float(np.linalg.norm(obs.poses[arm][:3] - target[:3]) * 1000)
            angles[arm] = float(np.rad2deg((rotation(target).inv() * rotation(obs.poses[arm])).magnitude()))
            displacement[arm] = ((obs.poses[arm][:3] - self.start.poses[arm][:3]) * 1000).tolist()
            before = self.before_action.poses[arm]
            requested = (target[:3] - before[:3]) * 1000
            actual = (obs.poses[arm][:3] - before[:3]) * 1000
            length = float(np.linalg.norm(requested))
            along = float(np.dot(actual, requested / length)) if length > 1e-9 else 0.0
            before_angle = float(np.rad2deg((rotation(target).inv() * rotation(before)).magnitude()))
            reduction = length - errors[arm]
            angle_reduction = before_angle - angles[arm]
            needs_motion = errors[arm] > 2 or angles[arm] > 2
            low = needs_motion and reduction < 0.25 and angle_reduction < 0.25
            self.low_progress[arm] = self.low_progress[arm] + 1 if low else 0
            progress[arm] = {'along_requested_direction_mm': along,
                             'lateral_displacement_mm': float(np.linalg.norm(actual - along * requested / length))
                             if length > 1e-9 else float(np.linalg.norm(actual)),
                             'position_error_reduction_mm': reduction,
                             'rotation_error_reduction_deg': angle_reduction,
                             'consecutive_low_progress_boundaries': self.low_progress[arm]}
        reached = max(errors.values()) <= 2 and max(angles.values()) <= 2
        gross_error = max(errors.values()) > 60 or max(angles.values()) > 30
        stalled = max(self.low_progress.values()) >= self.stall_boundaries and self.remaining > 0
        interrupted = (gross_error or stalled) and not terminated
        # Gripper state is a command echo: pose reached does not prove a grasp or release.
        if interrupted or self.remaining == 0 or terminated:
            self.remaining = 0
        self.feedback = {
            "steps": self.steps,
            "pose_reached": reached,
            "interrupted": interrupted,
            "remaining_steps": self.remaining,
            "error_mm": errors,
            "error_deg": angles,
            "displacement_mm": displacement,
            "before": self.start.sequence,
            "after": obs.sequence,
            "semantic_status": "unconfirmed",
            'progress': progress,
            'interruption_reason': ('tracking_deviation' if gross_error else 'sustained_low_progress')
            if interrupted else None,
            'terminated_by_environment': terminated,
            'phase': 'motion' if self.steps <= self.motion_steps else 'settle',
            'review_checkpoint': 'after_settle_or_tracking_interrupt',
        }

        if self.remaining == 0:
            self.consecutive_not_reached = 0 if reached else self.consecutive_not_reached + 1
        if self.detailed_feedback:
            self.feedback.update(
                consecutive_completed_segments_not_reached=self.consecutive_not_reached,
                requested_target_poses={a: self.target[f"{a}_ee_pose"].tolist() for a in ARMS},
                observed_poses={a: obs.poses[a].tolist() for a in ARMS},
                segment_remaining_native_calls=self.remaining,
                evidence_scope="robot_pose_tracking_only; contact_and_object_state_unknown",
            )

    def plan(self, decision):
        if self.obs is None or self.pending or self.remaining:
            raise RuntimeError("Cannot replace an outstanding action")
        if isinstance(decision, Trajectory):
            return plan_trajectory(decision, self.obs, self.latched)
        target = interpret(decision, self.obs, self.latched)
        for arm in ARMS:
            if getattr(decision, arm).operation == 'hold_latched':
                position = np.linalg.norm(target[f'{arm}_ee_pose'][:3] - self.obs.poses[arm][:3]) * 1000
                angle = np.rad2deg((rotation(target[f'{arm}_ee_pose']).inv() * rotation(self.obs.poses[arm])).magnitude())
                if position > 30 + 1e-6 or angle > 15 + 1e-6:
                    raise ToolInputError('Latched reference too far; use bounded motions to return')
        return [{k: v.copy() for k, v in target.items()} for _ in range(decision.steps)]

    def load(self, decision, plan=None):
        targets = self.plan(decision) if plan is None else plan
        if self.pending or self.remaining:
            raise RuntimeError('Cannot replace an outstanding action')
        self.targets = targets
        self.target = targets[-1]
        self.start = self.obs
        self.remaining = len(targets)
        self.steps = 0
        self.low_progress = dict.fromkeys(ARMS, 0)
        self.motion_steps = decision.motion_steps if isinstance(decision, Trajectory) else decision.steps
        if not isinstance(decision, Trajectory):
            for arm in ARMS:
                if getattr(decision, arm).operation == 'latch':
                    self.latched[arm] = self.obs.poses[arm].copy()

    def next_action(self):
        if self.pending or not self.remaining:
            raise RuntimeError("Fresh observation and decision required")
        self.pending = True
        self.before_action = self.obs
        self.target = self.targets[self.steps]
        self.remaining -= 1
        self.steps += 1
        # Follow fixed absolute samples; never accumulate unexecuted increments.
        return {key: value.copy() for key, value in self.target.items()}

    def finish(self):
        if self.pending or self.obs is None:
            raise RuntimeError("Terminal observation required before finishing")
        if self.remaining:
            self.remaining = 0
            self.feedback = dict(self.feedback, remaining_steps=0, terminated_by_environment=True)
            if self.detailed_feedback:
                self.feedback['segment_remaining_native_calls'] = 0
