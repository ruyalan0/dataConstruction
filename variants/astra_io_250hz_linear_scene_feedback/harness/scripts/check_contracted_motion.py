"""Exercise finite contraction through actual IPC with ideal FK dynamics."""
import argparse
import json
import tempfile
import threading
import time
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from motion_server_fixture import run_server
from egg_harness.kinematics import Kinematics

model=Kinematics(json.loads(Path("configs/x5-kinematics.json").read_text()))
parser=argparse.ArgumentParser()
parser.add_argument('--mode', choices=['development','frozen'], default='development')
mode=parser.parse_args().mode
class Env:
    step_lim=200
    robot_manager=type("Manager",(),{"robot_list":[], 'solve_ik':lambda *a,**kw:None})()
    def __init__(self):
        self.take_action_cnt=[0];self.q={a:np.array([.3,.5,.7,-.4,.2,.1]) for a in ("left","right")};self.actions=[]
    def get_obs(self):
        state={}
        for arm,q in self.q.items():
            t=model.forward(q)
            state[arm+"_arm_joint_state"]=q.tolist()
            state[arm+"_ee_pose"]=np.r_[t[:3,3],Rotation.from_matrix(t[:3,:3]).as_quat()[[3,0,1,2]]].tolist()
            state[arm+"_ee_joint_state"]=[1]
            state[arm+"_gripper_measured_joints"]=[.04]
        return {"state":state,"vision":{"head":{"color":np.zeros((4,4,3),np.uint8)}},"instruction":"test"}
    def is_episode_end(self):return False
    def take_action(self,action):
        assert set(action)=={"left_arm_joint_state","right_arm_joint_state","left_ee_joint_state","right_ee_joint_state"}
        for arm in self.q:
            assert np.max(np.abs(action[arm+"_arm_joint_state"]-self.q[arm]))<=.150001
            self.q[arm]=action[arm+"_arm_joint_state"].copy()
        self.actions.append(action);self.take_action_cnt[0]+=1
