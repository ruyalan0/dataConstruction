"""Audit a fixed direct-posture prefix from recorded public state."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from egg_harness.kinematics import Kinematics
from egg_harness.posture_action import posture_increment,upright_joint_target
from posture_execution_checks import check_record
from egg_harness.posture_recovery import validate_recovery, plan_recovery, check_envelope

parser=argparse.ArgumentParser()
parser.add_argument('episode',type=Path)
parser.add_argument('--through',type=int,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
if args.output.exists() or args.through<1:raise ValueError('New output and positive prefix required')
model=Kinematics(json.loads((Path(__file__).resolve().parents[1]/'configs/x5-kinematics.json').read_text()))
def state(step):
    name='initial' if step==0 else 'step-%04d'%step
    return json.loads((args.episode/'frames'/(name+'.json')).read_text())['state']
rows=[json.loads(line) for line in (args.episode/'actions.jsonl').read_text().splitlines() if json.loads(line)['step']<=args.through]
if [r['step'] for r in rows]!=list(range(1,args.through+1)):raise ValueError('Incomplete prefix')
targets={};report=[];recoveries={}
for row in rows:
    if row.get('primitive')!='posture':continue
    before=state(row['step']-1);after=state(row['step'])
    command=json.loads((args.episode/'commands'/(row['command']+'.json')).read_text())
    recovery = command.get('recover_posture')
    if recovery is not None:
        validate_recovery({'recover_posture':recovery},'transit')
        selected = {recovery['arm']}
        check_envelope(model,before,recovery['arm'],recovery['clearance_box'])
        commanded = dict(before)
        commanded[recovery['arm']+'_arm_joint_state']=row['action'][recovery['arm']+'_arm_joint_state']
        commanded[recovery['arm']+'_ee_pose']=row['cartesian_target'][recovery['arm']+'_ee_pose']
        check_envelope(model,commanded,recovery['arm'],recovery['clearance_box'])
        if row['command'] not in recoveries:
            plan=plan_recovery(model,before,recovery)
            recorded=json.loads((args.episode/(row['command']+'-recovery-plan.json')).read_text())
            if recorded['planned_steps']!=plan['planned_steps'] or not np.allclose(recorded['target'],plan['target']):raise ValueError('Recovery plan mismatch')
            recoveries[row['command']]={'start_step':row['step']-1,'planned_steps':plan['planned_steps'],'executed_steps':0}
        recoveries[row['command']]['executed_steps']+=1
        if recoveries[row['command']]['executed_steps']>recoveries[row['command']]['planned_steps']:raise ValueError('Recovery exceeded planned prefix')
    else:
        selected=set(command['posture'])
    previous_grip={a:rows[row['step']-2]['action'][a+'_ee_joint_state'] if row['step']>1 else [1.] for a in ('left','right')}
    independent=check_record(model,before,after,row,selected,previous_grip)
    if row['command'] not in targets:
        targets[row['command']]={a:upright_joint_target(model,before[a+'_arm_joint_state'],before[a+'_ee_pose']) for a in selected}
    q,poses=posture_increment(model,before,targets[row['command']])
    for arm in q:
        if not np.allclose(q[arm],row['action'][arm+'_arm_joint_state'],atol=1e-10):raise ValueError('Joint target mismatch')
        if not np.allclose(poses[arm],row['cartesian_target'][arm+'_ee_pose'],atol=1e-8):raise ValueError('FK target mismatch')
        report.append({'step':row['step'],'arm':arm,**independent[arm],'joint_delta_rad':float(np.max(np.abs(q[arm]-before[arm+'_arm_joint_state']))),
            'wrist_tracking_m':float(np.linalg.norm(np.asarray(after[arm+'_ee_pose'][:3])-poses[arm][:3]))})
if not report:raise ValueError('No executed posture steps')
for command, entry in recoveries.items():
    response_path=args.episode/'responses'/(command+'.json')
    if not response_path.exists():
        entry['complete_response_in_prefix']=False
        continue
    response=json.loads(response_path.read_text())
    if response['step']>args.through:
        entry['complete_response_in_prefix']=False
        continue
    feedback=response['posture_recovery']
    if feedback['executed_steps']!=entry['executed_steps'] or feedback['next_motion_verified'] is not False:raise ValueError('Invalid recovery outcome claim')
    if feedback['status'] not in ('budget_exhausted','target_reached','episode_ended','clearance_envelope_exit','tracking_or_gripper_change'):raise ValueError('Unknown recovery outcome')
    entry['complete_response_in_prefix']=True
    entry['status']=feedback['status']
record={'through':args.through,'rows':report,'prefix_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),'scope':'Recorded joint/FK equality plus independent joint, downstream-point, upward-direction and retained-grip limits; no collision or grasp-success claim'}
record['recovery_commands']=recoveries
args.output.write_text(json.dumps(record,indent=2))
print(json.dumps(record))
