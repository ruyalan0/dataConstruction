"""Offline public-geometry recovery sweeps; never a collision or grasp verdict."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
from egg_harness.kinematics import Kinematics
from egg_harness.posture_action import upright_joint_target, posture_increment
from egg_harness.posture_recovery import points


def evaluate(model, observation, arm, budget):
    state=json.loads(observation.read_text())['state']
    target=upright_joint_target(model,state[arm+'_arm_joint_state'],state[arm+'_ee_pose'])
    initial=np.asarray(state[arm+'_ee_pose'][:3])
    geometry=[];wrists=[];steps=0
    for _ in range(budget+1):
        geometry.append(points(model,state[arm+'_arm_joint_state'],state[arm+'_ee_pose']))
        wrists.append(state[arm+'_ee_pose'][:3])
        if abs(state[arm+'_arm_joint_state'][1]-target[1])<.01 or steps==budget:break
        joints,poses=posture_increment(model,state,{arm:target})
        state[arm+'_arm_joint_state']=joints[arm].tolist();state[arm+'_ee_pose']=poses[arm].tolist();steps+=1
    geometry=np.concatenate(geometry);wrists=np.asarray(wrists)
    low=geometry.min(axis=0)-.03;high=geometry.max(axis=0)+.03
    return {'observation':str(observation),'arm':arm,'steps':steps,
        'target_reached':bool(abs(state[arm+'_arm_joint_state'][1]-target[1])<.01),
        'minimum_geometry_height_m':float(geometry[:,2].min()),
        'minimum_wrist_height_m':float(wrists[:,2].min()),
        'maximum_wrist_travel_from_start_m':float(np.linalg.norm(wrists-initial,axis=1).max()),
        'required_point_envelope_with_margin':[low.tolist(),high.tolist()],
        'fits_one_metre_box':bool(np.all(high-low<=1.)),
        'warning':'Envelope is required geometric extent, NOT observed free space. No mesh/contact/collision or task-progress verification.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--observations',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=32);a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    if not 1<=a.workers<=64:raise ValueError('workers must be1..64')
    model=Kinematics(json.loads((Path(__file__).resolve().parents[1]/'configs/x5-kinematics.json').read_text()))
    tasks=[(f,arm) for f in a.observations for arm in ('left','right')]
    with ThreadPoolExecutor(max_workers=a.workers) as pool:rows=list(pool.map(lambda item:evaluate(model,*item,40),tasks))
    result={'scope':'Recorded development public state, ideal shoulder-only FK; no physical execution and no frozen scene inputs','rows':rows}
    a.output.write_text(json.dumps(result,indent=2));print(json.dumps(result))

if __name__=='__main__':main()