with tempfile.TemporaryDirectory() as d:
    out=Path(d);env=Env();errors=[]
    def run():
        try:run_server(env,out,[20 if mode=='development' else 23],mode,"classify_objects_by_language")
        except BaseException as e:errors.append(e)
    thread=threading.Thread(target=run,daemon=True);thread.start()
    def wait(path):
        start=time.monotonic()
        while not path.exists():
            if errors:raise errors[0]
            if time.monotonic()-start>10:raise TimeoutError(path)
            time.sleep(.01)
    wait(out/"ready.json")
    provenance=json.loads((out/'provenance.json').read_text())
    assert provenance['mode']==mode
    assert provenance['privileged_diagnostics']==(mode=='development')
    assert (out/'developer-only').exists()==(mode=='development')
    def command(name,**body):
        p=out/"commands"/(name+".json");tmp=p.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(id=name,**body)));tmp.replace(p)
        response=out/"responses"/p.name;wait(response);return json.loads(response.read_text())
    right=env.q["right"].copy()
    target=model.forward(env.q["left"]+np.array([.01,0,0,0,0,0]))
    target_pose=np.r_[target[:3,3],Rotation.from_matrix(target[:3,:3]).as_quat()[[3,0,1,2]]].tolist()
    obs=command("a",op="move_local",arms={"left":{"pose":target_pose}},steps=2)
    assert obs["step"]==2 and np.allclose(env.q["right"],right)
    assert np.linalg.norm(np.array(obs["state"]["left_ee_pose"][:3])-target[:3,3])<.001
    rejected=command("b",op="move_local",arms={"left":{"delta":[.6,0,0]}},steps=1)
    assert rejected["step"]==2 and len(env.actions)==2
    assert json.loads((out/"interruptions.jsonl").read_text())["reason"]=="local_ik_rejected"
    command("c",op="move_local",arms={"left":{"delta":[.6,0,0],"gripper":0}},steps=1)
    command("d",op="move_local",arms={},steps=1)
    assert env.actions[-1]["left_ee_joint_state"][0]==1, "Rejected motion changed later gripper intent"
    from egg_harness.skill_policy import compile_motion,validate
    before={a:env.q[a].copy() for a in env.q};goals={}
    for arm,sign in (("left",1),("right",-1)):
        target=model.forward(before[arm]+np.array([sign*.02,0,0,0,0,0]))
        goals[arm]={"position":target[:3,3].tolist(),"quaternion":Rotation.from_matrix(target[:3,:3]).as_quat()[[3,0,1,2]].tolist()}
    compiled,steps,settle=compile_motion({"arms":goals,"steps":2},"fine",env.get_obs(),validate)
    observed=command("e",op="move_local",arms={a:{k:v for k,v in item["action"].items() if k in ("position","quaternion")} for a,item in compiled.items()},steps=steps,guards=[{"arm":a,"tracking_error_m":.035,"watch_grip":False,"opening_change":.003} for a in goals])
    assert observed["step"]==5
    assert all(not np.allclose(env.q[a],before[a]) for a in before), "Both arms must move in the same two native steps"
    before={a:env.q[a].copy() for a in env.q}
    observed=command("f",op="move_local",arms={"left":{"delta":[.001,0,0]},"right":{"delta":[.6,0,0]}},steps=1)
    assert observed["step"]==5 and all(np.array_equal(env.q[a],before[a]) for a in before), "One invalid arm must reject the entire native action"
    before={a:env.q[a].copy() for a in env.q}
    start=env.get_obs()['state']
    large=model.forward(before['left']+np.array([.24,0,0,0,0,0]))
    target_pose=np.r_[large[:3,3],Rotation.from_matrix(large[:3,:3]).as_quat()[[3,0,1,2]]].tolist()
    observed=command('g',op='move_local',arms={'left':{'pose':target_pose}},steps=1,settle=2,contract_local_ik=True)
    assert observed['step']==6, 'Contracted action must return after one native step, without settle'
    assert np.array_equal(env.q['right'],before['right'])
    contraction=json.loads((out/'ik-contractions.jsonl').read_text().splitlines()[-1])
    assert contraction['scale'] in (.5,.25,.125) and len(contraction['attempts'])<=4
    row=json.loads((out/'actions.jsonl').read_text().splitlines()[-1])
    selected=np.asarray(row['cartesian_target']['left_ee_pose'])
    assert np.allclose(selected[:3],np.asarray(start['left_ee_pose'][:3])+contraction['scale']*(large[:3,3]-start['left_ee_pose'][:3]))
    assert np.linalg.norm(np.asarray(observed['state']['left_ee_pose'][:3])-selected[:3])<.001
    before={a:env.q[a].copy() for a in env.q}
    goals={}
    for arm,sign in (('left',1),('right',-1)):
        target=model.forward(before[arm]+np.array([sign*.24,0,0,0,0,0]))
        goals[arm]={'pose':np.r_[target[:3,3],Rotation.from_matrix(target[:3,:3]).as_quat()[[3,0,1,2]]].tolist()}
    observed=command('h',op='move_local',arms=goals,steps=1,settle=2,contract_local_ik=True)
    event=json.loads((out/'ik-contractions.jsonl').read_text().splitlines()[-1])
    assert len(event['attempts'])<=4 and event['scale'] in (.5,.25,.125)
    assert observed['step']==7
    assert all(not np.array_equal(env.q[a],before[a]) for a in before)
    from egg_harness.motion_evidence import executed_motion,target_for_arm
    evidence=executed_motion(out,'h',6,7)
    assert evidence['contracted']
    for arm in ('left','right'):
        position,quaternion=target_for_arm(evidence,arm,start[arm+'_ee_pose'],1)
        assert np.linalg.norm(position-np.asarray(observed['state'][arm+'_ee_pose'][:3]))<.001
    if mode=='frozen':
        path=out/'commands/z-diagnostic.json'
        path.write_text(json.dumps({'id':'z-diagnostic','op':'diagnostic'}))
        thread.join(2)
        assert not thread.is_alive() and len(errors)==1
        assert isinstance(errors[0],ValueError) and 'development-only' in str(errors[0])
        assert not (out/'developer-only').exists()
    else:
        command("z",op="finish");thread.join(2)
        assert not thread.is_alive() and not errors
print(mode+' IPC contraction preserves bounds, inactive arm and actual selected targets; returns after one contracted step')
