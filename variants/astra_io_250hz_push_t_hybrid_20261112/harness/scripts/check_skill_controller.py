"""Verify actuator bounds and retained-session identity without GPU/API."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from egg_harness.skill_policy import validate
from egg_harness.client import Robot
obs={'state':{'left_ee_pose':[0,0,1,1,0,0,0]},'ended':False}
validate({'arm':'left','position':[.02,0,1],'steps':6},'fine',obs)
for action in ({'arm':'left','position':[.03,0,1],'steps':6},{'arm':'left','quaternion':[0,0,0,1],'steps':6},{'arm':'left','steps':7}):
    try:validate(action,'fine',obs)
    except ValueError:pass
    else:raise AssertionError('Unsafe fine segment accepted')
with tempfile.TemporaryDirectory() as d:
    folder=Path(d);home={'left_ee_pose':[1]*7,'right_ee_pose':[2]*7,'left_arm_joint_state':[0]*6,'right_arm_joint_state':[0]*6}
    (folder/'initial-state.json').write_text(json.dumps(home))
    original=Robot.command
    def observe(self,*args,**kw):return obs
    Robot.command=observe
    a=Robot(folder,folder);b=Robot(folder,folder)
    Robot.command=original
    assert a.session!=b.session and a.home['left']==home['left_ee_pose']
print('Fine motion bounds, recovery session identity and original home passed')
