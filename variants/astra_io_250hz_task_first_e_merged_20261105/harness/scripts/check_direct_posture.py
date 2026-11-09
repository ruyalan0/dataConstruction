"""Exercise explicit posture through real server IPC with measured public FK."""
import json
import argparse
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace

import numpy as np
from scipy.spatial.transform import Rotation

from motion_server_fixture import run_server
from egg_harness.kinematics import Kinematics
from egg_harness.posture import posture_context


MODEL = Kinematics(json.loads(Path('configs/x5-kinematics.json').read_text()))
parser=argparse.ArgumentParser()
parser.add_argument('--mode',choices=['development','frozen'],default='development')
MODE=parser.parse_args().mode


def points(q):
    transform = np.eye(4)
    result = []
    for origin, axis, angle in zip(MODEL.origins, MODEL.axes, q):
        rotation = np.eye(4)
        rotation[:3, :3] = Rotation.from_rotvec(axis * angle).as_matrix()
        transform = transform @ origin @ rotation
        result.append(transform[:3, 3].copy())
    return np.asarray(result)


class Env:
    step_lim = 200

    def __init__(self, *, stalled=False, measured=.044, echo=1., close_after_step=False):
        # Managers are per environment: development instrumentation replaces solve_ik.
        self.robot_manager = SimpleNamespace(robot_list=[], solve_ik=lambda **kwargs: None)
        self.take_action_cnt = [0]
        self.q = {arm: np.array([.3, 3., 4.6, -1.9, .16, .48]) for arm in ('left', 'right')}
        self.actions = []
        self.stalled = stalled
        self.measured = measured
        self.echo = echo
        self.close_after_step = close_after_step

    def get_obs(self):
        state = {}
        for arm, q in self.q.items():
            transform = MODEL.forward(q)
            state[arm + '_arm_joint_state'] = q.tolist()
            state[arm + '_ee_pose'] = np.r_[transform[:3, 3], Rotation.from_matrix(transform[:3, :3]).as_quat()[[3, 0, 1, 2]]].tolist()
            state[arm + '_ee_joint_state'] = [self.echo]
            state[arm + '_gripper_measured_joints'] = [self.measured, self.measured]
        return {'state': state, 'vision': {'head': {'color': np.zeros((4, 4, 3), np.uint8)}}, 'instruction': 'test'}

    def is_episode_end(self):
        return False

    def take_action(self, action):
        assert set(action) == {arm + suffix for arm in self.q for suffix in ('_arm_joint_state', '_ee_joint_state')}
        record = {}
        for arm in self.q:
            target = np.asarray(action[arm + '_arm_joint_state'])
            delta = target - self.q[arm]
            assert np.max(np.abs(delta)) <= .15000001
            assert np.allclose(delta[[0, 2, 3, 4, 5]], 0, atol=1e-12), 'Posture changed another joint'
            assert np.max(np.linalg.norm(points(target) - points(self.q[arm]), axis=1)) <= .02400001, 'Link origin swept beyond 24 mm'
            assert np.linalg.norm(MODEL.forward(target)[:3, 3] - MODEL.forward(self.q[arm])[:3, 3]) <= .02400001
            record[arm + '_arm_joint_state'] = target.copy()
            if not self.stalled:
                self.q[arm] = target.copy()
        self.actions.append(record)
        self.take_action_cnt[0] += 1
        if self.close_after_step:
            self.measured = .02


class Session:
    def __init__(self, output, env, mode=MODE):
        self.output, self.env, self.errors = output, env, []
        def run():
            try:
                run_server(env, output, [20], mode, 'classify_objects_by_language')
            except BaseException as error:
                self.errors.append(error)
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        self.wait(output / 'ready.json')

    def wait(self, path):
        deadline = time.monotonic() + 10
        while not path.exists():
            if self.errors:
                raise self.errors[0]
            if time.monotonic() > deadline:
                raise TimeoutError(path)
            time.sleep(.005)

    def submit(self, name, **body):
        target = self.output / 'commands' / (name + '.json')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps({'id': name, **body}))
        temporary.replace(target)
        return self.output / 'responses' / target.name

    def command(self, name, **body):
        response = self.submit(name, **body)
        self.wait(response)
        return json.loads(response.read_text())

    def finish(self):
        self.command('zz-test-finish', op='finish')
        self.thread.join(2)
        assert not self.thread.is_alive() and not self.errors


