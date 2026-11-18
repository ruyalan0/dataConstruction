"""Native function contracts mapped to the existing, guarded policy actions."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from .action_control import action_interface
from .io_contract import round_output
from .input_contract import strip_schema_titles
from . import push_t_motion

Arm = Literal['left', 'right']
Phase = Literal['fine', 'transit']
Number = Annotated[float, Field(allow_inf_nan=False)]
Vec2 = Annotated[list[Number], Field(min_length=2, max_length=2)]
Vec3 = Annotated[list[Number], Field(min_length=3, max_length=3)]
Quat = Annotated[list[Number], Field(min_length=4, max_length=4)]
Box = Annotated[list[Vec3], Field(min_length=2, max_length=2)]
Steps = Annotated[int, Field(ge=1, le=20)]
JointSteps = Annotated[int, Field(ge=1, le=6)]
Token = Annotated[str, Field(pattern='^[0-9a-f]{64}$')]
Reason = Literal['approach_blocked', 'descent_blocked', 'withdrawal_blocked']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Decision(Contract):
    assessment: str
    plan: str
    phase: Phase
    memory: str


class Transit(Decision):
    phase: Literal['transit']


class Frame(Contract):
    step: Annotated[int, Field(ge=0)]
    camera: Literal['cam_head', 'cam_left_wrist', 'cam_right_wrist']


class Review(Decision):
    frames: Annotated[list[Frame], Field(min_length=1, max_length=6)]


class Point(Contract):
    pixel: Vec2
    z: Number


class Project(Decision):
    points: Annotated[list[Point], Field(min_length=1, max_length=32)]


class MatchedView(Contract):
    camera: Literal['cam_head', 'cam_left_wrist', 'cam_right_wrist']
    pixel: Vec2


class Triangulate(Decision):
    step: Annotated[int, Field(ge=0)]
    feature: Annotated[str, Field(min_length=1, max_length=500)]
    views: Annotated[list[MatchedView], Field(min_length=2, max_length=3)]


class TimedPixel(Contract):
    step: Annotated[int, Field(ge=0)]
    pixel: Vec2


class TemporalTriangulate(Decision):
    step: Annotated[int, Field(ge=0)]
    camera: Literal['cam_left_wrist', 'cam_right_wrist']
    feature: Annotated[str, Field(min_length=1, max_length=500)]
    stationary_target: Literal[True]
    camera_hand_empty: Literal[True]
    stationarity_evidence: Annotated[str, Field(min_length=1, max_length=1000)]
    samples: Annotated[list[TimedPixel], Field(min_length=2, max_length=3)]


class Pose(Contract):
    position: Vec3 | None
    quaternion: Quat | None


class ArmCommand(Pose):
    toward: Pose | None
    gripper: Annotated[float, Field(ge=0, le=1)] | None


class MoveArm(Decision, ArmCommand):
    arm: Arm
    steps: Steps
    settle: Annotated[int, Field(ge=0, le=2)]


class MoveArms(Decision):
    left: ArmCommand | None
    right: ArmCommand | None
    steps: Steps
    settle: Annotated[int, Field(ge=0, le=2)]


class JointArm(Contract):
    control: Literal['joint']
    joints: Annotated[list[Number], Field(min_length=6, max_length=6)] | None
    gripper: Annotated[float, Field(ge=0, le=1)] | None


class TcpArm(Contract):
    control: Literal['tcp']
    toward: Pose | None
    gripper: Annotated[float, Field(ge=0, le=1)] | None


class JointDecision(Contract):
    assessment: str
    plan: str
    memory: str


class JointArms(JointDecision):
    left: JointArm | None
    right: JointArm | None


class PushTArms(JointArms):
    motion_stage: Literal['initial_reach', 'manipulation', 'home_return']


class MixedArms(JointArms):
    left: JointArm | TcpArm | None
    right: JointArm | TcpArm | None


class Stop(Decision):
    reason: str


class Posture(Transit):
    left: Literal['upright'] | None
    right: Literal['upright'] | None
    steps: JointSteps


class Recovery(Transit):
    arm: Arm
    reason: Reason
    clearance_box: Box
    max_steps: Annotated[int, Field(ge=1, le=40)]


Delta = Annotated[float, Field(ge=-.6, le=.6)]


class Joints(Contract):
    joint1: Delta | None
    joint2: Delta | None
    joint3: Delta | None
    joint4: Delta | None
    joint5: Delta | None
    joint6: Delta | None


class PreviewDelta(Transit):
    left: Joints | None
    right: Joints | None
    steps: JointSteps


class ExecuteDelta(PreviewDelta):
    preview_id: Token


class PreviewUnfold(Recovery):
    max_steps: Annotated[int, Field(ge=1, le=24)]


class ExecuteUnfold(PreviewUnfold):
    preview_id: Token


# Descriptions document the same physical limits enforced by the existing executor.
BASE_TOOLS = {
    'review_frames': (Review, 'Read 1–6 recorded RGB frames and public poses from this episode. Zero motion; no paths or future steps.'),
    'project_pixels': (Project, 'Project head-camera pixels onto an inferred world-z plane. Zero motion; not object truth.'),
    'triangulate_point': (Triangulate, 'Locate ONE identical visible physical feature in 2–3 distinct CURRENT camera images at the supplied current step. Give raw 640x480 pixel coordinates, e.g. the same corner in each view, not independent silhouette centers. Returns XYZ, reprojection errors and conditional pixel-noise sensitivity, or rejects inconsistent/poor views. Zero motion. Height is estimated, not supplied; result is not a grasp/contact target.'),
    'triangulate_temporal_point': (TemporalTriangulate, 'Zero-motion depth from ONE wrist camera at 2–3 increasing steps, ending at current step, spanning at most 24 native steps. First review_frames for ALL samples so their named RGB is in this request. Match the same static uncontacted feature in raw 640x480 pixels and explain evidence for stationarity and an empty camera hand. Uses actual recorded poses, not requested TCP offsets. Camera hand must remain open, other arm stationary, both grippers unchanged throughout. Geometry checks cannot prove target stationarity; result is conditional. Use ordinary bounded TCP moves separately to obtain sufficient parallax.'),
    'move_arm': (MoveArm, 'Execute one bounded single-arm segment. Nullable pose/gripper fields hold their current values. Use either toward or direct pose, never both. Existing fine/transit and tracking guards apply.'),
    'move_arms': (MoveArms, 'Execute a simultaneous bounded two-arm segment. A null arm holds; supply at least one arm. Each arm uses toward OR direct pose. Existing guards stop the whole segment.'),
    'verify_completion': (Decision, 'Advance six hold steps, then inspect the native termination state. Use after satisfying the full task and required arm return; not a success declaration.'),
    'stop_episode': (Stop, 'Stop this controller and preserve the scene when blocked. This is an incomplete attempt, not native success.'),
}
OPTIONAL_TOOLS = {
    'direct_posture_action': {
        'set_posture': (Posture, 'Bounded shoulder adjustment, selected empty/open hands only. Null arm holds; at least one upright arm required. Changes wrist pose; inspect swept space first.'),
    },
    'posture_recovery_program': {
        'recover_posture': (Recovery, 'Bounded empty-handed shoulder recovery within an RGB-supported clear box. Use only for an obstructed next motion.'),
    },
    'joint_control': {
        'preview_joint_delta': (PreviewDelta, 'Zero-motion FK preview of selected joints/arms. Null joints hold. Select at least one joint. Empty/open selected hands and transit required.'),
        'execute_joint_delta': (ExecuteDelta, 'Execute the exact latest joint preview: same non-null joints, steps, scene step and preview_id. Never invent a token. Existing empty-hand, joint and sweep guards apply.'),
        'preview_unfold_arm': (PreviewUnfold, 'Zero-motion preview of bounded unfolding within a declared clear box. Public robot geometry only; no contact simulation.'),
        'execute_unfold_arm': (ExecuteUnfold, 'Execute the exact latest unfolding spec and preview_id, at the unchanged scene step. Existing envelope and empty-hand guards apply.'),
    },
}


class ToolRegistry:
    def __init__(self, config):
        self.action_interface = action_interface(config)
        self.contracts = dict(BASE_TOOLS)
        if self.action_interface == 'joint':
            del self.contracts['move_arm'], self.contracts['move_arms']
        if self.action_interface in ('joint', 'both'):
            self.contracts['control_arms'] = (
                JointArms if self.action_interface == 'joint' else MixedArms,
                'Set simultaneous absolute joint goals (radians) and gripper openings (0..1). '
                'The controller generates synchronized bounded reference segments and returns measured outcome; '
                'a segment return can leave the goal unfinished. No model-specified duration. '
                'Use null for holds. Invalid goals are rejected before either arm moves; inspect last_motion.pose_validation. '
                'To observe, move only an empty idle arm and keep the working arm null; inspect its wrist RGB. '
                'Gripper settling does not establish object retention.')
        if self.action_interface == 'joint' and push_t_motion.enabled(config.get('task'), config.get('interpolation_profile')):
            self.contracts['control_arms'] = (PushTArms, self.contracts['control_arms'][1] +
                ' For push_T, select motion_stage: initial_reach for each arm first empty-hand '
                'free-space approach ending before contact, manipulation for final contact approach, '
                'all pushes, unloading, withdrawal and later approaches, or home_return only after '
                'verified complete separation from the T. Closed empty fingers are allowed; '
                'gripper opening does not establish separation. Initial reach and home use '
                'velocity-limited linear references; manipulation uses quintic. All segments still '
                'stop and use the same measured settling checks. See observation.motion_policy for '
                'the measured-state linear home-return time and call budget.')
        model, description = self.contracts['triangulate_temporal_point']
        self.contracts['triangulate_temporal_point'] = (model, description.replace(
            'ordinary bounded TCP moves', 'bounded ' + {'joint':'joint','tcp':'TCP','both':'joint or TCP'}[self.action_interface] + ' observation moves'))
        for flag, contracts in OPTIONAL_TOOLS.items():
            enabled = config.get(flag, False)
            if type(enabled) is not bool:
                raise ValueError(f'{flag} must be boolean')
            if enabled and self.action_interface == 'both':
                self.contracts.update(contracts)
        self.definitions = [dict(type='function', name=name, description=description,
                                 strict=True, parameters=strip_schema_titles(model.model_json_schema()))
                            for name, (model, description) in self.contracts.items()]

    def decision(self, name, arguments):
        if name not in self.contracts:
            raise ValueError(f'Tool is not enabled: {name}')
        # Validate locally even when the upstream advertises strict generation.
        data = self.contracts[name][0].model_validate_json(arguments).model_dump(exclude_none=True)
        if name == 'control_arms':
            data = round_output(data)
            keys = ('assessment', 'plan', 'memory')
        else:
            keys = ('assessment', 'plan', 'phase', 'memory')
        decision = {key: data.pop(key) for key in keys}
        if name == 'review_frames':
            action = {'review': data['frames']}
        elif name == 'project_pixels':
            action = {'project': data['points']}
        elif name == 'triangulate_point':
            action = {'triangulate': data}
        elif name == 'triangulate_temporal_point':
            action = {'triangulate_temporal': data}
        elif name == 'control_arms':
            arms = {arm: data.pop(arm) for arm in ('left', 'right') if arm in data}
            action = {'control_arms': {'arms': arms, **data}}
        elif name == 'move_arm':
            action = data
        elif name in ('move_arms', 'set_posture', 'preview_joint_delta', 'execute_joint_delta'):
            arms = {arm: data.pop(arm) for arm in ('left', 'right') if arm in data}
            if not arms or any(isinstance(value, dict) and not value for value in arms.values()):
                raise ValueError('Select a nonempty arm/joint operation')
            key = {'move_arms': 'arms', 'set_posture': 'posture',
                   'preview_joint_delta': 'joint_delta', 'execute_joint_delta': 'joint_delta'}[name]
            action = {key: arms, **data}
            if name == 'preview_joint_delta':
                action = {'preview_joint_delta': action}
        elif name == 'verify_completion':
            action = {'verify': True}
        elif name == 'stop_episode':
            action = {'stop': True, 'reason': data['reason']}
        elif name == 'recover_posture':
            action = {'recover_posture': data}
        elif name == 'preview_unfold_arm':
            action = {'preview_unfold_arm': data}
        elif name == 'execute_unfold_arm':
            token = data.pop('preview_id')
            action = {'unfold_arm': data, 'preview_id': token}
        else:
            raise AssertionError('Enabled tool has no action mapping')
        decision['action'] = action
        return decision
