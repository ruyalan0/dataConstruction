"""Small air-motion checks using allowed EE observations and the native controller."""

import json

import numpy as np

from dojo_astra.arx_x5_interpreter import rotation
from dojo_astra.executor import Executor
from dojo_astra.observation_adapter import ARMS, adapt
from dojo_astra.trace_store import TraceStore
from dojo_astra.trajectory import Trajectory


def run_interface_probe(env, output):
    trace = TraceStore(output, 'interface')
    ex = Executor(detailed_feedback=True)
    first = adapt(env.get_obs(), 0)
    trace.observation(first)
    ex.observe(first)
    initial = {a: p.copy() for a, p in first.poses.items()}
    phases, sequence = [], 0

    def execute(label, goal, motion, settle, grip='hold'):
        nonlocal sequence
        left = ex.obs.poses['left']
        delta = (goal[:3] - left[:3]) * 1000
        turn = (rotation(goal) * rotation(left).inv()).as_rotvec() * 180 / np.pi
        command = Trajectory.model_validate({
            'waypoints': [{'left': {'translation_mm': delta.tolist(), 'rotation_vector_deg': turn.tolist(),
                           'frame': 'world', 'reference': 'current', 'gripper': grip},
                           'right': {'translation_mm': [0, 0, 0], 'rotation_vector_deg': [0, 0, 0],
                            'frame': 'world', 'reference': 'latched', 'gripper': 'hold'}}],
            'motion_steps': motion, 'settle_steps': settle, 'note': 'Generic air interface check: ' + label})
        ex.load(command)
        trace.event('decision', command.model_dump())
        start = ex.obs.poses['left'].copy()
        first_displacement = None
        count = 0
        while ex.remaining:
            if env.is_episode_end():
                raise RuntimeError('Native environment ended during interface check')
            action = ex.next_action()
            before = env.take_action_cnt[0]
            env.take_action(action)
            if env.take_action_cnt[0] != before + 1:
                raise RuntimeError('Native action was not acknowledged')
            count += 1
            sequence += 1
            trace.event('native_action', {k: v.tolist() for k, v in action.items()})
            obs = adapt(env.get_obs(), sequence)
            trace.observation(obs)
            ex.observe(obs)
            trace.event('native_feedback', ex.feedback)
            if count == 1:
                first_displacement = float(np.linalg.norm(obs.poses['left'][:3] - start[:3]) * 1000)
        result = {'label': label, 'native_steps': count, 'requested_steps': motion + settle,
                  'first_observed_displacement_mm': first_displacement, 'feedback': ex.feedback,
                  'endpoint_error_mm': float(np.linalg.norm(ex.obs.poses['left'][:3] - goal[:3]) * 1000),
                  'endpoint_error_deg': float(np.rad2deg((rotation(goal).inv() * rotation(ex.obs.poses['left'])).magnitude()))}
        phases.append(result)
        (output / 'interface-checkpoint.json').write_text(json.dumps({'phases': phases}, indent=2))
        if count != motion + settle or ex.feedback['interrupted']:
            raise RuntimeError('Native path or settling was interrupted: ' + label)
        if result['endpoint_error_mm'] > 2 or result['endpoint_error_deg'] > 2:
            raise RuntimeError('Endpoint exceeds the existing 2 mm / 2 degree tracking tolerance: ' + label)

    from scipy.spatial.transform import Rotation

    goal = initial['left'].copy()
    goal[:3] += [0.006, 0, 0.006]
    goal[3:] = (Rotation.from_rotvec([0, 0, np.deg2rad(6)]) * rotation(goal)).as_quat(scalar_first=True)
    execute('slow_diagonal_pose', goal, 4, 2)
    execute('return_after_slow', initial['left'], 4, 2)
    execute('fast_same_endpoint', goal, 1, 2)
    execute('return_after_fast', initial['left'], 4, 2)
    execute('close_in_air_wait', initial['left'], 1, 3, 'close')
    execute('open_in_air_wait', initial['left'], 1, 3, 'open')
    slow, fast = phases[0]['first_observed_displacement_mm'], phases[2]['first_observed_displacement_mm']
    if fast <= slow + 0.5:
        raise RuntimeError('Different motion durations did not produce distinguishable first-boundary displacement')
    result = {'status': 'completed', 'development_only': True, 'task_success': None,
              'native_steps': sequence, 'observations': sequence + 1, 'api_calls': 0,
              'phases': phases, 'first_displacement_fast_over_slow': fast / slow,
              'final_pose_error_mm': {a: float(np.linalg.norm(ex.obs.poses[a][:3] - initial[a][:3]) * 1000) for a in ARMS},
              'scope': 'Air motion and command waiting only; no contact, TCP calibration or task success claim'}
    (output / 'interface-result.json').write_text(json.dumps(result, indent=2))
    return result
