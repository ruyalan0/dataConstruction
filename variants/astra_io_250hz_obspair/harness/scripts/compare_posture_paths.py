"""Compare ideal local-IK paths to one fixed wrist goal from recorded arm states."""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from egg_harness.kinematics import Kinematics

p=argparse.ArgumentParser()
p.add_argument('episode',type=Path)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if a.output.exists():raise FileExistsError(a.output)
m=Kinematics(json.loads((Path(__file__).resolve().parents[1]/'configs/x5-kinematics.json').read_text()))
goal=np.array([-.055,-.108,.922,np.sqrt(.5),0,np.sqrt(.5),0])
rows=[]
for step in (993,1002,1027,1031):
    state=json.loads((a.episode/'frames'/f'step-{step:04d}.json').read_text())['state']
    for arm in ('left','right'):
        for duration in (8,16,32):
            joints=np.asarray(state[arm+'_arm_joint_state'])
            initial=np.asarray(state[arm+'_ee_pose'])
            measured=initial.copy()
            base=m.observed_base(joints,initial)
            rotations=Slerp([0,1],Rotation.from_quat(np.array([initial[3:],goal[3:]])[:,[1,2,3,0]]))
            record=dict(step=step,arm=arm,duration=duration,completed_steps=0,max_joint_increment=0.,status='solved')
            for i in range(1,duration+1):
                t=i/duration
                target=np.r_[initial[:3]*(1-t)+goal[:3]*t,rotations(t).as_quat()[[3,0,1,2]]]
                try:solution=m.solve_local(joints,measured,target)
                except ValueError as error:
                    record.update(status='rejected',error=str(error));break
                record['max_joint_increment']=max(record['max_joint_increment'],float(np.max(np.abs(solution-joints))))
                joints=solution
                achieved=base@m.forward(joints)
                measured=np.r_[achieved[:3,3],Rotation.from_matrix(achieved[:3,:3]).as_quat()[[3,0,1,2]]]
                record['completed_steps']=i
            record['final_position_error_m']=float(np.linalg.norm(measured[:3]-goal[:3]))
            rows.append(record)
report=dict(goal=goal.tolist(),rows=rows,scope='Ideal FK-updated paths with fixed measured base and unchanged0.15rad increment limit; no dynamics, collisions, gripper geometry or object manipulation. Different starting arm configurations, not a causal physical experiment.')
a.output.write_text(json.dumps(report,indent=2))
for row in rows:print(json.dumps(row))
