"""Qualify full joint IPC and native tool schemas on one fresh worker episode."""
import argparse
import json
from pathlib import Path

import numpy as np

from egg_harness.client import Robot
from egg_harness.native_responses import NativeToolVision
from egg_harness.usage import model_usage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--episode', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--live-providers', nargs='*', choices=['azure-astra', 'zzz-astra', 'zzy-astra'], default=[])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    trace = args.output / 'robot'; trace.mkdir()
    robot = Robot(args.episode, trace)
    if robot.obs['step'] != 0 or robot.obs['action_interface'] != 'joint':
        raise ValueError('Require a fresh episode with the server default Joint interface')
    initial = robot.obs
    results = {'task_success_evaluation': False, 'default_interface': 'joint', 'segments': [], 'providers': []}
    try:
        # Tiny, explicit near-home joint motion; all twelve joints and both grippers.
        arms = {arm: {'control': 'joint',
                      'joints': (np.asarray(initial['state'][arm + '_arm_joint_state']) +
                                 np.array([.005, .01, .005, -.005, .005, -.005])).tolist(),
                      'gripper': grip} for arm, grip in (('left', .37), ('right', .62))}
        for label, targets in [('all14', arms), ('return', {
                arm: {'control': 'joint', 'joints': initial['state'][arm + '_arm_joint_state'], 'gripper': 1.}
                for arm in ('left', 'right')})]:
            robot.command('control_action', action={'arms': targets, 'steps': 2, 'settle': 0}, phase='fine')
            feedback = robot.obs['action_feedback']
            if feedback['executed_steps'] != 2 or feedback['status'] != 'completed':
                raise RuntimeError('Joint prefix did not complete: ' + json.dumps(feedback))
            results['segments'].append({'label': label, 'feedback': feedback})
        step = robot.obs['step']
        robot.command('control_action', action={'arms': {'left': {'control': 'tcp'}}, 'steps': 1}, phase='fine')
        if robot.obs['step'] != step or robot.obs['action_feedback']['status'] != 'rejected_before_motion':
            raise RuntimeError('Joint-only server accepted a TCP arm')
        results['tcp_rejected_without_motion'] = True
        for provider in args.live_providers:
            config = json.loads(Path('configs/classify-language-joint-development.json').read_text())
            # Omission qualifies the registry default, independently of the explicit template.
            del config['action_interface']
            config.update(astra_provider=provider, max_model_calls=2)
            model = NativeToolVision(config, args.output / provider / 'model')
            try:
                obs = robot.obs
                question = {'step': obs['step'], 'state': obs['state'],
                    'instruction': 'Protocol qualification, not task solving. Call control_arms with BOTH arms in joint mode. Copy each arm current measured six joint angles as absolute targets, both grippers1.0, steps1, settle0, phase fine. This is one nearly stationary native joint hold; do not plan any other movement.'}
                decision = model.ask(json.dumps(question), obs['cameras'])
                action = decision['action']
                if set(action) != {'control_arms'} or decision['phase'] != 'fine':
                    raise ValueError('Expected native joint hold tool')
                spec = action['control_arms']
                if set(spec['arms']) != {'left', 'right'} or spec['steps'] != 1 or spec['settle'] != 0:
                    raise ValueError('Expected exactly one joint hold step for both arms')
                for arm, target in spec['arms'].items():
                    if target['control'] != 'joint' or target['gripper'] != 1.:
                        raise ValueError('Unexpected joint hold specification')
                    if np.max(np.abs(np.asarray(target['joints']) - obs['state'][arm + '_arm_joint_state'])) > .0001:
                        raise ValueError('Model requested movement beyond qualification tolerance')
                robot.command('control_action', action=spec, phase='fine')
                feedback = robot.obs['action_feedback']
                if feedback['executed_steps'] != 1 or feedback['status'] != 'completed':
                    raise RuntimeError('Native model-selected joint hold failed')
                model.observe(feedback, robot.obs['step'], robot.obs['ended'])
                end = model.ask(json.dumps({'step': robot.obs['step'], 'instruction':
                    'Read the real preceding control_arms result. Call stop_episode with reason JOINT_INTERFACE_OK and memory containing ONLY its executed_steps as a decimal integer. This concludes protocol qualification, not native task success.'}), {})
                if end['action'] != {'stop': True, 'reason': 'JOINT_INTERFACE_OK'} or end['memory'].strip() != '1':
                    raise ValueError('Model did not consume the actual joint tool result')
                model.observe({'protocol_complete': True, 'native_steps_executed': 0}, robot.obs['step'], False)
                results['providers'].append({'provider': provider, 'status': 'passed',
                    'tool_names': sorted(model.registry.contracts), 'usage': model_usage(model.trace)})
            except BaseException as error:
                model.fail_pending(error, robot.obs['step'])
                raise
            finally:
                model.client.close()
        records = [json.loads(line) for line in (args.episode / 'actions.jsonl').read_text().splitlines()]
        for record in records[:2]:
            if record['action']['left_ee_joint_state'] != [.37] or record['action']['right_ee_joint_state'] != [.62]:
                raise ValueError('Continuous gripper commands were not preserved')
        results.update(status='passed', native_steps=robot.obs['step'])
    except BaseException as error:
        results.update(status='failed', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        (args.output / 'result.json').write_text(json.dumps(results, indent=2) + '\n')
        robot.command('finish')
    print(json.dumps(results), flush=True)


if __name__ == '__main__':
    main()
