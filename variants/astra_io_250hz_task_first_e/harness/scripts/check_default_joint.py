"""Exercise matched interfaces through the real file IPC server and client."""
import json
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from motion_server_fixture import run_server
from egg_harness.action_control import matrix_pose
from egg_harness.client import Robot
from egg_harness.kinematics import Kinematics
from egg_harness.skill_policy import system_prompt
from egg_harness.native_tools import ToolRegistry

MODEL = Kinematics(json.loads(Path('configs/x5-kinematics.json').read_text()))


class Env:
    step_lim = 100
    def __init__(self, fault=None):
        self.robot_manager = SimpleNamespace(robot_list=[], solve_ik=lambda **kwargs: None)
        self.take_action_cnt = [0]
        self.q = {a: np.array([.3, .5, .7, -.4, .2, .1]) for a in ('left', 'right')}
        self.grips = {'left': 1., 'right': 1.}
        self.actions, self.fault = [], fault
    def get_obs(self):
        state = {}
        for arm, q in self.q.items():
            state[arm + '_arm_joint_state'] = q.tolist()
            state[arm + '_ee_pose'] = matrix_pose(MODEL.forward(q)).tolist()
            state[arm + '_ee_joint_state'] = [self.grips[arm]]
            state[arm + '_gripper_measured_joints'] = [.018, .018]
        return {'state': state, 'vision': {name: {'color': np.zeros((4, 4, 3), np.uint8)} for name in ('cam_head','cam_left_wrist','cam_right_wrist')}, 'instruction': 'Mock public task'}
    def is_episode_end(self): return False
    def take_action(self, action):
        assert set(action) == {a + s for a in self.q for s in ('_arm_joint_state', '_ee_joint_state')}
        for arm in self.q:
            q = np.asarray(action[arm + '_arm_joint_state'])
            assert np.max(np.abs(q - self.q[arm])) <= .150000001
            self.q[arm] = q.copy()
            self.grips[arm] = float(action[arm + '_ee_joint_state'][0])
        if self.fault == 'drift': self.q['left'][1] += .8
        self.actions.append({k: np.asarray(v).copy() for k, v in action.items()})
        self.take_action_cnt[0] += 1


class Session:
    def __init__(self, path, env, setting):
        self.errors = []
        def serve():
            try: run_server(env, path, [20], 'development', 'classify_objects_by_language', action_interface=setting)
            except BaseException as error: self.errors.append(error)
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not (path / 'ready.json').exists():
            if self.errors: raise self.errors[0]
            if time.monotonic() > deadline: raise TimeoutError('Server startup')
            time.sleep(.01)
        trace = path / 'trace'; trace.mkdir()
        self.robot = Robot(path, trace)
    def action(self, arms, steps=2, phase='fine', **kwargs):
        return self.robot.command('control_action', phase=phase, action={'arms': arms, 'steps': steps, **kwargs})
    def finish(self):
        self.robot.command('finish'); self.thread.join(3)
        assert not self.errors and not self.thread.is_alive(), self.errors


def goal(env, arm, mode, grip=.37):
    q = env.q[arm] + np.array([.01, -.01, .01, -.01, .01, -.01])
    if mode == 'joint': return {'control': mode, 'joints': q.tolist(), 'gripper': grip}
    pose = matrix_pose(MODEL.forward(q))
    return {'control': mode, 'toward': {'position': pose[:3].tolist(), 'quaternion': pose[3:].tolist()}, 'gripper': grip}


def check_setting(setting):
    with tempfile.TemporaryDirectory() as folder:
        path, env = Path(folder), Env()
        session = Session(path, env, setting)
        left_mode = 'joint' if setting == 'joint' else 'tcp'
        right_mode = 'tcp' if setting == 'tcp' else 'joint'
        result = session.action({'left': goal(env, 'left', left_mode), 'right': goal(env, 'right', right_mode, .62)})
        assert result['step'] == 2 and result['action_feedback']['status'] == 'completed'
        assert all(a['left_ee_joint_state'][0] == .37 and a['right_ee_joint_state'][0] == .62 for a in env.actions)
        # Closed, object-like measured fingers do not exclude full joint control.
        session.action({'left': {'control': left_mode, 'gripper': 0}})
        result = session.action({'left': goal(env, 'left', left_mode, 0)})
        assert result['step'] == 6 and env.grips['left'] == 0
        before = len(env.actions)
        if setting != 'both':
            invalid = 'joint' if setting == 'tcp' else 'tcp'
            rejected = session.action({'left': goal(env, 'left', invalid)})
            assert rejected['action_feedback']['status'] == 'rejected_before_motion'
            assert len(env.actions) == before and rejected['step'] == before
        bad = goal(env, 'right', right_mode); bad['gripper'] = float('nan')
        result = session.action({'left': goal(env, 'left', left_mode), 'right': bad})
        assert result['action_feedback']['executed_steps'] == 0 and len(env.actions) == before
        held = env.q['right'].copy()
        session.action({'left': goal(env, 'left', left_mode)}, settle=1)
        assert np.array_equal(env.q['right'], held)
        records = [json.loads(line) for line in (path / 'actions.jsonl').read_text().splitlines()]
        assert len(records) == len(env.actions)
        assert all((path / 'frames' / f'step-{i:04d}.json').exists() for i in range(1, len(records) + 1))
        session.finish()


for setting in ('tcp', 'joint', 'both'):
    check_setting(setting)

