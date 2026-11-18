"""Multijoint IPC: previews, fixed holds, drift stops and zero-action rejection."""
import json
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from scipy.spatial.transform import Rotation
from motion_server_fixture import run_server
from egg_harness.client import Robot, validate_joint_feedback
from egg_harness.kinematics import Kinematics
from egg_harness.joint_control import geometry

MODEL = Kinematics(json.loads(Path('configs/x5-kinematics.json').read_text()))

class Env:
    step_lim = 100
    def __init__(self, fault=None):
        self.robot_manager = SimpleNamespace(robot_list=[], solve_ik=lambda **kwargs: None)
        self.take_action_cnt = [0]
        self.q = {a: np.array([.3, 3., 4.6, -1.9, .16, .48]) for a in ('left', 'right')}
        self.opening = {a: [.044, .044] for a in self.q}
        self.actions = []
        self.fault = fault
    def get_obs(self):
        state = {}
        for a, q in self.q.items():
            matrix = MODEL.forward(q)
            state[a + '_arm_joint_state'] = q.tolist()
            state[a + '_ee_pose'] = np.r_[matrix[:3, 3], Rotation.from_matrix(matrix[:3, :3]).as_quat()[[3, 0, 1, 2]]].tolist()
            state[a + '_ee_joint_state'] = [1.]
            state[a + '_gripper_measured_joints'] = list(self.opening[a])
        return {'state': state, 'vision': {'head': {'color': np.zeros((4, 4, 3), np.uint8)}}, 'instruction': 'Public mock scene'}
    def is_episode_end(self): return False
    def take_action(self, action):
        assert set(action) == {a + s for a in self.q for s in ('_arm_joint_state', '_ee_joint_state')}
        record = {k: np.asarray(v).copy() for k, v in action.items()}
        for a in self.q:
            target = record[a + '_arm_joint_state']
            assert np.max(np.abs(target - self.q[a])) <= .150000001
            self.q[a] = target.copy()
        if self.fault == 'held_drift': self.q['right'][0] += .05
        if self.fault == 'held_grip': self.opening['right'] = [.03, .03]
        if self.fault == 'selected_grip': self.opening['left'] = [.03, .03]
        if self.fault == 'small_selected_hold_drift': self.q['left'][5] += .002
        self.actions.append(record)
        self.take_action_cnt[0] += 1

class Session:
    def __init__(self, path, env, mode='development'):
        self.path, self.errors = path, []
        def run():
            try: run_server(env, path, [20], mode, 'classify_objects')
            except BaseException as error: self.errors.append(error)
        self.thread = threading.Thread(target=run, daemon=True); self.thread.start()
        self.wait(path / 'ready.json')
        trace = path / 'trace'; trace.mkdir()
        self.robot = Robot(path, trace)
    def wait(self, path):
        deadline = time.monotonic() + 10
        while not path.exists():
            if self.errors: raise self.errors[0]
            if time.monotonic() > deadline: raise TimeoutError(path)
            time.sleep(.005)
    def reject(self, **command):
        p = self.path / 'commands' / 'z-reject.json'
        p.write_text(json.dumps({'id': 'z-reject', **command}))
        self.thread.join(5)
        assert not self.thread.is_alive() and len(self.errors) == 1 and isinstance(self.errors[0], ValueError), self.errors
    def finish(self):
        self.robot.command('finish')
        self.thread.join(2)
        assert not self.errors and not self.thread.is_alive()

DELTA = {'left': {'joint2': -.1, 'joint3': -.12, 'joint4': .08}}

def execute(session, deltas=DELTA, steps=6):
    before = session.robot.obs['step']
    preview = session.robot.command('preview_joint_delta', joint_delta=deltas, steps=steps)
    assert preview['step'] == before and preview['joint_preview']['executed_steps'] == 0
    token = preview['joint_preview']['preview_id']
    result = session.robot.command('joint_delta', joint_delta=deltas, steps=steps, preview_id=token)
    assert result['joint_control']['next_motion_verified'] is False
    return result

for fault in (None, 'held_drift', 'held_grip', 'selected_grip', 'small_selected_hold_drift'):
    with tempfile.TemporaryDirectory() as folder:
        env = Env(fault); initial = env.q['right'].copy()
        session = Session(Path(folder), env)
        result = execute(session)
        if fault in ('held_drift', 'held_grip', 'selected_grip'):
            assert result['step'] == 1 and result['joint_control']['status'] == 'tracking_or_gripper_change'
        else:
            assert 1 < result['step'] <= 6, (fault, result['joint_control'])
        assert all(np.array_equal(row['right_arm_joint_state'], initial) for row in env.actions)
        assert all(row['left_arm_joint_state'][5] == initial[5] for row in env.actions), 'Selected-arm unnamed joint drifted its target'
        assert all(row[a + '_ee_joint_state'].tolist() == [1.] for row in env.actions for a in env.q)
        assert len(result['joint_control']['tracking']) == 2 * result['step']
        assert all((Path(folder) / 'frames' / ('step-%04d-head.jpg' % n)).exists() for n in range(1, result['step'] + 1))
        corrupted = json.loads(json.dumps(result)); corrupted['joint_control']['next_motion_verified'] = True
        try: validate_joint_feedback(corrupted, 'joint_delta', {'joint_delta': DELTA, 'steps': 6, 'preview_id': result['joint_control']['preview_id']}, 0)
        except ValueError: pass
        else: raise AssertionError('Accepted false next-motion claim')
        session.finish()

