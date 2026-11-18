"""Native action execution and durable evidence for the controlling agent."""
import json
import hashlib
import os
import time
import sys
from pathlib import Path
import numpy as np
from PIL import Image
from public_capture import capture_public_observation, validate_camera_rig
from scipy.spatial.transform import Rotation, Slerp
from motion_guard import evaluate_guard
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from egg_harness.clocks import PHYSICS_HZ, TASK_HZ
from egg_harness.visual_history import RollingVisualHistory
from egg_harness.kinematics import Kinematics
from egg_harness.multiview import MultiViewGeometry
from egg_harness.contraction import solve_contracted
from egg_harness.storage import check_configured_storage
from egg_harness.privileged_scene import capture_privileged_scene
from egg_harness.posture_action import validate_posture_action,upright_joint_target,posture_increment
from egg_harness.posture_recovery import validate_recovery, plan_recovery, check_envelope
from egg_harness.joint_control import (joint_increment_with_holds, validate_joint_action, joint_increment, joint_preview,
    validate_unfold, plan_unfold, check_joint_envelope, check_joint_envelope_segment, validate_joint_segment, JOINT_NAMES)


class PreviewRejected(ValueError):
    """A preview binding failed before any native action was attempted."""


def plain(x):
    if hasattr(x, 'detach'):
        return x.detach().cpu().tolist()
    if hasattr(x, 'tolist'):
        return x.tolist()
    raise TypeError(type(x).__name__)


def save(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, default=plain, indent=2, allow_nan=False))
    tmp.replace(path)