with tempfile.TemporaryDirectory() as folder:
    env = Env(); session = Session(Path(folder), env, 'both')
    arms = {'left': goal(env, 'left', 'joint'), 'right': goal(env, 'right', 'tcp')}
    with patch.object(Kinematics, 'solve_local', side_effect=ValueError('deliberate IK rejection')):
        result = session.action(arms)
    assert result['step'] == 0 and not env.actions
    assert result['action_feedback']['status'] == 'kinematic_rejection'
    assert env.grips == {'left': 1., 'right': 1.}, 'Rejected coupled action changed gripper intent'
    session.finish()

with tempfile.TemporaryDirectory() as folder:
    env = Env('drift'); session = Session(Path(folder), env, 'joint')
    result = session.action({'left': goal(env, 'left', 'joint')}, steps=6)
    assert result['step'] == 1 and result['action_feedback']['status'] == 'tracking_or_gripper_change'
    session.finish()


# The default native registry has no TCP or preview-gated recovery motion tools.
registry = ToolRegistry({})
assert registry.action_interface == 'joint'
assert set(registry.contracts) == {'review_frames', 'project_pixels', 'triangulate_point',
    'triangulate_temporal_point', 'control_arms', 'verify_completion', 'stop_episode'}
meta={'assessment':'visible clearance','plan':'small native joint step','phase':'fine','memory':'carry'}
args={**meta,'left':{'control':'joint','joints':[.3,.5,.7,-.4,.2,.1],'gripper':.37},
      'right':{'control':'joint','joints':[.3,.5,.7,-.4,.2,.1],'gripper':.62},'steps':2,'settle':0}
assert registry.decision('control_arms',json.dumps(args))['action']['control_arms']['arms']['left']['gripper']==.37
for name in ('move_arm','move_arms','execute_joint_delta','set_posture'):
    try: registry.decision(name,json.dumps(args))
    except ValueError: pass
    else: raise AssertionError('Joint-only registry exposed '+name)
for corrupt in ({**args,'left':{'control':'tcp','toward':None,'gripper':None}},
                {**args,'left':{'control':'joint','joints':[0]*5,'gripper':.5}},
                {**args,'right':{'control':'joint','joints':[0]*6,'gripper':1.1}}):
    try: registry.decision('control_arms',json.dumps(corrupt))
    except ValueError: pass
    else: raise AssertionError('Invalid native joint schema accepted')
with tempfile.TemporaryDirectory() as folder:
    path, env=Path(folder),Env();session=Session(path,env,'joint')
    assert session.robot.obs['action_interface']=='joint'
    (path/'commands'/'bypass.json').write_text(json.dumps({'id':'bypass','op':'move_local','arms':{},'steps':1}))
    session.thread.join(3)
    assert not env.actions and len(session.errors)==1 and isinstance(session.errors[0],ValueError)
assert 'ABSOLUTE joint targets' in system_prompt('joint')
assert 'bounded joint observation moves' in system_prompt('joint')
assert 'ordinary bounded TCP observation moves' not in system_prompt('joint')
print('PASS: default joint native tools, 14 controls, held-object motion, atomic rejection, tracking, TCP isolation and temporal-vision instructions')

# Real native Responses -> skill_policy -> file IPC -> joint executor -> tool result.
import sys
from scripts.check_native_tools import Client, call, response, META, TEST_PROVIDER_URLS
from egg_harness import native_responses, skill_policy
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);episode=root/'episode';episode.mkdir()
    env=Env();env.success=[True];env.is_episode_end=lambda:env.take_action_cnt[0]>=8
    session=Session(episode,env,'joint')
    credential=root/'credential.py';credential.write_text('client=AzureOpenAI(api_key="TEST_ONLY")')
    skill=root/'skills'/'fixture';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('Inspect RGB and control available joints.')
    memory=root/'memory.json';memory.write_text('{"head_camera":{}}')
    config={'credentials':str(credential),'astra_provider':'azure-astra','model':'gpt-6-astra',
            'provider_base_urls':TEST_PROVIDER_URLS,
            'reasoning_effort':'high','request_timeout':10,'max_model_calls':2,
            'skills_root':str(skill.parent),'skill_names':['fixture'],'memory':str(memory),
            'public_task_requirements':'Fixture: finish after eight actual joint actions'}
    config_path=root/'config.json';config_path.write_text(json.dumps(config))
    arms={a:goal(env,a,'joint',.37 if a=='left' else .62) for a in ('left','right')}
    client=Client([response([call('control_arms',{**META,**arms,'steps':2,'settle':0},1)]),
                   response([call('verify_completion',META,2)])])
    trace=root/'policy'
    with patch.object(native_responses,'OpenAI',return_value=client), \
         patch.object(Kinematics,'solve_local',side_effect=AssertionError('Joint-only called IK')), \
         patch.object(sys,'argv',['policy','--config',str(config_path),'--episode',str(episode),'--trace',str(trace)]):
        skill_policy.main()
    assert len(env.actions)==8 and len(client.requests)==2
    assert all(x['left_ee_joint_state'][0]==.37 and x['right_ee_joint_state'][0]==.62 for x in env.actions)
    assert json.loads((trace/'policy-result.json').read_text())['status']=='native_end'
    assert len(list((trace/'model').glob('*.tool-result.json')))==2
    first=json.loads((trace/'model'/'0001.request.json').read_text())
    context=json.loads(first['question'])
    assert context['action_interface']=='joint' and set(context['home_joints'])=={'left','right'}
    assert 'joint_semantics' in context
    assert {t['name'] for t in client.requests[0]['tools']}==set(registry.contracts)
    session.finish()
print('PASS: full native Responses/skill policy/IPC pipeline and completion hold execute without IK')