with tempfile.TemporaryDirectory() as folder:
    env = Env(); session = Session(Path(folder), env)
    execute(session, {**DELTA, 'right': {'joint2': -.08, 'joint3': -.06}})
    assert any(not np.array_equal(a['left_arm_joint_state'], a['right_arm_joint_state']) for a in env.actions)
    session.finish()

for variant in ('missing_preview', 'wrong_token', 'stale_state', 'mismatched_spec', 'closed_selected', 'frozen', 'bad_envelope'):
    with tempfile.TemporaryDirectory() as folder:
        env = Env(); session = Session(Path(folder), env, 'frozen' if variant == 'frozen' else 'development')
        request = {'op': 'joint_delta', 'joint_delta': DELTA, 'steps': 6}
        if variant == 'wrong_token': request['preview_id'] = 'f' * 64
        if variant in ('stale_state', 'mismatched_spec'):
            preview = session.robot.command('preview_joint_delta', joint_delta=DELTA, steps=6)
            request['preview_id'] = preview['joint_preview']['preview_id']
            if variant == 'stale_state': env.q['left'][0] += .002
            else: request['steps'] = 5
        elif variant == 'closed_selected':
            env.opening['left'] = [.02, .02]
            request['op'] = 'preview_joint_delta'
        elif variant == 'frozen': request['op'] = 'preview_joint_delta'
        elif variant == 'bad_envelope':
            request = {'op': 'preview_unfold_arm', 'unfold_arm': {'arm': 'left', 'reason': 'descent_blocked', 'max_steps': 24, 'clearance_box': [[0, 0, 0], [.1, .1, .1]]}}
        if variant in ('missing_preview', 'wrong_token', 'stale_state', 'mismatched_spec'):
            rejected = session.robot.command(**request)
            assert rejected['joint_rejection']['status'] == 'rejected_before_motion'
            assert rejected['joint_rejection']['executed_steps'] == 0 and not rejected['joint_rejection']['next_motion_verified']
            assert not env.actions and env.take_action_cnt == [0], variant
            assert session.thread.is_alive() and not session.errors
            assert session.robot.command('observe')['step'] == 0
            forged = json.loads(json.dumps(rejected)); forged['joint_rejection']['executed_steps'] = 1
            try: validate_joint_feedback(forged, 'joint_delta', request, 0)
            except ValueError: pass
            else: raise AssertionError('Accepted moving rejection')
            result = execute(session)
            assert result['step'] > 0 and 'joint_control' in result
            session.finish()
        else:
            session.reject(**request)
            assert not env.actions and env.take_action_cnt == [0], variant

with tempfile.TemporaryDirectory() as folder:
    env = Env(); session = Session(Path(folder), env)
    first = execute(session); old_count = len(env.actions)
    old_actions = [{key: value.copy() for key, value in row.items()} for row in env.actions]
    rejected = session.robot.command('joint_delta', joint_delta=DELTA, steps=6, preview_id='0' * 64)
    assert rejected['step'] == first['step'] and len(env.actions) == old_count
    assert all(np.array_equal(old[key], new[key]) for old, new in zip(old_actions, env.actions) for key in old)
    assert execute(session)['step'] > first['step']
    session.finish()

# An unrelated failure after a native action must remain fatal; the narrow
# binding exception must never convert execution errors into zero-step refusals.
with tempfile.TemporaryDirectory() as folder:
    env = Env(); session = Session(Path(folder), env)
    preview = session.robot.command('preview_joint_delta', joint_delta=DELTA, steps=6)
    original_take_action = env.take_action
    def failing_take_action(action):
        original_take_action(action)
        raise ValueError('Injected execution failure after native step')
    env.take_action = failing_take_action
    session.reject(op='joint_delta', joint_delta=DELTA, steps=6, preview_id=preview['joint_preview']['preview_id'])
    assert env.take_action_cnt == [1] and len(env.actions) == 1
    assert not (Path(folder) / 'responses' / 'z-reject.json').exists()

with tempfile.TemporaryDirectory() as folder:
    env = Env(); session = Session(Path(folder), env)
    state = env.get_obs()['state']; g = geometry(MODEL, state, 'left')
    points = np.asarray(g['origins_world_m'] + [g['finger_proxy_world_m']])
    box = [(points.min(axis=0) - .18).tolist(), (points.max(axis=0) + .18).tolist()]
    spec = {'arm': 'left', 'reason': 'descent_blocked', 'max_steps': 24, 'clearance_box': box}
    preview = session.robot.command('preview_unfold_arm', unfold_arm=spec)
    assert not env.actions
    result = session.robot.command('unfold_arm', unfold_arm=spec, preview_id=preview['joint_preview']['preview_id'])
    assert 1 <= result['step'] <= 24 and not result['joint_control']['next_motion_verified']
    assert result['joint_control']['target_reached']
    session.finish()

print('Joint IPC passed: zero-motion previews, coordinated/bilateral execution, fixed joint/grip holds, both-arm drift stops, stale/closed/frozen/envelope rejection, unfold execution and false-claim rejection')
