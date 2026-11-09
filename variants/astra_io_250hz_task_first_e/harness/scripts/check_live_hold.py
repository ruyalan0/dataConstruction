"""Exercise native-service IPC hold/diagnostic separation with a fake environment."""
import json
import tempfile
import threading
import time
from pathlib import Path
import numpy as np
from motion_server_fixture import run_server
class Layout:
    def get_labels_by_prefix(self,*args):return ["egg_holder"]
    def get_instance_pose(self,*a,**kw):return [0,0,0],[1,0,0,0]
    def get_instance_name(self,*a):return 'holder'
    def get_scene_object(self,*a):return self
    def get_all_joints_info(self):return {'lid':{'position':0}}
class Env:
    step_lim=200
    take_action_cnt=[0]
    robot_manager=type('RobotManager',(),{'robot_list':[], 'solve_ik':lambda self,**kw:{'status':'Fail'}})()
    scene_manager=type('Scene',(),{'layout_manager':Layout()})()
    def get_obs(self):return {'state':{'left_ee_pose':[0]*7},'vision':{'head':{'color':np.zeros((4,4,3),np.uint8)}},'instruction':'test'}
    def is_episode_end(self):return False
with tempfile.TemporaryDirectory() as d:
    out=Path(d);errors=[]
    def run():
        try:run_server(Env(),out,[0],'development','fill_egg_holder')
        except BaseException as e:errors.append(e)
    thread=threading.Thread(target=run,daemon=True);thread.start()
    def wait(path):
        start=time.monotonic()
        while not path.exists():
            if errors:raise errors[0]
            if time.monotonic()-start>5:raise TimeoutError(str(path))
            time.sleep(.02)
    wait(out/'ready.json')
    result=Env.robot_manager.solve_ik(robot=type('Robot',(),{'arm_name':'left_arm'})(),target_pose=[0]*7)
    assert result=={'status':'Fail'}
    assert json.loads((out/'developer-only/ik.jsonl').read_text())['result']==result
    (out/'developer-snapshot.request').touch()
    wait(out/'developer-only/failure-0.json')
    assert thread.is_alive() and not (out/'actions.jsonl').exists()
    for key,op in [('resume','observe'),('done','finish')]:
        path=out/'commands'/f'{key}.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps({'id':key,'op':op}));tmp.replace(path)
        response=out/'responses'/path.name;wait(response)
        assert set(json.loads(response.read_text()))=={'step','state','cameras','instruction','ended','budget','camera_calibration_id'}
    thread.join(2);assert not thread.is_alive() and not errors
print('Live hold retains zero-step state, diagnostics isolated, resume/finish works')
