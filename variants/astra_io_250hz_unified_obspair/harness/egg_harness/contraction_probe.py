"""Bounded development component probe; no object/task success claim."""
import argparse
import json
import time
import traceback
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from .client import Robot
from .kinematics import Kinematics
from .contraction import solve_contracted
from .motion_evidence import executed_motion,target_for_arm


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--episode',type=Path,required=True);p.add_argument('--trace',type=Path,required=True)
    args=p.parse_args();args.trace.mkdir(exist_ok=False)
    result={'status':'probe_failed','not_task_success':True,'model_calls':0}
    started=time.monotonic();robot=None
    try:
        robot=Robot(args.episode,args.trace)
        if robot.obs['step']!=0:raise ValueError('Fresh development scene required')
        model=Kinematics(json.loads((Path(__file__).resolve().parents[1]/'configs/x5-kinematics.json').read_text()))
        state=robot.obs['state'];targets={};specs={}
        # Predetermine a nearby same-branch rotation requiring more than one bounded joint increment.
        for arm,sign in (('left',1),('right',-1)):
            q=np.asarray(state[arm+'_arm_joint_state']);candidate=q.copy();candidate[0]+=sign*.24
            matrix=model.observed_base(q,state[arm+'_ee_pose'])@model.forward(candidate)
            target=np.r_[matrix[:3,3],Rotation.from_matrix(matrix[:3,:3]).as_quat()[[3,0,1,2]]]
            if np.linalg.norm(target[:3]-state[arm+'_ee_pose'][:3])>.08:raise ValueError('Probe exceeds predeclared 8cm envelope')
            targets[arm]=target;specs[arm]={'pose':target.tolist(),'gripper':1}
        planned=solve_contracted(model,state,targets,{'left','right'})
        if planned['scale'] not in (.5,.25,.125):raise ValueError('Probe does not exercise contraction at initial posture')
        (args.trace/'planned.json').write_text(json.dumps({'scale':planned['scale'],'targets':{a:t.tolist() for a,t in targets.items()},'attempts':planned['attempts']},indent=2))
        robot.command('move_local',arms=specs,steps=1,settle=0,contract_local_ik=True,guards=[{'arm':a,'tracking_error_m':.035,'watch_grip':False,'opening_change':.003} for a in specs])
        if robot.obs['step']!=1:raise RuntimeError('Expected exactly one contracted native step')
        evidence=executed_motion(args.episode,robot.last_command_id,0,1)
        if not evidence['contracted']:raise RuntimeError('No executed contraction')
        metrics={}
        for arm in specs:
            actual=np.asarray(robot.obs['state'][arm+'_ee_pose']);pos,quat=target_for_arm(evidence,arm,state[arm+'_ee_pose'],1)
            position_error=float(np.linalg.norm(actual[:3]-pos))
            angle=float((Rotation.from_quat(actual[[4,5,6,3]])*Rotation.from_quat(quat[[1,2,3,0]]).inv()).magnitude()*180/np.pi)
            metrics[arm]={'position_error_m':position_error,'rotation_error_deg':angle,'achieved_m':float(np.linalg.norm(actual[:3]-state[arm+'_ee_pose'][:3]))}
            if position_error>.01 or angle>3:raise RuntimeError('Physical tracking exceeds probe thresholds: '+arm)
        result.update(status='probe_completed',metrics=metrics,evidence=evidence)
    except BaseException:
        result['error']=traceback.format_exc();raise
    finally:
        result.update(seconds=time.monotonic()-started,steps=robot.obs['step'] if robot else None)
        (args.trace/'policy-result.json').write_text(json.dumps(result,indent=2))
        if robot is not None:robot.command('finish')


if __name__=='__main__':main()