def run_server(env, output, seeds, mode, task, replay=None, action_interface='joint'):
    from egg_harness.action_control import action_interface as resolve_interface, execute_control
    resolve_interface({'action_interface': action_interface})
    geometry = MultiViewGeometry()
    total_steps = env.step_lim
    if type(total_steps) is not int or total_steps <= 0:
        raise ValueError("Invalid official step budget")
    for name in ('commands', 'responses', 'frames'):
        (output / name).mkdir()
    if mode == 'development':
        diagnostic_dir = output/'developer-only'
        diagnostic_dir.mkdir()
        original_ik = env.robot_manager.solve_ik
        def audited_ik(*args, **kwargs):
            result = original_ik(*args, **kwargs)
            with (diagnostic_dir/'ik.jsonl').open('a') as f:
                f.write(json.dumps(dict(step=int(env.take_action_cnt[0]),
                    arm=kwargs['robot'].arm_name, target=kwargs['target_pose'],
                    result=result), default=plain)+chr(10))
            return result
        env.robot_manager.solve_ik = audited_ik
    commanded_grip = {'left': 1.0, 'right': 1.0}
    visual_history = RollingVisualHistory(output / 'visual-history.jsonl')
    kinematics = Kinematics(json.loads((Path(__file__).resolve().parents[1]/"configs/x5-kinematics.json").read_text()))
    def robot_state():
        from egg_harness.timed_joint import public_robot_state
        return public_robot_state(env)
    save(output / 'initial-state.json', env.get_obs()['state'])
    save(output / 'provenance.json', dict(seeds=seeds, task=task,
         privileged_diagnostics=mode=='development', depth_calibration=False, object_teleportation=False,
         privileged_scene_enabled=True, privileged_scene_source='simulator_current_state',
         mode=mode, action_interface=action_interface, io_variant='astra_io_250hz_joint_goal',
         control_timing=json.loads((output / 'control-timing.json').read_text())))

    def observe(tag):
        raw = capture_public_observation(env)
        camera_calibration_id = validate_camera_rig(env, geometry, raw)
        cameras = {}
        for name, camera in raw['vision'].items():
            stem = output / 'frames' / f'{tag}-{name}'
            Image.fromarray(camera['color']).save(str(stem) + '.jpg', quality=90)
            cameras[name] = str(stem) + '.jpg'
        ended = env.is_episode_end()
        result = dict(step=int(env.take_action_cnt[0]), state=robot_state(), cameras=cameras,
                      visual_history=visual_history.observe(int(env.take_action_cnt[0]), cameras),
                      control_timing=json.loads((output / 'control-timing.json').read_text()),
                      camera_calibration_id=camera_calibration_id, action_interface=action_interface,
                      instruction=raw['instruction'], ended=ended,
                      budget=dict(total_native_steps=env.native.step_lim,
                                  executed_native_steps=int(env.native.take_action_cnt[0]),
                                  remaining_native_steps=env.native.step_lim-int(env.native.take_action_cnt[0]),
                                  native_step_unit='25 Hz task periods',
                                  total_control_ticks=total_steps,
                                  executed_control_ticks=int(env.take_action_cnt[0]),
                                  remaining_control_ticks=total_steps-int(env.take_action_cnt[0]),
                                  step_unit='250 Hz physics ticks',
                                  task_horizon_seconds=env.native.step_lim/TASK_HZ,
                                  elapsed_simulation_seconds=int(env.take_action_cnt[0])/PHYSICS_HZ))
        if not tag.startswith('step-'):
            # Capture once per decision boundary, not on RGB-only history samples.
            result['privileged_scene'] = capture_privileged_scene(env, result['step'])
            with (output / 'privileged-scene.jsonl').open('a') as stream:
                stream.write(json.dumps(result['privileged_scene'], default=plain, allow_nan=False) + '\n')
        if ended or tag == 'zz-runner-finish' or tag.startswith('developer-'):
            unstable = 0 in getattr(env, 'unstable_envs', set())
            score = 100.0 if ended and bool(env.success[0]) else (float(env.reward_manager.get_score()[0]) if hasattr(env, 'get_score') else 0.0)
            save(output / 'runner-result.json', dict(steps=int(env.take_action_cnt[0]),
                 step_unit='250 Hz physics ticks', task_steps=int(env.native.take_action_cnt[0]),
                 elapsed_simulation_seconds=int(env.take_action_cnt[0])/PHYSICS_HZ,
                 task_horizon_seconds=env.native.step_lim/TASK_HZ,
                 success=bool(env.success[0]) and bool(ended) and not unstable,
                 score=None if unstable else score, raw_native_score=score,
                 valid_sample=not unstable, unstable=unstable, native_terminal=bool(ended)))
        save(output / 'latest.json', result)
        save(output / 'frames' / f'{tag}.json', result)
        return result

    def diagnostic(tag):
        if mode != 'development':
            raise ValueError('Privileged diagnostics are development-only')
        lm = env.scene_manager.layout_manager
        objects = {}
        for label in lm.get_labels_by_prefix(0, ''):
            pos, quat = lm.get_instance_pose(0, label=label)
            objects[label] = dict(position=pos, quaternion=quat)
        if task == "fill_egg_holder":
            inst = lm.get_instance_name(0, 'egg_holder')
            objects['egg_holder']['joints'] = lm.get_scene_object(0, inst).get_all_joints_info()
        folder = output/'developer-only'
        folder.mkdir(exist_ok=True)
        save(folder/(tag+'.json'), dict(step=int(env.take_action_cnt[0]),
             robot=env.get_obs()['state'], objects=objects, commanded_grip=commanded_grip,
             snapshot_kind='diagnostic poses and joints; not restorable physics state',
             continuation='live simulator retained without stepping'))

    joint_previews = {}

    def joint_state_snapshot(state):
        return {arm + suffix: np.asarray(state[arm + suffix], dtype=float).tolist()
                for arm in ('left', 'right')
                for suffix in ('_arm_joint_state', '_ee_pose', '_gripper_measured_joints')}

    def execute_joint(command):
        if mode != 'development':
            raise ValueError('Multijoint controls require development qualification')
        op = command['op']
        preview = op.startswith('preview_')
        primitive = op.removeprefix('preview_')
        allowed = {'id', 'op', 'label', primitive, 'preview_id'}
        if primitive == 'joint_delta': allowed.add('steps')
        if set(command) - allowed or (preview and 'preview_id' in command):
            raise ValueError('Unexpected multijoint command fields')
        if primitive == 'joint_delta':
            spec = {'joint_delta': command['joint_delta'], 'steps': command['steps']}
            deltas, limit = validate_joint_action(spec, 'transit')
            selected = list(deltas)
        else:
            spec = {'unfold_arm': command['unfold_arm']}
            unfold = validate_unfold(spec, 'transit')
            selected, limit = [unfold['arm']], unfold['max_steps']
        state = robot_state()
        for arm in selected:
            if commanded_grip[arm] < .8 or min(state[arm + '_gripper_measured_joints']) < .035:
                raise ValueError('Multijoint control requires an already open gripper; verify empty from RGB first')
        snapshot = joint_state_snapshot(state)
        start_step = int(env.take_action_cnt[0])
        if preview:
            plan = (joint_preview(kinematics, state, deltas, limit) if primitive == 'joint_delta'
                    else plan_unfold(kinematics, state, unfold))
            payload = {'primitive': primitive, 'spec': spec, 'step': start_step,
                       'state': snapshot, 'grips': commanded_grip.copy(), 'plan': plan}
            encoded = json.dumps(payload, default=plain, sort_keys=True, allow_nan=False)
            preview_id = hashlib.sha256(encoded.encode()).hexdigest()
            joint_previews[preview_id] = json.loads(encoded)
            save(output / (command['id'] + '-joint-plan.json'), payload)
            result = observe(command['id'])
            result['joint_preview'] = {'preview_id': preview_id, 'primitive': primitive,
                'planned_steps': plan['planned_steps'], 'executed_steps': 0,
                'next_motion_verified': False, 'plan': json.loads(json.dumps(plan, default=plain))}
            return result
        preview_id = command.get('preview_id')
        if not isinstance(preview_id, str) or preview_id not in joint_previews:
            raise PreviewRejected('A matching zero-motion preview is required before execution')
        bound = joint_previews[preview_id]
        if bound['primitive'] != primitive or bound['spec'] != spec or bound['step'] != start_step or bound['grips'] != commanded_grip:
            raise PreviewRejected('Stale or mismatched multijoint preview')
        for key, value in snapshot.items():
            if not np.allclose(value, bound['state'][key], atol=1e-4, rtol=0):
                raise PreviewRejected('Measured state changed since multijoint preview')
        del joint_previews[preview_id]
        plan = bound['plan']
        targets = {a: np.asarray(plan['targets'][a], dtype=float) for a in selected}
        held = {a: np.asarray(state[a + '_arm_joint_state'], dtype=float).copy()
                for a in ('left', 'right') if a not in selected}
        hold_poses = {a: np.asarray(state[a + '_ee_pose'], dtype=float).copy() for a in held}
        all_targets = {**targets, **held}
        initial_joints = {a: np.asarray(state[a + '_arm_joint_state'], dtype=float).copy() for a in ('left', 'right')}
        allowed_mask = {a: np.array([name in deltas[a] for name in JOINT_NAMES])
                        if primitive == 'joint_delta' and a in selected else
                        (np.abs(targets[a] - initial_joints[a]) > 1e-9 if a in selected else np.zeros(6, dtype=bool))
                        for a in ('left', 'right')}
        grips = commanded_grip.copy()
        reference_openings = {a: np.asarray(state[a + '_gripper_measured_joints'], dtype=float)
                              for a in ('left', 'right')}
        save(output / (command['id'] + '-joint-plan.json'), bound)
        tracking = []
        status = 'budget_exhausted'
        def track(current, joints, poses, step):
            rows = []
            for arm in ('left', 'right'):
                error = float(np.max(np.abs(np.asarray(current[arm + '_arm_joint_state']) - joints[arm])))
                wrist = float(np.linalg.norm(np.asarray(current[arm + '_ee_pose'][:3]) - poses[arm][:3]))
                opening = np.asarray(current[arm + '_gripper_measured_joints'], dtype=float)
                drift = float(np.max(np.abs(opening - reference_openings[arm])))
                bad = error > .03 or wrist > .012 or drift > .005 or (arm in selected and float(opening.min()) < .035)
                rows.append({'arm': arm, 'step': step, 'joint_error_rad': error,
                             'wrist_error_m': wrist, 'opening_change_m': drift, 'stopped': bool(bad)})
            return rows
        previous_joints = {a: np.asarray(state[a + '_arm_joint_state']) for a in ('left', 'right')}
        previous_poses = {a: np.asarray(state[a + '_ee_pose']) for a in ('left', 'right')}
        for _ in range(min(limit, plan['planned_steps'])):
            if env.is_episode_end():
                status = 'episode_ended'
                break
            check_configured_storage()
            state = robot_state()
            rows = track(state, previous_joints, previous_poses, int(env.take_action_cnt[0]))
            if any(row['stopped'] for row in rows):
                tracking.extend(rows)
                status = 'tracking_or_gripper_change'
                break
            if all(np.max(np.abs(np.asarray(state[a + '_arm_joint_state']) - targets[a])) < .001 for a in selected):
                break
            try:
                joints, poses, bounds = joint_increment_with_holds(kinematics, state, all_targets, initial_joints, allowed_mask)
            except ValueError:
                status = 'tracking_or_gripper_change'
                break
            for a in held: poses[a] = hold_poses[a]
            if primitive == 'unfold_arm':
                predicted = dict(state)
                for a in ('left', 'right'):
                    predicted[a + '_arm_joint_state'] = joints[a]
                    predicted[a + '_ee_pose'] = poses[a]
                try:
                    check_joint_envelope(kinematics, state, selected[0], unfold['clearance_box'])
                    check_joint_envelope_segment(kinematics, state, predicted, selected[0], unfold['clearance_box'])
                except ValueError:
                    status = 'clearance_envelope_exit'
                    break
            native = {}; cartesian = {}
            for arm in ('left', 'right'):
                native[arm + '_arm_joint_state'] = joints[arm]
                native[arm + '_ee_joint_state'] = np.array([grips[arm]])
                cartesian[arm + '_ee_pose'] = poses[arm]
                cartesian[arm + '_ee_joint_state'] = native[arm + '_ee_joint_state']
            before = int(env.take_action_cnt[0])
            env.take_action(native)
            if env.take_action_cnt[0] != before + 1:
                raise RuntimeError('Multijoint action not executed')
            with (output / 'actions.jsonl').open('a') as f:
                f.write(json.dumps({'command': command['id'], 'step': before + 1,
                    'action': native, 'cartesian_target': cartesian, 'primitive': primitive,
                    'preview_id': preview_id, 'joint_bounds': bounds}, default=plain) + chr(10))
            observation = observe(f'step-{before + 1:04d}')
            rows = track(observation['state'], joints, poses, before + 1)
            tracking.extend(rows)
            previous_joints, previous_poses = joints, poses
            if any(row['stopped'] for row in rows):
                status = 'tracking_or_gripper_change'
                break
        result = observe(command['id'])
        reached = all(np.max(np.abs(np.asarray(result['state'][a + '_arm_joint_state']) - targets[a])) < .001 for a in selected)
        if reached and status == 'budget_exhausted': status = 'target_reached'
        result['joint_control'] = {'preview_id': preview_id, 'primitive': primitive, 'status': status,
            'target_reached': bool(reached), 'planned_steps': plan['planned_steps'],
            'executed_steps': result['step'] - start_step, 'next_motion_verified': False,
            'instruction': 'Inspect the new RGB and measured joints, then retry the previously obstructed motion; posture completion is not task progress.',
            'tracking': tracking}
        save(output / (command['id'] + '-joint-result.json'), result['joint_control'])
        return result

    def execute(command):
        op = command['op']
        if op == 'control_action':
            return execute_control(env, output, command, action_interface, kinematics,
                                   robot_state, observe, commanded_grip, plain)
        if action_interface == 'joint' and op not in ('observe', 'finish'):
            raise ValueError('Joint-only server rejects Cartesian and legacy posture operations')
        if action_interface == 'tcp' and op in ('posture', 'recover_posture', 'joint_delta',
                                                'unfold_arm', 'preview_joint_delta', 'preview_unfold_arm'):
            raise ValueError('TCP-only server rejects joint/posture operations')
        if op in ('joint_delta', 'unfold_arm', 'preview_joint_delta', 'preview_unfold_arm'):
            return execute_joint(command)
        if op in ('posture', 'recover_posture'):
            recovery = None
            if op == 'recover_posture':
                if set(command)-{'id','op','recover_posture','label'}:raise ValueError('Unexpected recovery fields')
                recovery = validate_recovery({'recover_posture': command['recover_posture']}, 'transit')
                arms, steps = [recovery['arm']], recovery['max_steps']
            else:
                if set(command)-{'id','op','posture','steps','label'}:raise ValueError('Unexpected posture command fields')
                arms,steps=validate_posture_action({'posture':command['posture'],'steps':command['steps']},'transit')
            state=robot_state()
            for arm in arms:
                if commanded_grip[arm]<.8 or min(state[arm+'_gripper_measured_joints'])<.035:
                    raise ValueError('Posture requires an already open gripper; support/release and inspect first')
            targets={a:upright_joint_target(kinematics,state[a+'_arm_joint_state'],state[a+'_ee_pose']) for a in arms}
            if recovery is not None:
                plan = plan_recovery(kinematics, state, recovery)
                steps = plan['planned_steps']
                save(output / (command['id'] + '-recovery-plan.json'), plan)
            start_step = int(env.take_action_cnt[0])
            stop_reason = 'budget_exhausted'
            for _ in range(steps):
                if env.is_episode_end():
                    stop_reason = 'episode_ended'
                    break
                check_configured_storage()
                state=robot_state()
                if all(abs(state[a+'_arm_joint_state'][1]-targets[a][1])<.01 for a in arms):break
                joints,poses=posture_increment(kinematics,state,targets)
                if recovery is not None:
                    predicted = dict(state)
                    predicted[arms[0]+'_arm_joint_state'] = joints[arms[0]]
                    predicted[arms[0]+'_ee_pose'] = poses[arms[0]]
                    try:
                        check_envelope(kinematics, state, arms[0], recovery['clearance_box'])
                        check_envelope(kinematics, predicted, arms[0], recovery['clearance_box'])
                    except ValueError:
                        stop_reason = 'clearance_envelope_exit'
                        break
                native={};cartesian={}
                for arm in ('left','right'):
                    native[arm+'_arm_joint_state']=joints[arm]
                    native[arm+'_ee_joint_state']=np.array([commanded_grip[arm]])
                    cartesian[arm+'_ee_pose']=poses[arm]
                    cartesian[arm+'_ee_joint_state']=native[arm+'_ee_joint_state']
                before=int(env.take_action_cnt[0])
                env.take_action(native)
                if env.take_action_cnt[0]!=before+1:raise RuntimeError('Posture action not executed')
                with (output/'actions.jsonl').open('a') as f:
                    f.write(json.dumps({'command':command['id'],'step':before+1,'action':native,'cartesian_target':cartesian,'primitive':'posture'},default=plain)+chr(10))
                observation=observe(f'step-{before+1:04d}')
                stalled=False
                for arm in arms:
                    error=float(np.max(np.abs(np.asarray(observation['state'][arm+'_arm_joint_state'])-joints[arm])))
                    wrist_error=float(np.linalg.norm(np.asarray(observation['state'][arm+'_ee_pose'][:3])-poses[arm][:3]))
                    opening=min(observation['state'][arm+'_gripper_measured_joints'])
                    if error>.03 or wrist_error>.012 or opening<.035:
                        with (output/'interruptions.jsonl').open('a') as f:
                            f.write(json.dumps({'command':command['id'],'step':before+1,'reason':'posture_tracking_or_gripper_change','arm':arm,'joint_error_rad':error,'wrist_error_m':wrist_error})+chr(10))
                        stalled=True
                if stalled:
                    stop_reason = 'tracking_or_gripper_change'
                    break
            result = observe(command['id'])
            if recovery is not None:
                reached = all(abs(result['state'][a+'_arm_joint_state'][1]-targets[a][1])<.01 for a in arms)
                if reached and stop_reason == 'budget_exhausted':stop_reason = 'target_reached'
                result['posture_recovery'] = {'status': stop_reason, 'target_reached': reached,
                    'executed_steps': result['step']-start_step, 'next_motion_verified': False,
                    'instruction': 'Re-estimate wrist target and test the previously obstructed motion; reaching posture alone does not prove recovery.'}
                save(output / (command['id'] + '-recovery-result.json'), result['posture_recovery'])
            return result
        if op == 'diagnostic':
            diagnostic(command['id'])
            return observe(command['id'])
        if op in ('observe', 'finish'):
            return observe(command['id'])
        if op not in ('move', 'move_local'):
            raise ValueError('Unknown operation')
        raw = env.get_obs()
        start = {arm:np.asarray(raw['state'][f'{arm}_ee_pose'],dtype=float) for arm in ('left','right')}
        target = {arm:start[arm].copy() for arm in start}
        grip = commanded_grip.copy()
        steps, settle = command.get('steps',1), command.get('settle',0)
        contract = command.get('contract_local_ik', False)
        if type(contract) is not bool or (contract and op != 'move_local'):
            raise ValueError('Cartesian contraction requires local motion')
        if not isinstance(steps,int) or not 1 <= steps <= 80 or not 0 <= settle <= 20:
            raise ValueError('Invalid motion/settle duration')
        for arm, spec in command.get('arms',{}).items():
            if arm not in target:
                raise ValueError('Unknown arm')
            if 'pose' in spec: target[arm] = np.asarray(spec['pose'],dtype=float)
            if 'position' in spec: target[arm][:3] = spec['position']
            if 'quaternion' in spec: target[arm][3:] = spec['quaternion']
            if 'delta' in spec: target[arm][:3] += np.asarray(spec['delta'])
            if 'gripper' in spec: grip[arm] = spec['gripper']
            if target[arm].shape != (7,) or not np.isfinite(target[arm]).all():
                raise ValueError('Invalid pose')
            if abs(np.linalg.norm(target[arm][3:])-1) > .003 or not 0 <= grip[arm] <= 1:
                raise ValueError('Invalid quaternion/gripper')
            if np.linalg.norm(target[arm][:3]-start[arm][:3]) > .7:
                raise ValueError('Motion exceeds .7m')
        if "guard" in command and "guards" in command:
            raise ValueError("Use guard or guards, not both")
        guards=command.get("guards", [command["guard"]] if "guard" in command else [])
        if not isinstance(guards,list) or len({g["arm"] for g in guards}) != len(guards):
            raise ValueError("Invalid or duplicate motion guards")
        reference_openings={}
        for guard in guards:
            if guard['arm'] not in start or not .005<=guard['tracking_error_m']<=.10 or not .001<=guard['opening_change']<=.05 or type(guard['watch_grip'])!=bool:
                raise ValueError('Invalid motion guard')
            reference_openings[guard["arm"]]=float(robot_state()[guard['arm']+'_gripper_measured_joints'][0])
        rotations = {arm:Slerp([0,1],Rotation.from_quat(np.array([start[arm][3:],target[arm][3:]])[:,[1,2,3,0]])) for arm in start}
        for i in range(steps+settle):
            if env.is_episode_end(): break
            check_configured_storage()
            t = min((i+1)/steps,1)
            action = {}
            for arm in start:
                xyz = start[arm][:3]*(1-t)+target[arm][:3]*t
                quat = rotations[arm](t).as_quat()[[3,0,1,2]]
                action[f'{arm}_ee_pose'] = np.r_[xyz,quat]
                action[f'{arm}_ee_joint_state'] = np.array([grip[arm]])
            before = int(env.take_action_cnt[0])
            native_action = action
            contracted = False
            if op == "move_local":
                state = robot_state()
                native_action = {}
                if contract:
                    result = solve_contracted(kinematics, state, {arm:action[arm+'_ee_pose'] for arm in start}, command.get('arms', {}))
                    with (output/'ik-contractions.jsonl').open('a') as f:
                        f.write(json.dumps({'command':command['id'], 'step':before, 'scale':result['scale'], 'attempts':result['attempts']})+chr(10))
                    if result['scale'] is None:
                        with (output/'interruptions.jsonl').open('a') as f:
                            f.write(json.dumps({'command':command['id'], 'step':before, 'reason':'local_ik_rejected', 'detail':'All four bounded Cartesian candidates rejected'})+chr(10))
                        break
                    for arm in start:
                        native_action[arm+'_arm_joint_state'] = result['solutions'][arm]
                        native_action[arm+'_ee_joint_state'] = action[arm+'_ee_joint_state']
                        action[arm+'_ee_pose'] = result['targets'][arm]
                    contracted = result['scale'] < 1
                try:
                    for arm in start:
                        if contract:continue
                        joints = state[arm+"_arm_joint_state"]
                        target_pose = action[arm+"_ee_pose"]
                        # An untouched arm holds its measured joints, avoiding a new IK branch.
                        moved = arm in command.get("arms", {}) and not np.allclose(target_pose, state[arm+"_ee_pose"], atol=1e-7)
                        solution = kinematics.solve_local(joints, state[arm+"_ee_pose"], target_pose) if moved else kinematics.joints_array(joints)
                        native_action[arm+"_arm_joint_state"] = solution
                        native_action[arm+"_ee_joint_state"] = action[arm+"_ee_joint_state"]
                except ValueError as error:
                    with (output/"interruptions.jsonl").open("a") as f:
                        f.write(json.dumps({"command":command["id"],"step":before,"reason":"local_ik_rejected","detail":str(error)})+chr(10))
                    break
            env.take_action(native_action)
            if env.take_action_cnt[0] != before+1:
                raise RuntimeError('Action not executed')
            commanded_grip.update(grip)
            with (output / 'actions.jsonl').open('a') as f:
                f.write(json.dumps(dict(command=command['id'], step=before+1, action=native_action, cartesian_target=action),default=plain)+chr(10))
            observation=observe(f'step-{before+1:04d}')
            interrupted=False
            for guard in guards:
                event=evaluate_guard(observation['state'],action,guard,reference_openings[guard["arm"]])
                if event is not None:
                    with (output/'interruptions.jsonl').open('a') as f:
                        f.write(json.dumps({'command':command['id'],'step':observation['step'],"arm":guard["arm"],**event})+chr(10))
                    interrupted=True
                    break
            if interrupted or contracted:break
        return observe(command['id'])

    observe('initial')
    if replay is not None:
        from native_replay import compare_public, compare_objects
        if mode != 'development' or replay['provenance']['task'] != task or replay['provenance']['seeds'] != list(seeds):
            raise ValueError('Replay does not match the initialized native scene')
        if int(env.take_action_cnt[0]) != 0 or replay['through'] >= total_steps:
            raise ValueError('Replay must start at native zero and leave the original budget available')
        source = Path(replay['episode'])
        save(output / 'replay-source.json', {key: replay[key] for key in ('episode', 'through', 'provenance', 'prefix_sha256')})
        for identifier in {row['command'] for row in replay['rows']}:
            # Originals are evidence, never pending IPC: replay is driven only by
            # recorded native actions, without rerunning high-level planning/IK.
            folder = output / 'developer-only' / 'replay-commands'; folder.mkdir(exist_ok=True)
            save(folder / (identifier + '.json'), json.loads((source / 'commands' / (identifier + '.json')).read_text()))
        maximum_errors = {}
        for original in replay['rows']:
            if env.is_episode_end(): raise RuntimeError('Native scene ended before replay endpoint')
            check_configured_storage()
            before = int(env.take_action_cnt[0])
            if original['step'] != before + 1: raise ValueError('Replay native step continuity mismatch')
            native = {key: np.asarray(value, dtype=float) for key, value in original['action'].items()}
            env.take_action(native)
            if int(env.take_action_cnt[0]) != before + 1: raise RuntimeError('Replay native action did not execute once')
            commanded_grip.update({arm: float(native[arm + '_ee_joint_state'][0]) for arm in ('left', 'right')})
            row = {**original, 'replay_source_step': original['step'], 'replay_source_episode': str(source)}
            with (output / 'actions.jsonl').open('a') as stream:
                stream.write(json.dumps(row, default=plain) + chr(10))
            observation = observe(f'step-{before + 1:04d}')
            expected = json.loads((source / 'frames' / f'step-{before + 1:04d}.json').read_text())
            comparison = {'step': before + 1, 'arms': compare_public(expected['state'], observation['state'])}
            for arm, errors in comparison['arms'].items():
                maximum_errors.setdefault(arm, {key: 0. for key in errors})
                for key, value in errors.items(): maximum_errors[arm][key] = max(maximum_errors[arm][key], value)
            with (output / 'developer-only' / 'replay-prefix-errors.jsonl').open('a') as stream:
                stream.write(json.dumps(comparison) + chr(10))
        diagnostic('replay-endpoint')
        actual_diagnostic = json.loads((output / 'developer-only' / 'replay-endpoint.json').read_text())
        report = {'step': int(env.take_action_cnt[0]), 'arms': compare_public(replay['target']['state'], observation['state']),
                  'maximum_prefix_errors': maximum_errors,
                  'objects': compare_objects(source, replay['through'], actual_diagnostic['objects']),
                  'exact_physics_restoration': False, 'ready_for_policy': True,
                  'scope': 'Re-executed original native action prefix under original budget; measured discrepancies require review, not a restorable checkpoint or determinism guarantee'}
        save(output / 'developer-only' / 'replay-comparison.json', report)
    save(output / 'ready.json', dict(pid=os.getpid(), seeds=seeds))
    while True:
        for path in sorted((output / 'commands').glob('*.json')):
            response = output / 'responses' / path.name
            if response.exists(): continue
            command = json.loads(path.read_text())
            before_command = int(env.take_action_cnt[0])
            try:
                result = execute(command)
            except PreviewRejected as error:
                if command['op'] not in ('joint_delta', 'unfold_arm') or int(env.take_action_cnt[0]) != before_command:
                    raise RuntimeError('Preview rejection occurred outside pre-execution binding') from error
                result = observe(command['id'])
                result['joint_rejection'] = {'status': 'rejected_before_motion', 'reason': str(error),
                    'executed_steps': 0, 'next_motion_verified': False}
                save(output / (command['id'] + '-joint-rejection.json'), result['joint_rejection'])
            save(response,result)
            if command['op'] == 'finish':
                save(output / 'result.json',result)
                return
        request = output/'developer-snapshot.request'
        if request.exists():
            diagnostic('failure-'+str(int(env.take_action_cnt[0])))
            request.rename(output/('developer-snapshot-'+str(time.time_ns())+'.done'))
        time.sleep(.15)
