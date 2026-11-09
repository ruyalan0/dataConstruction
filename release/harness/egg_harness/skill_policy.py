"""Astra chooses every bounded segment from RGB, proprioception and frozen skills."""
import argparse
import hashlib
import json
import time
import traceback
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from .client import Robot
from .geometry import unproject
from .multiview import MultiViewGeometry, DEFAULT_CALIBRATION
from .active_vision import locate_temporal
from .native_responses import NativeToolVision as Vision
from .segments import compile_segment
from .action_control import action_interface
from .bilateral import compile_motion
from .motion_evidence import executed_motion, target_for_arm
from .kinematics import Kinematics
from .posture import posture_context
from .posture_action import validate_posture_action,posture_preview
from .posture_recovery import validate_recovery, plan_recovery
from .joint_control import validate_joint_action, validate_unfold, joint_semantics, joint_preview, plan_unfold

SYSTEM = """You are Astra controlling a physical robot in RoboDojo. Use only supplied RGB,
robot state and explicit skills. Decide the next action yourself; review the previous
segment before moving. Images show CURRENT state and the previous segment's head and
active-wrist views, with their step and robot state. Compare finger-relative object
motion against background motion. ee_joint_state is the command echo, NOT measured
opening. gripper_measured_joints are measured finger joints in metres, not contact
sensors. Explain retention evidence and uncertainty while carrying an object.
Use a supplied native function tool. Its assessment, plan, phase and memory fields
carry your brief decision summary. The existing action semantics are described below
as {assessment:string, plan:string, phase:'fine'|'transit',
action:{arm:'left'|'right',position:[x,y,z],quaternion:[w,x,y,z],gripper:0..1,
steps:integer,settle:integer},memory:string}. Position/quaternion/gripper are optional
hold commands. Alternatively action:{stop:true,reason:string} preserves the scene.
Fine segments: <=0.025m wrist displacement, <=12deg rotation, 1..6 steps, settle0..2.
Transit: <=0.30m, <=90deg, 1..20 steps, settle0..2; only in visibly clear space.
Choose fine near objects, fixtures, contact surfaces or after tracking error. Each action returns
fresh images before your next decision. A segment may return early on >35mm tracking
error or >0.003m measured finger change during an established closed grip. Review actual state;
remaining target motion has NOT executed. This guard is not a grasp detector.
Quaternion is world wxyz; translations meters.
Prefer action:{arm, toward:{position:[x,y,z],quaternion:[w,x,y,z]},steps,settle,gripper}
for pose goals. toward executes ONLY one bounded straight/spherical prefix from the
current measured wrist pose: fine <=24mm/11deg, transit <=290mm/85deg. Its quaternion
is explicitly normalized (norm must be .5..1.5). It never loops to complete the goal.
Observe the result and choose again; a prefix is not an obstacle-avoiding path. Omitted
toward pose components hold the current measured component. Direct pose fields remain
strict exact segment requests and cannot be mixed with toward.
For simultaneous motion use action:{arms:{left:{toward:{position,quaternion},gripper},
right:{toward:{position,quaternion},gripper}},steps,settle}. Both arms share one phase
and duration; each has its own bounded target and measured tracking/grip guard. Either
guard stops the whole segment. Use this for visibly separated paths; account for both
hands and held objects sweeping through space. Inspect both current/previous wrist
views afterward. Omit an arm to hold it; single-arm actions remain supported.
You may request action:{project:[{pixel:[u,v],z:number},...]} for head RGB ray-plane
intersections without moving. These are approximate inferred geometry, not object truth.
Use triangulate_point when the same identifiable feature is visible in two or three
CURRENT camera images and its height is uncertain. Supply the current step and raw
640x480 pixels for the same physical point (a corner/mark), not independently chosen
silhouette centers. The program uses wrist poses and fixed camera calibration to
estimate XYZ. Inspect reprojection error and pixel-noise sensitivity; neither proves
feature identity or grasp accuracy. A rejection means obtain better corresponding
views, using ordinary bounded motion only when visibly safe. Keep triangulate_point
limited to simultaneous views. For temporal depth with ONE wrist camera, explicitly
make ordinary bounded TCP observation moves with an empty open hand through visible
free space, keeping the other arm and both grippers unchanged and the target static
and uncontacted. Lateral camera translation supplies parallax; commanded displacement
is not measured displacement. Then review_frames for 2–3 steps of that same wrist
camera (strictly increasing, ending at current step, span<=24 native steps), and use
triangulate_temporal_point on the named reviewed images in the next request. It checks
actual recorded camera poses and correspondence geometry, executes zero actions,
and reports depth conditional on the static-target assumption. Explain independent
visual evidence for stationarity/empty hand; low reprojection error alone is not
proof. Moving/held targets and changing silhouettes violate the correspondence.
The existing 10deg parallax and 2px-noise sensitivity checks still apply: one short
move may not provide enough baseline. Acquire additional bounded views only when
safe and worthwhile within the task budget; do not weaken checks to obtain a number.
Include compact episode memory: held object, target pixels/derived estimates, completed
slots, last result, uncertainty. Never declare native success; stop only if blocked.
Complete the supplied runtime instruction and all public task conditions. Return to
the observed original home when the public task requires reset. Plan within the native
step budget and reserve enough actions for task completion and any required reset.
At visually completed home, action:{verify:true} advances six hold steps per observation
until native end. Skills below are evidence-qualified guidance, not a fixed trajectory.
"""


