"""Exercise one-call recovery through server IPC, including interrupted execution."""
import tempfile
import subprocess
import sys
import json
from pathlib import Path
import numpy as np
from check_direct_posture import Env, Session, MODEL
from egg_harness.posture_recovery import points, validate_recovery, plan_recovery
from egg_harness.posture_action import posture_increment, upright_joint_target
from egg_harness.client import Robot


def request(env):
    state = env.get_obs()['state']
    arm = 'left'
    target = upright_joint_target(MODEL, state[arm+'_arm_joint_state'], state[arm+'_ee_pose'])
    geometry = [points(MODEL, state[arm+'_arm_joint_state'], state[arm+'_ee_pose'])]
    for _ in range(40):
        joints, poses = posture_increment(MODEL, state, {arm: target})
        state[arm+'_arm_joint_state'] = joints[arm].tolist()
        state[arm+'_ee_pose'] = poses[arm].tolist()
        geometry.append(points(MODEL, joints[arm], poses[arm]))
    geometry = np.concatenate(geometry)
    return {'arm': arm, 'reason': 'approach_blocked',
            'clearance_box': [(geometry.min(axis=0)-.04).tolist(), (geometry.max(axis=0)+.04).tolist()], 'max_steps': 40}


for behavior in ({}, {'stalled': True}, {'close_after_step': True}):
    with tempfile.TemporaryDirectory() as folder:
        env = Env(**behavior)
        session = Session(Path(folder), env)
        spec = request(env)
        validate_recovery({'recover_posture': spec}, 'transit')
        plan_recovery(MODEL, env.get_obs()['state'], spec)
        trace=Path(folder)/'client-trace';trace.mkdir()
        robot=Robot(Path(folder),trace)
        result = robot.command('recover_posture', recover_posture=spec)
        command_id=robot.last_command_id
        feedback = result['posture_recovery']
        assert not feedback['next_motion_verified']
        if behavior:
            assert result['step'] == 1 and feedback['status'] == 'tracking_or_gripper_change'
        else:
            assert 6 < result['step'] <= 40
            assert feedback['target_reached'] and feedback['status'] == 'target_reached'
        assert (Path(folder)/(command_id+'-recovery-plan.json')).exists()
        audit_output=Path(folder)/'audit.json'
        subprocess.run([sys.executable,'scripts/audit_posture_execution.py',folder,'--through',str(result['step']),'--output',str(audit_output)],check=True,stdout=subprocess.DEVNULL)
        audit=json.loads(audit_output.read_text())
        assert audit['recovery_commands'][command_id]['executed_steps']==result['step']
        if not behavior:
            response=Path(folder)/'responses'/(command_id+'.json')
            changed=json.loads(response.read_text());changed['posture_recovery']['next_motion_verified']=True
            response.write_text(json.dumps(changed))
            rejection=subprocess.run([sys.executable,'scripts/audit_posture_execution.py',folder,'--through',str(result['step']),'--output',str(Path(folder)/'tampered-audit.json')],capture_output=True,text=True)
            assert rejection.returncode and 'Invalid recovery outcome claim' in rejection.stderr
        session.finish()

with tempfile.TemporaryDirectory() as folder:
    env = Env()
    session = Session(Path(folder), env)
    spec = request(env)
    spec['clearance_box'] = [[0, 0, 0], [.1, .1, .1]]
    session.submit('reject', op='recover_posture', recover_posture=spec)
    session.thread.join(3)
    assert len(session.errors) == 1 and isinstance(session.errors[0], ValueError)
    assert not env.actions

for changed in ({'reason': 'horizontal'}, {'max_steps': True}, {'max_steps': 41}, {'arm': 'both'}):
    spec = {**request(Env()), **changed}
    try:
        validate_recovery({'recover_posture': spec}, 'transit')
    except ValueError:
        pass
    else:
        raise AssertionError(changed)
print('Recovery IPC: one-call completion, tracking/closure stop, clear-envelope preflight and invalid-input rejection passed')
