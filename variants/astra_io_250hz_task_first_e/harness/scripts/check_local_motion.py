"""Exercise local-IK IPC through native joint actions and rejected targets."""
import json
import tempfile
import threading
import time
from pathlib import Path
import numpy as np
from unittest.mock import patch
from scipy.spatial.transform import Rotation
from motion_server_fixture import run_server
from egg_harness.kinematics import Kinematics
from egg_harness.storage import StoragePressure

model=Kinematics(json.loads(Path("configs/x5-kinematics.json").read_text()))
class Env:
    step_lim=200
    robot_manager=type("Manager",(),{"robot_list":[]})()
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
        try:run_server(env,out,[20],"frozen","classify_objects_by_language")
        except BaseException as e:errors.append(e)
    thread=threading.Thread(target=run,daemon=True);thread.start()
    def wait(path):
        start=time.monotonic()
        while not path.exists():
            if errors:raise errors[0]
            if time.monotonic()-start>10:raise TimeoutError(path)
            time.sleep(.01)
    wait(out/"ready.json")
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
    command("z",op="finish");thread.join(2)
    assert not thread.is_alive() and not errors
print("Local motion uses bounded bilateral native joint actions; unreachable target executes zero steps")
with tempfile.TemporaryDirectory() as d:
    out=Path(d);env=Env()
    def reject_when_ready():
        while not (out/'ready.json').exists():time.sleep(.01)
        target=out/'commands'/'pressure.json'
        temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps({'id':'pressure','op':'move_local','arms':{},'steps':1}))
        temporary.replace(target)
    sender=threading.Thread(target=reject_when_ready);sender.start()
    with patch('server_loop.check_configured_storage',side_effect=StoragePressure('full')):
        try:run_server(env,out,[20],'frozen','classify_objects_by_language')
        except StoragePressure:pass
        else:raise AssertionError('Native action proceeded under storage pressure')
    sender.join(2)
    assert not env.actions
print('Storage pressure rejects before native action execution')