JOINT_INSTRUCTIONS = """Use the supplied control_arms native tool for motion. Select left and/or right;
null arms hold. Each joint arm has control:"joint", six ABSOLUTE joint targets in
radians, and continuous gripper opening0..1; null joints/gripper hold. Both arms and
both grippers may change in the same action, including while carrying an object.
There is no empty-hand or preview prerequisite for this full joint interface.
Fine1..6 steps; transit1..20 steps only through visibly clear space; settle0..2.
Every joint goal change must be<=0.15*steps radians. Linear interpolation is checked
against each measured state at<=0.15rad/joint/native step; lag may interrupt a prefix.
Each joint's limits_rad in joint_semantics gives its angle limits. Plan commanded
targets at least0.02rad inside each limit: for joint6 limits[-3.14,3.14], select
targets within[-3.12,3.12]. This is a planning margin; physical feedback may slightly
overshoot a target. The reported limits and measured angles remain unchanged.
Wrist position and orientation change with your joint choices. No implicit IK,
Cartesian goal conversion, alternate branches or automatic unfolding is performed.
A segment stops on wrist tracking error>35mm or measured opening change>3mm during
an established closed grip. These are tracking checks, not collision/grasp detectors.
Use public joint_semantics and home_joints, current/previous three-view RGB and actual
wrist poses to plan small joint changes and inspect swept space for arms/held objects.
Skills may describe desired TCP geometry: achieve it using available joint actions;
only explicitly enabled tools are callable. At task completion return according to
the public task's reset condition, using observed home poses and home_joints.
"""


def system_prompt(interface):
    if interface == 'tcp':
        return SYSTEM
    if interface == 'both':
        return SYSTEM + '\nBoth TCP and native joints are enabled. control_arms may choose control independently per arm. The following bounds apply to JOINT arms only:\n' + JOINT_INSTRUCTIONS
    if interface != 'joint':
        raise ValueError('Unknown action interface')
    prefix = SYSTEM[:SYSTEM.index('Use a supplied native function tool.')]
    vision = SYSTEM[SYSTEM.index('You may request action:{project:'):]
    vision = vision.replace('ordinary bounded TCP observation moves', 'bounded joint observation moves')
    return prefix + JOINT_INSTRUCTIONS + vision


def bind_joint_preview(pending, step, primitive, options, preview_id):
    """Bind execution to the latest unchanged preview before sending any IPC."""
    if not isinstance(preview_id, str) or len(preview_id) != 64 or any(char not in '0123456789abcdef' for char in preview_id):
        raise ValueError('Invalid preview token; copy the latest returned preview ID')
    if pending is None or pending['step'] != step or pending['primitive'] != primitive or pending['options'] != options or pending['preview_id'] != preview_id:
        raise ValueError('Execution must copy the latest matching preview ID and unchanged specification; request a fresh preview')
    return {**options, 'preview_id': preview_id}


def validate(action, phase, obs):
    if phase not in ('fine','transit'):raise ValueError('Invalid control phase')
    if set(action)-{'arm','position','quaternion','gripper','steps','settle'}:raise ValueError('Unknown action fields')
    arm=action['arm']
    if arm not in ('left','right'):raise ValueError('Unknown arm')
    pose=np.asarray(obs['state'][arm+'_ee_pose'],float)
    pos=np.asarray(action.get('position',pose[:3]),float)
    quat=np.asarray(action.get('quaternion',pose[3:]),float)
    if pos.shape!=(3,) or quat.shape!=(4,) or not np.isfinite(np.r_[pos,quat]).all():raise ValueError('Invalid pose')
    if abs(np.linalg.norm(quat)-1)>.003:raise ValueError('Quaternion not unit')
    distance=float(np.linalg.norm(pos-pose[:3]))
    angle=float((Rotation.from_quat(quat[[1,2,3,0]])*Rotation.from_quat(pose[[4,5,6,3]]).inv()).magnitude()*180/np.pi)
    max_distance,max_angle,max_steps=(.025,12,6) if phase=='fine' else (.30,90,20)
    if distance>max_distance+1e-6 or angle>max_angle+1e-3:raise ValueError(f'{phase} segment exceeds bounds: {distance:.4f}m {angle:.2f}deg')
    steps=action['steps'];settle=action.get('settle',0)
    if type(steps)!=int or not 1<=steps<=max_steps or type(settle)!=int or not 0<=settle<=2:raise ValueError('Invalid duration')
    if 'gripper' in action and not 0<=action['gripper']<=1:raise ValueError('Invalid gripper')
    return arm,pos,quat,steps,settle


