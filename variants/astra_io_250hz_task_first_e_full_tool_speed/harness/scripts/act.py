"""Compact Cartesian CLI; prints the observed result rather than the full trace."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
p=argparse.ArgumentParser()
p.add_argument('episode',type=Path)
p.add_argument('id')
p.add_argument('--arm',choices=['left','right'],default='right')
p.add_argument('--xyz',type=float,nargs=3)
p.add_argument('--quat',type=float,nargs=4)
p.add_argument('--delta',type=float,nargs=3)
p.add_argument('--grip',type=float,required=True)
p.add_argument('--steps',type=int,default=10)
p.add_argument('--settle',type=int,default=2)
a=p.parse_args()
spec={'gripper':a.grip}
for key,value in [('position',a.xyz),('quaternion',a.quat),('delta',a.delta)]:
 if value is not None: spec[key]=value
command=dict(op='move',id=a.id,steps=a.steps,settle=a.settle,arms={a.arm:spec})
r=subprocess.run([sys.executable,str(Path(__file__).with_name('command.py')),str(a.episode),json.dumps(command)],check=True,capture_output=True,text=True)
x=json.loads(r.stdout)
print(json.dumps({k:x[k] for k in ('step','ended','budget')}))
print('ee',x['state'][a.arm+'_ee_pose'],'grip',x['state'][a.arm+'_ee_joint_state'])
print('measured_finger_joints',x['state'][a.arm+'_gripper_measured_joints'])