with tempfile.TemporaryDirectory() as directory:
    output = Path(directory)
    env = Env()
    session = Session(output, env)
    before = posture_context(MODEL, env.get_obs()['state'])['arms']
    observation = session.command('bilateral', op='posture', posture={'left': 'upright', 'right': 'upright'}, steps=6)
    after = posture_context(MODEL, observation['state'])['arms']
    assert observation['step'] == 6 and len(env.actions) == 6
    for arm in env.q:
        assert after[arm]['link2_tilt_from_vertical_deg'] < before[arm]['link2_tilt_from_vertical_deg']
        assert all(action[arm + '_arm_joint_state'][1] < 3. for action in env.actions)
    inactive = env.q['right'].copy()
    session.command('single', op='posture', posture={'left': 'upright'}, steps=2)
    assert np.array_equal(env.q['right'], inactive), 'Inactive arm did not hold measured joints'
    recorded = [json.loads(line) for line in (output / 'actions.jsonl').read_text().splitlines()]
    assert len(recorded) == 8 and all(row['primitive'] == 'posture' for row in recorded)
    assert all(row['action'][arm + '_ee_joint_state'] == [1.] for row in recorded for arm in env.q), 'Posture changed gripper intent'
    if MODE=='frozen': assert not (output/'developer-only').exists()
    session.finish()

invalid_commands = [
    {'posture': {}, 'steps': 1},
    {'posture': {'left': 'down'}, 'steps': 1},
    {'posture': {'other': 'upright'}, 'steps': 1},
    {'posture': {'left': 'upright'}, 'steps': True},
    {'posture': {'left': 'upright'}, 'steps': 0},
    {'posture': {'left': 'upright'}, 'steps': 7},
    {'posture': {'left': 'upright'}, 'steps': 1, 'gripper': 1},
]
for mode, measured, command in [
    *[(MODE, .044, command) for command in invalid_commands],
    (MODE, .02, {'posture': {'left': 'upright'}, 'steps': 1}),
]:
    with tempfile.TemporaryDirectory() as directory:
        env = Env(measured=measured)
        session = Session(Path(directory), env, mode)
        session.submit('rejected', op='posture', **command)
        session.thread.join(3)
        assert not session.thread.is_alive(), 'Invalid request did not fail fast'
        assert len(session.errors) == 1 and isinstance(session.errors[0], ValueError), session.errors
        assert not env.actions and env.take_action_cnt == [0], 'Rejected request moved the robot'

with tempfile.TemporaryDirectory() as directory:
    env = Env()
    session = Session(Path(directory), env)
    # A delayed physical gripper still reads open after a closing command.
    # Posture must reject the retained closed intent before another native step.
    session.command('a-close', op='move_local', arms={'left': {'gripper': 0}}, steps=1)
    assert env.get_obs()['state']['left_gripper_measured_joints'] == [.044, .044]
    previous_count = len(env.actions)
    session.submit('b-rejected', op='posture', posture={'left': 'upright'}, steps=1)
    session.thread.join(3)
    assert not session.thread.is_alive() and len(session.errors) == 1
    assert isinstance(session.errors[0], ValueError) and 'open gripper' in str(session.errors[0])
    assert len(env.actions) == previous_count, 'Closed gripper intent allowed a posture step'

for behavior in ({'stalled': True}, {'close_after_step': True}):
    with tempfile.TemporaryDirectory() as directory:
        env = Env(**behavior)
        output = Path(directory)
        session = Session(output, env)
        observation = session.command('stop', op='posture', posture={'left': 'upright', 'right': 'upright'}, steps=6)
        assert observation['step'] == 1 and len(env.actions) == 1, 'Tracking/opening failure must stop the first native step'
        interruptions = [json.loads(line) for line in (output / 'interruptions.jsonl').read_text().splitlines()]
        assert interruptions and all(row['reason'] == 'posture_tracking_or_gripper_change' for row in interruptions)
        session.finish()

if MODE=='frozen':
    with tempfile.TemporaryDirectory() as directory:
        session=Session(Path(directory),Env())
        session.submit('no-diagnostics',op='diagnostic')
        session.thread.join(3)
        assert not session.thread.is_alive() and len(session.errors)==1
        assert not (Path(directory)/'developer-only').exists()

print('Posture IPC '+MODE+': bilateral tilt, inactive hold, joint/link bounds, closed-hand rejection, tracking stops and diagnostic isolation passed')