def temporal_inputs(obs, previous):
    images=dict(obs['cameras'])
    if previous is None:return images,None
    images['previous_head']=previous['cameras']['cam_head']
    arm=previous['arm']
    arms=arm if isinstance(arm,list) else ([] if arm is None else [arm])
    for name in arms:images['previous_'+name+'_wrist']=previous['cameras']['cam_'+name+'_wrist']
    return images,{'step':previous['step'],'state':previous['state'],'active_arm':arm}


def gripper_trace(episode, start, end, arm):
    samples=[]
    for step in range(start+1,end+1):
        frame=json.loads((episode/'frames'/f'step-{step:04d}.json').read_text())
        samples.append({'step':step,'measured_finger_m':float(frame['state'][arm+'_gripper_measured_joints'][0]),'command_echo':float(frame['state'][arm+'_ee_joint_state'][0])})
    return samples


def executed_target(before_pose, position, quaternion, executed_steps, requested_steps):
    if not 0 <= executed_steps or requested_steps < 1:
        raise ValueError('Invalid executed duration')
    fraction=min(executed_steps/requested_steps,1.0)
    xyz=np.asarray(before_pose[:3])*(1-fraction)+np.asarray(position)*fraction
    rotations=Rotation.from_quat(np.asarray([before_pose[3:],quaternion])[:,[1,2,3,0]])
    quat=Slerp([0,1],rotations)(fraction).as_quat()[[3,0,1,2]]
    return xyz,quat


