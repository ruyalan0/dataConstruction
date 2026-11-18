"""Allowlisted robot IPC. Evaluator results are never read here."""
import json
import time
import uuid
import numpy as np
from pathlib import Path


def validate_joint_feedback(obs, op, kwargs, previous_step):
    if 'joint_rejection' in obs:
        result = obs['joint_rejection']
        if op not in {'joint_delta', 'unfold_arm'} or 'joint_control' in obs or 'joint_preview' in obs:
            raise ValueError('Unexpected joint rejection operation')
        if not isinstance(result, dict) or set(result) != {'status', 'reason', 'executed_steps', 'next_motion_verified'}:
            raise ValueError('Unexpected joint rejection fields')
        if result['status'] != 'rejected_before_motion' or not isinstance(result['reason'], str) or not result['reason']:
            raise ValueError('Invalid joint rejection reason/status')
        if type(result['executed_steps']) is not int or result['executed_steps'] != 0 or result['next_motion_verified'] is not False or obs['step'] != previous_step:
            raise ValueError('Joint rejection must preserve the native step count')
        return
    preview = op.startswith('preview_')
    primitive = op.removeprefix('preview_')
    result = obs['joint_preview' if preview else 'joint_control']
    common = {'preview_id', 'primitive', 'planned_steps', 'executed_steps', 'next_motion_verified'}
    expected = common | ({'plan'} if preview else {'status', 'target_reached', 'instruction', 'tracking'})
    if not isinstance(result, dict) or set(result) != expected:
        raise ValueError('Unexpected joint feedback fields')
    token = result['preview_id']
    if not isinstance(token, str) or len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid joint preview identifier')
    if result['primitive'] != primitive or result['next_motion_verified'] is not False:
        raise ValueError('Invalid joint primitive or verification claim')
    limit = kwargs['steps'] if primitive == 'joint_delta' else kwargs['unfold_arm']['max_steps']
    planned, executed = result['planned_steps'], result['executed_steps']
    if type(planned) is not int or not 0 <= planned <= limit or type(executed) is not int or not 0 <= executed <= planned:
        raise ValueError('Invalid joint execution count')
    if obs['step'] - previous_step != executed:
        raise ValueError('Joint feedback disagrees with native step count')
    if preview:
        if executed != 0:
            raise ValueError('Joint preview executed a native action')
        plan = result['plan']
        fields = {'targets', 'path', 'planned_steps', 'next_motion_verified', 'scope'}
        fields |= ({'remaining_delta_rad'} if primitive == 'joint_delta' else
                   {'geometry_score_gain', 'candidate_target_rad', 'target_reachable_within_budget'})
        if not isinstance(plan, dict) or set(plan) != fields or plan['next_motion_verified'] is not False:
            raise ValueError('Unexpected joint plan fields')
        if plan['planned_steps'] != planned or not isinstance(plan['path'], list) or len(plan['path']) != planned:
            raise ValueError('Invalid joint path length')
        selected = set(kwargs['joint_delta']) if primitive == 'joint_delta' else {kwargs['unfold_arm']['arm']}
        def vector(value, size, limit=None):
            array = np.asarray(value, dtype=float)
            if array.shape != (size,) or not np.isfinite(array).all() or (limit is not None and np.max(np.abs(array)) > limit):
                raise ValueError('Invalid joint plan vector')
            return array
        if not isinstance(plan['scope'], str) or not isinstance(plan['targets'], dict) or set(plan['targets']) != selected:
            raise ValueError('Unexpected joint plan targets')
        for value in plan['targets'].values(): vector(value, 6)
        if primitive == 'joint_delta':
            if not isinstance(plan['remaining_delta_rad'], dict) or set(plan['remaining_delta_rad']) != selected:
                raise ValueError('Unexpected joint remainder arms')
            for value in plan['remaining_delta_rad'].values(): vector(value, 6)
        else:
            vector(plan['candidate_target_rad'], 6)
            if type(plan['target_reachable_within_budget']) is not bool or not isinstance(plan['geometry_score_gain'], (int, float)) or not np.isfinite(plan['geometry_score_gain']):
                raise ValueError('Invalid unfold plan score')
        for frame in plan['path']:
            if not isinstance(frame, dict) or set(frame) != {'joints', 'poses', 'geometry', 'bounds'}:
                raise ValueError('Unexpected joint path frame')
            for field, size in (('joints', 6), ('poses', 7)):
                if set(frame[field]) != {'left', 'right'}:
                    raise ValueError('Joint path must include both arms')
                for values in frame[field].values():
                    array = np.asarray(values, dtype=float)
                    if array.shape != (size,) or not np.isfinite(array).all():
                        raise ValueError('Invalid joint path coordinates')
            if set(frame['geometry']) != {'left', 'right'} or set(frame['bounds']) != {'arms', 'max_joint_delta_rad', 'max_swept_bound_m'}:
                raise ValueError('Unexpected joint path geometry/bounds')
            for geometry in frame['geometry'].values():
                if set(geometry) != {'origins_world_m', 'elbow_world_m', 'wrist_world_m', 'finger_proxy_world_m'}:
                    raise ValueError('Unexpected public joint geometry')
                for name, values in geometry.items():
                    array = np.asarray(values, dtype=float)
                    if array.shape != ((6, 3) if name == 'origins_world_m' else (3,)) or not np.isfinite(array).all():
                        raise ValueError('Invalid public geometry coordinates')
            bounds = frame['bounds']
            if bounds['max_joint_delta_rad'] != .15 or bounds['max_swept_bound_m'] != .024 or set(bounds['arms']) != {'left', 'right'}:
                raise ValueError('Invalid joint motion bounds')
            for bound in bounds['arms'].values():
                if set(bound) != {'delta_rad', 'per_point_swept_bound_m', 'swept_bound_m', 'remaining_delta_rad'}:
                    raise ValueError('Unexpected joint bound fields')
                if not 0 <= bound['swept_bound_m'] <= .024000001:
                    raise ValueError('Exceeded joint sweep bound')
                vector(bound['delta_rad'], 6, .150000001)
                vector(bound['remaining_delta_rad'], 6)
                points = vector(bound['per_point_swept_bound_m'], 7, .024000001)
                if np.any(points < 0) or abs(float(points.max()) - bound['swept_bound_m']) > 1e-9:
                    raise ValueError('Invalid per-point swept bounds')
    else:
        if token != kwargs.get('preview_id'):
            raise ValueError('Joint feedback belongs to another preview')
        if result['status'] not in {'budget_exhausted', 'target_reached', 'episode_ended', 'clearance_envelope_exit', 'tracking_or_gripper_change'}:
            raise ValueError('Unknown joint execution status')
        if type(result['target_reached']) is not bool or not isinstance(result['instruction'], str) or not isinstance(result['tracking'], list):
            raise ValueError('Invalid joint execution feedback')
        for row in result['tracking']:
            if not isinstance(row, dict) or set(row) != {'arm', 'step', 'joint_error_rad', 'wrist_error_m', 'opening_change_m', 'stopped'}:
                raise ValueError('Unexpected joint tracking fields')
            if row['arm'] not in {'left', 'right'} or type(row['step']) is not int or type(row['stopped']) is not bool:
                raise ValueError('Invalid joint tracking identity')
            if not previous_step <= row['step'] <= obs['step'] or any(not np.isfinite(row[key]) or row[key] < 0 for key in ('joint_error_rad', 'wrist_error_m', 'opening_change_m')):
                raise ValueError('Invalid joint tracking measurement')