def execute_bilateral(robot,motion_op,compiled,steps,settle,commanded_grip,phase,contract_local_ik=False):
    before=robot.obs
    guards=[]
    for arm,item in compiled.items():
        grip=item["action"].get("gripper",commanded_grip[arm])
        guards.append({"arm":arm,"tracking_error_m":.035,"watch_grip":grip==0 and commanded_grip[arm]==0,"opening_change":.003})
    options={'contract_local_ik':True} if contract_local_ik else {}
    robot.command(motion_op,arms={arm:{k:v for k,v in item["action"].items() if k in ("position","quaternion","gripper")} for arm,item in compiled.items()},steps=steps,settle=settle,label="astra_"+phase,guards=guards,**options)
    elapsed=robot.obs["step"]-before["step"];feedback={"steps":elapsed,"arms":{}}
    if contract_local_ik:
        evidence=executed_motion(robot.episode,robot.last_command_id,before['step'],robot.obs['step'])
        feedback['local_ik_contraction']={k:v for k,v in evidence.items() if k!='targets'}
    if elapsed==0 and not robot.obs["ended"]:feedback["rejected_before_motion"]="Local IK rejected one arm; neither arm executed. Revise the approach."
    for arm,item in compiled.items():
        pose=np.asarray(before["state"][arm+"_ee_pose"]);actual=np.asarray(robot.obs["state"][arm+"_ee_pose"])
        pos,q=target_for_arm(evidence,arm,pose,elapsed) if contract_local_ik else executed_target(pose,item["position"],item["quaternion"],elapsed,steps)
        residual=float(np.linalg.norm(actual[:3]-pos))
        rotation=float((Rotation.from_quat(actual[[4,5,6,3]])*Rotation.from_quat(q[[1,2,3,0]]).inv()).magnitude()*180/np.pi)
        feedback["arms"][arm]={"wrist_error_m":residual,"rotation_error_deg":rotation,"requires_fine_recovery":residual>.025 or rotation>12,"goal_compilation":item["goal"],"achieved_displacement":(actual[:3]-pose[:3]).tolist(),"unexecuted_target_distance_m":float(np.linalg.norm(item["position"]-pos)),"interrupted_before_requested_end":elapsed<steps+settle and not robot.obs["ended"],"gripper_opening_by_step":gripper_trace(robot.episode,before["step"],robot.obs["step"],arm)}
        if elapsed>0 and "gripper" in item["action"]:commanded_grip[arm]=item["action"]["gripper"]
    return feedback


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--episode',type=Path,required=True);p.add_argument('--trace',type=Path,required=True);a=p.parse_args()
    config=json.loads(a.config.read_text());interface=action_interface(config);a.trace.mkdir(parents=True,exist_ok=False)
    for flag in ('contract_local_ik','public_posture_feedback','direct_posture_action','posture_recovery_program','joint_control'):
        if type(config.get(flag,False)) is not bool:raise ValueError(flag+' must be boolean')
    motion_op=config.get("motion_op", "move")
    if motion_op not in ("move", "move_local"):raise ValueError("Unsupported motion controller")
    contract=config.get('contract_local_ik',False) if interface in ('tcp','both') else False
    if type(contract) is not bool or (contract and motion_op!='move_local'):raise ValueError('Invalid contraction configuration')
    posture_enabled=config.get('public_posture_feedback',False) or interface in ('joint','both')
    direct_posture=config.get('direct_posture_action',False) if interface=='both' else False
    recovery_enabled=config.get('posture_recovery_program',False) if interface=='both' else False
    joint_enabled=config.get('joint_control',False) if interface=='both' else False
    if type(joint_enabled) is not bool or (joint_enabled and not posture_enabled):raise ValueError('Joint control requires public posture feedback')
    if type(recovery_enabled) is not bool or (recovery_enabled and not direct_posture):raise ValueError('Recovery program requires direct posture')
    if type(direct_posture) is not bool or (direct_posture and not posture_enabled):raise ValueError('Direct posture requires public posture feedback')
    if type(posture_enabled) is not bool:raise ValueError('Invalid posture feedback configuration')
    posture_model=Kinematics(json.loads((Path(__file__).resolve().parents[1]/'configs/x5-kinematics.json').read_text())) if posture_enabled else None
    legacy_skills={'direct-posture-adjustment','multijoint-escape','folded-right-recovery-case'}
    skill_names=[name for name in config['skill_names'] if interface=='both' or name not in legacy_skills]
    (a.trace/'action-interface.json').write_text(json.dumps({'action_interface':interface,'disabled_legacy_skills':sorted(set(config['skill_names'])-set(skill_names))},indent=2))
    files=[Path(config['skills_root'])/name/'SKILL.md' for name in skill_names]
    if not files:raise ValueError('No executable skills')
    skills=chr(10).join(f.read_text() for f in files)
    calibration=json.loads(Path(config['memory']).read_text())['head_camera']
    multiview=MultiViewGeometry(config.get('multiview_calibration', DEFAULT_CALIBRATION))
    (a.trace/'skills.json').write_text(json.dumps({str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files},indent=2))
    robot=Robot(a.episode,a.trace)
    if robot.obs.get('action_interface') != interface:
        raise ValueError('Policy/server action interface mismatch; start a matching server')
    model=Vision(config,a.trace/'model')
    memory='Fresh controller. Infer current scene; it may be a preserved failure scene.'
    commanded_grip={'left':1.0,'right':1.0}
    actions_path=a.episode/'actions.jsonl'
    if actions_path.exists():
        last=json.loads(actions_path.read_text().splitlines()[-1])['action']
        commanded_grip={arm:float(last[arm+'_ee_joint_state'][0]) for arm in ('left','right')}
    recent=[];stalls=[]
    feedback=None;previous=None;started=time.monotonic();status='error';errors=0
    pending_joint_preview=None
    review_images={};review_records=[]
    if config.get('continuation_request'):
        from .continuation_context import load_context
        restored=load_context(config['continuation_request'],a.episode,robot.obs['step'])
        memory=restored['memory']+' Reconnected after model interruption. Verify this prior memory against current RGB before acting.'
        feedback=restored['feedback'];recent=restored['recent'];stalls=restored['stalls']
        previous=restored['previous']
        (a.trace/'restored-context.json').write_text(json.dumps(restored,indent=2))
    try:
        while True:
            model.observe(feedback, robot.obs['step'], robot.obs['ended'])
            if robot.obs['ended']:break
            if model.calls>=config['max_model_calls']:raise TimeoutError('Model call budget exhausted')
            images,previous_state=temporal_inputs(robot.obs,previous)
            images.update(review_images)
            context={'step':robot.obs['step'],'state':robot.obs['state'],'home':robot.home,'instruction':robot.obs['instruction'],'memory':memory,'previous_result':feedback,'recent_executed_segments':recent[-8:],'stalled_segments_this_controller':stalls[-12:],'desired_gripper_commands':commanded_grip,'contact_estimates':{arm:(np.asarray(robot.obs['state'][arm+'_ee_pose'][:3])+Rotation.from_quat(np.asarray(robot.obs['state'][arm+'_ee_pose'])[ [4,5,6,3] ]).apply([.138,0,0])).tolist() for arm in ('left','right')}}
            context['previous_observation']=previous_state
            context['review_capability']='Zero-motion action:{review:[{step:recorded_native_step,camera:cam_head|cam_left_wrist|cam_right_wrist}]};1..6 frames. Historical RGB and original public poses appear on your next decision alongside current views. No future, other-episode or hidden state. Use before declaring past contact unknowable.'
            context['reviewed_observations']=review_records
            context['multiview_geometry']={'step':robot.obs['step'],'calibration_id':multiview.calibration_id,
                'image_coordinates':'Current named RGB frames, raw 640x480 pixels',
                'contract':'One identical physical feature in 2–3 distinct current views; zero-motion triangulate_point. Reported sensitivity assumes 2px independent pixel noise; excludes calibration and mismatches.'}
            context['temporal_geometry']={'reviewed_samples':[{'step':row['step'],'camera':row['camera'],'image':row['image']} for row in review_records],
                'contract':'triangulate_temporal_point uses ONE wrist camera at 2–3 reviewed steps ending now, span<=24. Match one static uncontacted feature; explain stationarity/empty-hand evidence. Actual recorded poses, open camera hand, other arm held, grippers unchanged. Zero motion; static target remains an assumption.'}
            context['action_interface']=interface
            if interface in ('joint','both'):
                context['joint_semantics']=joint_semantics(posture_model,robot.obs['state'])
                context['home_joints']=robot.home_joints
            if posture_model is not None:
                context['arm_posture']=posture_context(posture_model,robot.obs['state'])
                if previous_state is not None:
                    context['previous_arm_posture']=posture_context(posture_model,previous_state['state'])
            if direct_posture:
                context['posture_action']={'syntax':'action:{posture:{left:upright,right:upright},steps:1..6},phase:transit; select one or both arms',
                    'purpose':'Use when configuration obstructs approach, descent or withdrawal. Healthy compact standby can unfold during approach; horizontal link2 alone is not a trigger. Judge recovery by restored motion progress, not tilt reduction.',
                    'conditions':'Only empty selected hands already open, and visually clear swept paths. Shoulder joint2 moves directly; wrist position/orientation are NOT held. Up to0.15rad joint2 and24mm predicted wrist travel per native step; observe after each requested prefix. Use steps1 near uncertainty. No settle, gripper changes or Cartesian fields.',
                    'preview':{a:posture_preview(posture_model,robot.obs['state'],[a],6) for a in ('left','right')}}
            if contract:context['motion_capability']='Local IK tries shared scales1,1/2,1/4,1/8. A contracted native step immediately returns for your observation; remaining motion and settle are not executed. Feedback tracking errors use actual commanded targets. No collision or grasp guarantee.'
            if recovery_enabled:
                context['posture_recovery_program']={'syntax':'phase:transit, action:{recover_posture:{arm:left|right,reason:approach_blocked|descent_blocked|withdrawal_blocked,clearance_box:[[xmin,ymin,zmin],[xmax,ymax,zmax]],max_steps:1..40}}',
                    'contract':'One call runs bounded shoulder recovery with per-step tracking checks. Declare an RGB-supported empty swept-space box in world metres, including downstream origins and fingertip plus 3cm margin. Selected hand must be empty and open. Only use for obstructed next motion. Full prefix is checked before movement; no automatic obstacle avoidance. Return to visual planning to verify next-motion progress.'}
            if joint_enabled:
                context['pending_joint_preview']=pending_joint_preview
                context['joint_control']={
                    'semantics':joint_semantics(posture_model,robot.obs['state']),
                    'preview_delta':'action:{preview_joint_delta:{joint_delta:{left:{joint2:0.1,joint3:-0.1},right:{joint5:0.05}},steps:1..6}},phase:transit',
                    'execute_delta':'After reviewing preview against RGB: action:{joint_delta:SAME_MAP,steps:SAME_STEPS,preview_id:RETURNED_ID},phase:transit',
                    'preview_unfold':'action:{preview_unfold_arm:{arm:left|right,reason:approach_blocked|descent_blocked|withdrawal_blocked,clearance_box:[[xmin,ymin,zmin],[xmax,ymax,zmax]],max_steps:1..24}},phase:transit',
                    'execute_unfold':'After reviewing whole-arm path: action:{unfold_arm:SAME_SPEC,preview_id:RETURNED_ID},phase:transit',
                    'contract':'Selected hands must be visibly empty and already open. Multiple joints and both arms may change simultaneously. Unselected joints and all gripper targets hold their initial measured/commanded state. Deltas radians, cumulative abs<=0.6 per joint; native step<=0.15rad and conservative chain-point sweep<=24mm. Use measured-base previews, never guess mirrored signs. Preview consumes zero native steps; any native motion invalidates it. Test obstructed Cartesian motion after adjustment. Program target_reached is not task recovery; proxy geometry is not mesh collision clearance.'}
            context['budget']={**robot.obs['budget'], 'model_calls_remaining':config['max_model_calls']-model.calls}
            context['public_task_requirements']=config['public_task_requirements']
            decision=model.ask(json.dumps(context),images,system=system_prompt(interface)+chr(10)+skills)
            with (a.trace/'decisions.jsonl').open('a') as f:f.write(json.dumps({'step':robot.obs['step'],'decision':decision})+chr(10))
            print(robot.obs['step'],json.dumps(decision),flush=True)
            memory=decision['memory'];action=decision['action']
            if action.get('stop'):raise RuntimeError('Astra stopped: '+action['reason'])
            presented_review=review_records
            review_images={};review_records=[]
            if 'review' in action:
                from .public_review import review_frames
                try:
                    if set(action)!={'review'}:raise ValueError('Review cannot include motion')
                    review_images,review_records=review_frames(a.episode,robot.obs['step'],action['review'])
                    feedback={'reviewed_frames':review_records,'native_steps_executed':0}
                except (ValueError,KeyError,TypeError,FileNotFoundError) as error:
                    errors+=1;feedback={'rejected_before_motion':str(error)}
                continue
            if 'project' in action:
                feedback={'projections':[unproject(x['pixel'],x['z'],calibration).tolist() for x in action['project']]};continue
            if 'triangulate' in action:
                try:
                    if set(action)!={'triangulate'}:raise ValueError('Triangulation cannot include motion')
                    feedback={'triangulation':multiview.locate(action['triangulate'],robot.obs)}
                except ValueError as error:
                    errors+=1;feedback={'rejected_before_motion':str(error),'tool':'triangulate_point','native_steps_executed':0}
                continue
            if 'triangulate_temporal' in action:
                try:
                    if set(action)!={'triangulate_temporal'}:raise ValueError('Temporal triangulation cannot include motion')
                    feedback={'temporal_triangulation':locate_temporal(multiview,action['triangulate_temporal'],a.episode,
                        robot.obs,presented_review,model.last_images)}
                except (ValueError,KeyError,TypeError,FileNotFoundError) as error:
                    errors+=1;feedback={'rejected_before_motion':str(error),'tool':'triangulate_temporal_point','native_steps_executed':0}
                continue
            if 'control_arms' in action:
                if interface not in ('joint','both') or set(action)!={'control_arms'}:
                    raise ValueError('Native joint tool is not enabled or has mixed action fields')
                previous={**robot.obs,'arm':list(action['control_arms']['arms'])}
                robot.command('control_action',action=action['control_arms'],phase=decision['phase'])
                feedback=robot.obs['action_feedback']
                if feedback['executed_steps']:
                    for arm,spec in action['control_arms']['arms'].items():
                        if 'gripper' in spec:commanded_grip[arm]=spec['gripper']
                errors+=int(feedback['status']=='rejected_before_motion')
                event={'start_step':previous['step'],'end_step':robot.obs['step'],'action':action,'feedback':feedback}
                recent.append(event)
                with (a.trace/'motion-feedback.jsonl').open('a') as f:f.write(json.dumps(event)+chr(10))
                continue
            if action.get('verify'):
                previous={**robot.obs,'arm':None}
                if interface in ('joint','both'):
                    robot.command('control_action',action={'arms':{},'steps':6,'settle':0},phase='fine')
                    feedback={'hold_steps':robot.obs['action_feedback']['executed_steps'],'action_feedback':robot.obs['action_feedback']}
                else:
                    robot.command(motion_op,arms={},steps=6,settle=0,label='model_verify');feedback={'hold_steps':6}
                continue
            if interface=='joint' and not ('review' in action or 'project' in action):
                raise ValueError('Joint-only policy rejects Cartesian and legacy posture actions')
            joint_keys=set(action)&{'preview_joint_delta','preview_unfold_arm','joint_delta','unfold_arm'}
            if joint_keys:
                try:
                    if not joint_enabled:raise ValueError('Joint control not enabled')
                    if len(joint_keys)!=1:raise ValueError('Select exactly one joint operation')
                    kind=next(iter(joint_keys));preview=kind.startswith('preview_')
                    if kind in ('preview_joint_delta','joint_delta'):
                        if preview:
                            if set(action)!={'preview_joint_delta'}:raise ValueError('Unexpected joint preview fields')
                            body=action[kind]
                        else:
                            if set(action)!={'joint_delta','steps','preview_id'}:raise ValueError('Joint execution requires matching preview_id')
                            body={k:action[k] for k in ('joint_delta','steps')}
                        deltas,steps=validate_joint_action(body,decision['phase']);arms=list(deltas)
                        options=dict(body)
                        if preview:joint_preview(posture_model,robot.obs['state'],deltas,steps)
                    else:
                        if set(action)!=({kind} if preview else {'unfold_arm','preview_id'}):raise ValueError('Unexpected unfold fields')
                        spec=validate_unfold({'unfold_arm':action[kind]},decision['phase']);arms=[spec['arm']]
                        options={'unfold_arm':spec}
                        if preview:plan_unfold(posture_model,robot.obs['state'],spec)
                    for arm in arms:
                        if commanded_grip[arm]<.8 or min(robot.obs['state'][arm+'_gripper_measured_joints'])<.035:raise ValueError('Joint control requires selected hands empty and already open')
                    if not preview:
                        options=bind_joint_preview(pending_joint_preview,robot.obs['step'],kind,options,action['preview_id'])
                except (ValueError,KeyError,TypeError) as e:
                    errors+=1;feedback={'rejected_before_motion':str(e)}
                    if errors>=5:raise RuntimeError('Five invalid model actions') from e
                    continue
                before=robot.obs
                robot.command(kind,**options,label='astra_'+kind)
                if 'joint_rejection' in robot.obs:
                    feedback=robot.obs['joint_rejection'];pending_joint_preview=None
                    continue
                feedback=robot.obs['joint_preview' if preview else 'joint_control']
                pending_joint_preview={'step':robot.obs['step'],'primitive':kind.removeprefix('preview_'),'options':options,'preview_id':feedback['preview_id']} if preview else None
                if not preview:
                    previous={**before,'arm':arms}
                    event={'start_step':before['step'],'end_step':robot.obs['step'],'action':action,'feedback':feedback}
                    recent.append(event)
                    with (a.trace/'motion-feedback.jsonl').open('a') as f:f.write(json.dumps(event)+chr(10))
                continue
            if 'recover_posture' in action:
                try:
                    if not recovery_enabled:raise ValueError('Recovery program not enabled')
                    spec=validate_recovery(action,decision['phase'])
                    arm=spec['arm']
                    if commanded_grip[arm]<.8 or min(robot.obs['state'][arm+'_gripper_measured_joints'])<.035:raise ValueError('Recovery requires empty open hand')
                    plan_recovery(posture_model,robot.obs['state'],spec)
                except (ValueError,KeyError,TypeError) as e:
                    errors+=1;feedback={'rejected_before_motion':str(e)}
                    if errors>=5:raise RuntimeError('Five invalid model actions') from e
                    continue
                previous={**robot.obs,'arm':[arm]}
                robot.command('recover_posture',recover_posture=spec,label='astra_posture_recovery')
                feedback=robot.obs['posture_recovery']
                event={'start_step':previous['step'],'end_step':robot.obs['step'],'action':action,'feedback':feedback}
                recent.append(event)
                with (a.trace/'motion-feedback.jsonl').open('a') as f:f.write(json.dumps(event)+chr(10))
                continue
            if 'posture' in action:
                try:
                    if not direct_posture:raise ValueError('Direct posture is not enabled')
                    arms,steps=validate_posture_action(action,decision['phase'])
                    for arm in arms:
                        if commanded_grip[arm]<.8 or min(robot.obs['state'][arm+'_gripper_measured_joints'])<.035:
                            raise ValueError('Open selected hand after supporting/releasing objects before posture motion')
                except (ValueError,KeyError,TypeError) as e:
                    errors+=1;feedback={'rejected_before_motion':str(e)}
                    if errors>=5:raise RuntimeError('Five invalid model actions') from e
                    continue
                previous={**robot.obs,'arm':arms}
                before_posture=posture_context(posture_model,robot.obs['state'])
                robot.command('posture',posture=action['posture'],steps=steps,label='astra_upright')
                feedback={'primitive':'posture','steps':robot.obs['step']-previous['step'],'before':before_posture,'after':posture_context(posture_model,robot.obs['state']),
                    'interrupted_before_requested_end':robot.obs['step']-previous['step']<steps,'wrist_motion':{a:(np.asarray(robot.obs['state'][a+'_ee_pose'][:3])-previous['state'][a+'_ee_pose'][:3]).tolist() for a in arms}}
                feedback['joint_change_rad']={arm:(np.asarray(robot.obs['state'][arm+'_arm_joint_state'])-previous['state'][arm+'_arm_joint_state']).tolist() for arm in arms}
                feedback['gripper_opening_by_step']={arm:gripper_trace(robot.episode,previous['step'],robot.obs['step'],arm) for arm in arms}
                feedback['interruptions']=[]
                interruption_file=robot.episode/'interruptions.jsonl'
                if interruption_file.exists():
                    feedback['interruptions']=[row for row in map(json.loads,interruption_file.read_text().splitlines()) if row['command']==robot.last_command_id]
                event={'start_step':previous['step'],'end_step':robot.obs['step'],'action':action,'feedback':feedback}
                recent.append(event)
                with (a.trace/'motion-feedback.jsonl').open('a') as f:f.write(json.dumps(event)+chr(10))
                continue
            if "arms" in action:
                try:
                    compiled,steps,settle=compile_motion(action,decision["phase"],robot.obs,validate)
                except (ValueError,KeyError,TypeError) as e:
                    errors+=1;feedback={"rejected_before_motion":str(e)}
                    with (a.trace/"rejections.jsonl").open("a") as f:f.write(json.dumps(feedback)+chr(10))
                    if errors>=5:raise RuntimeError("Five invalid model actions") from e
                    continue
                previous={**robot.obs,"arm":list(compiled)}
                feedback=execute_bilateral(robot,motion_op,compiled,steps,settle,commanded_grip,decision["phase"],contract_local_ik=contract)
                event={"start_step":previous["step"],"end_step":robot.obs["step"],"action":action,"feedback":feedback}
                recent.append(event)
                if any(v["requires_fine_recovery"] for v in feedback["arms"].values()):stalls.append(event)
                with (a.trace/"motion-feedback.jsonl").open("a") as f:f.write(json.dumps(event)+chr(10))
                if any(v["wrist_error_m"]>.10 for v in feedback["arms"].values()):raise RuntimeError("Wrist deviation exceeds 10cm")
                continue
            try:
                action, compilation = compile_segment(action, decision['phase'], robot.obs)
                arm,pos,q,steps,settle=validate(action,decision['phase'],robot.obs)
            except (ValueError,KeyError,TypeError) as e:
                errors+=1;feedback={'rejected_before_motion':str(e)}
                with (a.trace/'rejections.jsonl').open('a') as f:f.write(json.dumps(feedback)+chr(10))
                if errors>=5:raise RuntimeError('Five invalid model actions') from e
                continue
            previous={**robot.obs,'arm':arm}
            before=robot.obs['step'];before_pose=np.asarray(robot.obs['state'][arm+'_ee_pose'])
            grip=action.get('gripper',commanded_grip[arm])
            watch_grip=grip==0 and commanded_grip[arm]==0
            options={'contract_local_ik':True} if contract else {}
            robot.command(motion_op,arms={arm:{k:v for k,v in action.items() if k in ('position','quaternion','gripper')}},steps=steps,settle=settle,label='astra_'+decision['phase'],guard={'arm':arm,'tracking_error_m':.035,'watch_grip':watch_grip,'opening_change':.003},**options)
            actual=np.asarray(robot.obs['state'][arm+'_ee_pose'])
            elapsed=robot.obs['step']-before
            if contract:evidence=executed_motion(robot.episode,robot.last_command_id,before,robot.obs['step'])
            if elapsed > 0 and 'gripper' in action:commanded_grip[arm]=action['gripper']
            if elapsed == 0 and not robot.obs["ended"]:
                feedback={"rejected_before_motion":"Bounded local IK could not reach the next interpolation target; use a smaller translation/rotation or change approach.","steps":0}
                if contract:feedback['local_ik_contraction']={k:v for k,v in evidence.items() if k!='targets'}
                recent.append({"start_step":before,"end_step":before,"action":action,"feedback":feedback})
                continue
            executed_pos,executed_q=target_for_arm(evidence,arm,before_pose,elapsed) if contract else executed_target(before_pose,pos,q,elapsed,steps)
            residual=float(np.linalg.norm(actual[:3]-executed_pos))
            rotation_error=float((Rotation.from_quat(actual[[4,5,6,3]])*Rotation.from_quat(executed_q[[1,2,3,0]]).inv()).magnitude()*180/np.pi)
            feedback={'requested_displacement':(pos-before_pose[:3]).tolist(),'achieved_displacement':(actual[:3]-before_pose[:3]).tolist(),'steps':robot.obs['step']-before,'wrist_error_m':residual,'rotation_error_deg':rotation_error,'requires_fine_recovery':residual>.025 or rotation_error>12}
            feedback["goal_compilation"] = compilation
            if contract:feedback['local_ik_contraction']={k:v for k,v in evidence.items() if k!='targets'}
            feedback['gripper_opening_by_step']=gripper_trace(a.episode,before,robot.obs['step'],arm)
            feedback['interrupted_before_requested_end']=robot.obs['step']-before<steps+settle and not robot.obs['ended']
            feedback['unexecuted_target_distance_m']=float(np.linalg.norm(pos-executed_pos))
            feedback['executed_target_displacement']=(executed_pos-before_pose[:3]).tolist()
            event={'start_step':before,'end_step':robot.obs['step'],'action':action,'feedback':feedback}
            recent.append(event)
            requested=float(np.linalg.norm(executed_pos-before_pose[:3]));achieved=float(np.linalg.norm(actual[:3]-before_pose[:3]))
            if residual>.012 and requested>.01 and achieved<requested*.65:stalls.append(event)
            with (a.trace/'motion-feedback.jsonl').open('a') as f:f.write(json.dumps(event)+chr(10))
            if residual>.10:raise RuntimeError('Wrist deviation exceeds 10cm; preserve for development diagnosis')
        status='native_end'
    except BaseException as error:
        model.fail_pending(error, robot.obs['step'])
        (a.trace/'failure.txt').write_text(traceback.format_exc());raise
    finally:
        (a.trace/'policy-result.json').write_text(json.dumps({'status':status,'steps':robot.obs['step'],'model_calls':model.calls,'seconds':time.monotonic()-started,'invalid_actions':errors},indent=2))

if __name__=='__main__':main()