class Robot:
    def __init__(self, episode, trace):
        self.episode = Path(episode)
        self.trace = Path(trace)
        self.counter = 0
        self.session = uuid.uuid4().hex[:12]
        self.active_arm = 'right'
        self.obs = self.command('observe')
        initial = json.loads((self.episode/'initial-state.json').read_text())
        self.home = {a:initial[a+'_ee_pose'] for a in ('left', 'right')}
        self.home_joints = {a:initial[a+'_arm_joint_state'] for a in ('left', 'right')}

    def command(self, op, **kwargs):
        self.counter += 1
        command = dict(op=op, id=f'policy-{self.session}-{self.counter:05d}', **kwargs)
        path = self.episode/'commands'/(command['id']+'.json')
        if path.exists():
            raise FileExistsError(path)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(command)); tmp.replace(path)
        response = self.episode/'responses'/path.name
        start = time.monotonic()
        while not response.exists():
            if (self.episode/'error.txt').exists():
                raise RuntimeError('Robot service failed; see runner logs')
            if time.monotonic()-start > 240:
                raise TimeoutError(command['id'])
            time.sleep(.2)
        obs = json.loads(response.read_text())
        expected_fields = {'step', 'state', 'cameras', 'instruction', 'ended', 'budget', 'control_timing'}
        from .control_timing import validate_timing_record
        validate_timing_record(obs.get('control_timing'))
        if 'action_interface' in obs:
            from .action_control import action_interface
            action_interface(obs)
            expected_fields.add('action_interface')
        if op == 'control_action':
            expected_fields.add('action_feedback')
        # Older held episodes remain readable, but cannot certify triangulation.
        if 'camera_calibration_id' in obs:
            certificate = obs['camera_calibration_id']
            if not isinstance(certificate, str) or len(certificate) != 64 or any(c not in '0123456789abcdef' for c in certificate):
                raise ValueError('Invalid camera calibration certificate')
            expected_fields.add('camera_calibration_id')
        if op in {'preview_joint_delta', 'preview_unfold_arm'}:
            expected_fields.add('joint_preview')
        if op in {'joint_delta', 'unfold_arm'}:
            expected_fields.add('joint_rejection' if 'joint_rejection' in obs else 'joint_control')
        if op == 'recover_posture':
            expected_fields.add('posture_recovery')
        if set(obs) != expected_fields:
            raise ValueError('Unexpected observation fields')
        if op == 'control_action':
            feedback = obs['action_feedback']
            if set(feedback) != {'status', 'reason', 'executed_steps', 'controls', 'tracking', 'goal_compilation'}:
                raise ValueError('Unexpected action feedback fields')
            if feedback['status'] not in {'completed', 'episode_ended', 'rejected_before_motion', 'kinematic_rejection', 'tracking_or_gripper_change'}:
                raise ValueError('Unknown action status')
            count = feedback['executed_steps']
            if type(count) is not int or count < 0 or count != obs['step'] - self.obs['step']:
                raise ValueError('Action feedback disagrees with native steps')
            if len(feedback['tracking']) != 2 * count:
                raise ValueError('Incomplete action tracking evidence')
            if feedback['status'] == 'rejected_before_motion' and count:
                raise ValueError('Rejected action executed motion')
        if op in {'joint_delta', 'unfold_arm', 'preview_joint_delta', 'preview_unfold_arm'}:
            validate_joint_feedback(obs, op, kwargs, self.obs['step'])
        if op == 'recover_posture':
            recovery = obs['posture_recovery']
            if not isinstance(recovery, dict) or set(recovery) != {'status','target_reached','executed_steps','next_motion_verified','instruction'}:
                raise ValueError('Unexpected recovery result fields')
            if recovery['status'] not in {'budget_exhausted','target_reached','episode_ended','clearance_envelope_exit','tracking_or_gripper_change'}:
                raise ValueError('Unknown recovery status')
            if type(recovery['target_reached']) is not bool or recovery['next_motion_verified'] is not False:
                raise ValueError('Invalid recovery verification claim')
            if type(recovery['executed_steps']) is not int or not 0 <= recovery['executed_steps'] <= kwargs['recover_posture']['max_steps']:
                raise ValueError('Invalid recovery step count')
            if not isinstance(recovery['instruction'], str):
                raise ValueError('Invalid recovery instruction')
        with (self.trace/'commands.jsonl').open('a') as f:
            f.write(json.dumps(dict(command=command, step=obs['step'], seconds=time.monotonic()-start))+chr(10))
        self.obs = obs
        self.last_command_id = command['id']
        return obs

    def move(self, name, position=None, q=None, grip=None, steps=12, settle=1, arm=None):
        arm = self.active_arm if arm is None else arm
        if self.obs['ended']:
            raise RuntimeError('Episode already ended')
        expected_position = np.asarray(position if position is not None else self.obs['state'][arm+'_ee_pose'][:3],dtype=float)
        spec = {}
        if position is not None: spec['position'] = list(position)
        if q is not None: spec['quaternion'] = list(q)
        if grip is not None: spec['gripper'] = grip
        result=self.command('move', arms={arm:spec}, steps=steps, settle=settle, label=name)
        if np.linalg.norm(np.asarray(result['state'][arm+'_ee_pose'][:3])-expected_position)>.04:
            raise RuntimeError('Motion tracking error exceeds 4cm: '+name)
        return result
