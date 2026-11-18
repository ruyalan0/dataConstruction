"""Verify stage-specific references, unchanged settling and linear return budgets."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness'), str(HERE / 'tests')]
from egg_harness.client import validate_goal_feedback
from egg_harness.input_contract import compact_motion
from egg_harness.joint_goal import controller_settings, execute_control
from egg_harness.native_tools import ToolRegistry
from egg_harness.observation_pair import build_pair, CAMERAS
from egg_harness.push_t_motion import linear_return_estimate, motion_policy, STAGE_PROFILES
from egg_harness.timed_joint import TimedJointEnvironment
from test_joint_goal import command, RateLimitedPhysics, run_goal
from test_transport import context


def run_sequence(native, requests):
    env = TimedJointEnvironment(native)
    grips = dict(left=1., right=1.)
    results = []

    def observe(tag):
        return dict(step=env.take_action_cnt[0], state=native.state(), ended=env.is_episode_end())

    with tempfile.TemporaryDirectory() as folder, patch('egg_harness.joint_goal.check_configured_storage'):
        for index, original in enumerate(requests):
            request = deepcopy(original)
            request['id'] = 'sequence-' + str(index)
            previous_step = env.take_action_cnt[0]
            result = execute_control(env, Path(folder), request, 'joint', native.model,
                                     native.state, observe, grips, lambda value: value.tolist())
            validate_goal_feedback(result, previous_step)
            results.append(result)
    return results


class PushTMotionTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, ASTRA_CONTROLLER_TASK='push_T',
                                 ASTRA_INTERPOLATION_PROFILE='quintic')
        environment.start()
        self.addCleanup(environment.stop)

    def test_schema_requires_stage_only_for_push_t_on_quintic(self):
        registry = ToolRegistry(dict(task='push_T', action_interface='joint', interpolation_profile='quintic'))
        tool = next(tool for tool in registry.definitions if tool['name'] == 'control_arms')
        self.assertIn('motion_stage', tool['parameters']['required'])
        arguments = dict(assessment='clear', plan='approach', memory='first approach',
                         left=dict(control='joint', joints=[0.1, 0., 0., 0., 0., 0.], gripper=1.),
                         right=None, motion_stage='initial_reach')
        action = registry.decision('control_arms', json.dumps(arguments))['action']['control_arms']
        self.assertEqual(set(action), {'arms', 'motion_stage'})
        self.assertEqual(action['motion_stage'], 'initial_reach')
        missing = dict(arguments)
        del missing['motion_stage']
        with self.assertRaises(ValueError):
            registry.decision('control_arms', json.dumps(missing))
        for extra in (dict(motion_stage='anything'), dict(speed=2.), dict(duration=1.), dict(phase='transit')):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                registry.decision('control_arms', json.dumps(dict(arguments, **extra)))
        for task, profile in [('swap_T', 'quintic'), ('align_blocks', 'quintic'),
                              ('push_T', 'native'), ('push_T', 'linear')]:
            other = ToolRegistry(dict(task=task, action_interface='joint', interpolation_profile=profile))
            contract = next(tool for tool in other.definitions if tool['name'] == 'control_arms')
            self.assertNotIn('motion_stage', contract['parameters']['properties'])
            with self.assertRaises(ValueError):
                other.decision('control_arms', json.dumps(arguments))

    def test_each_stage_executes_correct_profile_with_unchanged_speed_and_settling(self):
        for stage, profile in STAGE_PROFILES.items():
            with self.subTest(stage=stage):
                result, actions, _ = run_goal(RateLimitedPhysics(speed=10.),
                                               command(.12, motion_stage=stage))
                feedback = result['action_feedback']
                settings = feedback['controller']
                self.assertEqual(feedback['status'], 'target_reached')
                self.assertEqual(feedback['motion_stage'], stage)
                self.assertEqual(settings['interpolation_profile'], profile)
                self.assertEqual(settings['joint_velocity_limit_rad_s'], 3.75)
                self.assertEqual(settings['joint_acceleration_limit_rad_s2'], 3.)
                self.assertEqual(settings['settle_window_seconds'], .1)
                self.assertEqual(settings['reference_acceleration_limit_enforced'], profile == 'quintic')
                reference_seconds = feedback['trajectory']['duration_seconds']
                self.assertGreaterEqual(feedback['actual_duration_seconds'] + 1e-10, reference_seconds + .1)
                positions = np.array([[0.] * 6] + [row['action']['left_arm_joint_state'] for row in actions])
                velocity = np.diff(positions, axis=0) * 250
                self.assertLessEqual(np.max(np.abs(velocity)), 3.75 + 1e-9)
                np.testing.assert_allclose(positions[-25:, 0], .12)
                if profile == 'linear':
                    self.assertEqual(reference_seconds, .032)
                    self.assertEqual(feedback['trajectory']['profile'], 'linear_auto_duration')
                else:
                    self.assertGreater(reference_seconds, .4)
                    self.assertEqual(feedback['trajectory']['profile'], 'synchronized_quintic')
                    acceleration = np.diff(velocity, axis=0) * 250
                    self.assertLessEqual(np.max(np.abs(acceleration)), 3. + 1e-8)

    def test_long_initial_reach_keeps_partial_segment_and_settling(self):
        native = RateLimitedPhysics(speed=10.)
        result, continued = run_sequence(native, [command(3., motion_stage='initial_reach'),
                                                  command(3., motion_stage='initial_reach')])
        feedback = result['action_feedback']
        self.assertEqual(feedback['reason'], 'trajectory_segment_complete')
        self.assertAlmostEqual(feedback['trajectory']['goal_fraction'], 2.625 / 3.)
        self.assertEqual(feedback['actual_duration_seconds'], .8)
        self.assertEqual(continued['action_feedback']['status'], 'target_reached')

    def test_coarse_push_after_contact_acquisition_is_linear_then_fine_is_quintic(self):
        results = run_sequence(RateLimitedPhysics(speed=10.), [
            command(.04, motion_stage='manipulation'),
            command(.08, motion_stage='coarse_push'),
            command(.12, motion_stage='manipulation')])
        profiles = [result['action_feedback']['trajectory']['profile'] for result in results]
        self.assertEqual(profiles, ['synchronized_quintic', 'linear_auto_duration', 'synchronized_quintic'])
        self.assertTrue(all(result['action_feedback']['status'] == 'target_reached' for result in results))

    def test_coarse_push_schema_and_skill_switch_boundary(self):
        registry = ToolRegistry(dict(task='push_T', action_interface='joint', interpolation_profile='quintic'))
        arguments = dict(assessment='T center distance 0.0500 m; established low side contact',
                         plan='short coarse push', memory='fine_alignment=false',
                         left=dict(control='joint', joints=[0.1, 0., 0., 0., 0., 0.], gripper=0.),
                         right=None, motion_stage='coarse_push')
        action = registry.decision('control_arms', json.dumps(arguments))['action']['control_arms']
        self.assertEqual(action['motion_stage'], 'coarse_push')
        skill = (HERE / 'harness/skills/push-t/SKILL.md').read_text()
        self.assertIn('greater than 0.0200 m', skill)
        self.assertIn('fine_alignment=true', skill)
        self.assertIn('QUINTIC polynomial interpolation', skill)

    def test_initial_reach_is_per_arm_and_cannot_be_reused_after_manipulation(self):
        native = RateLimitedPhysics(speed=10.)
        other_arm = command(.12, motion_stage='initial_reach')
        other_arm['action']['arms']['right'] = other_arm['action']['arms'].pop('left')
        result, rejected, accepted = run_sequence(native, [command(.12, motion_stage='manipulation'),
            command(.15, motion_stage='initial_reach'), other_arm])
        self.assertEqual(result['action_feedback']['status'], 'target_reached')
        self.assertEqual(rejected['action_feedback']['status'], 'rejected_before_motion')
        self.assertIn('cannot be reused', rejected['action_feedback']['reason'])
        self.assertEqual(rejected['action_feedback']['executed_steps'], 0)
        self.assertEqual(accepted['action_feedback']['status'], 'target_reached')

    def test_rejected_manipulation_does_not_consume_initial_reach(self):
        native = RateLimitedPhysics(speed=10.)
        rejected, accepted = run_sequence(native, [command(.12, gripper=2., motion_stage='manipulation'),
                                                   command(.12, motion_stage='initial_reach')])
        self.assertEqual(rejected['action_feedback']['status'], 'rejected_before_motion')
        self.assertEqual(accepted['action_feedback']['status'], 'target_reached')

    def test_linear_stages_allow_compact_closed_empty_push_gripper(self):
        for stage in ('initial_reach', 'home_return'):
            with self.subTest(stage=stage):
                native = RateLimitedPhysics(speed=10.)
                native.openings['left'] = [0., 0.]
                result, actions, _ = run_goal(native,
                    command(.12, gripper=0., motion_stage=stage), dict(left=0., right=1.))
                feedback = result['action_feedback']
                self.assertEqual(feedback['status'], 'target_reached')
                self.assertEqual(feedback['trajectory']['profile'], 'linear_auto_duration')
                self.assertTrue(actions)
                self.assertTrue(all(row['action']['left_ee_joint_state'] == [0.] for row in actions))

    def test_invalid_or_missing_stage_rejects_without_motion(self):
        requests = [command(.12), command(.12, motion_stage='anything'),
                    command(.12, motion_stage=None), command(.12, motion_stage=1)]
        for request in requests:
            with self.subTest(request=request):
                result, actions, _ = run_goal(RateLimitedPhysics(speed=10.), request)
                self.assertEqual(result['action_feedback']['status'], 'rejected_before_motion')
                self.assertEqual(result['action_feedback']['executed_steps'], 0)
                self.assertEqual(actions, [])

    def test_home_return_also_prevents_reusing_initial_reach(self):
        results = run_sequence(RateLimitedPhysics(speed=10.), [command(.12, motion_stage='home_return'),
                               command(.15, motion_stage='initial_reach')])
        self.assertEqual(results[0]['action_feedback']['status'], 'target_reached')
        self.assertEqual(results[1]['action_feedback']['status'], 'rejected_before_motion')
        self.assertEqual(results[1]['action_feedback']['executed_steps'], 0)

    def test_linear_motion_can_leave_loaded_idle_arm_null(self):
        native = RateLimitedPhysics(speed=10., contact_aperture=.01)
        native.openings['left'] = [.01, .01]
        request = command(.12, motion_stage='initial_reach')
        request['action']['arms']['right'] = request['action']['arms'].pop('left')
        result, actions, _ = run_goal(native, request, dict(left=0., right=1.))
        self.assertEqual(result['action_feedback']['status'], 'gripper_settled_before_target')
        for action in actions:
            np.testing.assert_allclose(action['action']['left_arm_joint_state'], [0.] * 6)
            self.assertEqual(action['action']['left_ee_joint_state'], [0.])

    def test_feedback_is_checked_against_executed_stage_not_global_quintic(self):
        result, _, _ = run_goal(RateLimitedPhysics(speed=10.), command(.12, motion_stage='home_return'))
        validate_goal_feedback(result, 0)
        wrong = deepcopy(result)
        wrong['action_feedback']['motion_stage'] = 'manipulation'
        with self.assertRaises(ValueError):
            validate_goal_feedback(wrong, 0)
        missing = deepcopy(result)
        del missing['action_feedback']['motion_stage']
        with self.assertRaisesRegex(ValueError, 'requires motion_stage'):
            validate_goal_feedback(missing, 0)
        compact = compact_motion(result['action_feedback'])
        self.assertEqual(compact['motion_stage'], 'home_return')
        self.assertEqual(compact['trajectory']['profile'], 'linear_auto_duration')

    def test_other_tasks_and_nonquintic_runs_keep_original_contract(self):
        for task, profile in [('swap_T', 'quintic'), ('align_blocks', 'quintic'),
                              ('push_T', 'linear'), ('push_T', 'native')]:
            with self.subTest(task=task, profile=profile), patch.dict(os.environ,
                    ASTRA_CONTROLLER_TASK=task, ASTRA_INTERPOLATION_PROFILE=profile):
                result, _, _ = run_goal(RateLimitedPhysics(speed=10.), command(.12))
                self.assertNotIn('motion_stage', result['action_feedback'])
                rejected, actions, _ = run_goal(RateLimitedPhysics(speed=10.),
                                                 command(.12, motion_stage='home_return'))
                self.assertEqual(rejected['action_feedback']['status'], 'rejected_before_motion')
                self.assertEqual(actions, [])

    def test_linear_home_budget_rounds_ticks_and_adds_settling_per_segment(self):
        settings = controller_settings()
        for distance, seconds, calls in [(0., 0., 0), (.009, 0., 0), (1.5, .5, 1),
                                          (2.625, .8, 1), (3., 1., 2), (5.25, 1.6, 2)]:
            with self.subTest(distance=distance):
                estimate = linear_return_estimate(distance, settings)
                self.assertAlmostEqual(estimate['reference_and_settle_seconds'], seconds)
                self.assertEqual(estimate['model_calls'], calls)
        for distance in (-1., float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                linear_return_estimate(distance, settings)

    def test_budget_uses_measured_nonzero_home_and_conservative_sequential_return(self):
        state = {arm + '_arm_joint_state': [2.] * 6 for arm in ('left', 'right')}
        homes = dict(left=[.5] * 6, right=[1.25] * 6)
        policy = motion_policy(state, homes, controller_settings())
        budget = policy['home_return_budget']
        self.assertEqual(budget['max_joint_distance_rad'], dict(left=1.5, right=.75))
        self.assertAlmostEqual(budget['simultaneous']['reference_and_settle_seconds'], .5)
        self.assertAlmostEqual(budget['sequential_reference_and_settle_seconds'], .8)
        self.assertAlmostEqual(budget['suggested_reserve_seconds'], 2.64)
        self.assertEqual(budget['suggested_reserve_model_calls'], 5)
        self.assertEqual(policy['stage_profiles'], STAGE_PROFILES)
        self.assertTrue(policy['per_goal_settling_unchanged'])

    def test_observation_exposes_refreshed_budget_only_for_push_t(self):
        source = context(value=1.5)
        images = {name: name + '.jpg' for name in CAMERAS}
        payload, _, _ = build_pair(source, images)
        policy = payload['observation']['motion_policy']
        self.assertEqual(policy['stage_profiles'], STAGE_PROFILES)
        self.assertEqual(policy['home_return_budget']['simultaneous']['reference_and_settle_seconds'], .5)
        self.assertEqual(payload['controller']['interpolation_profile'], 'quintic')
        self.assertNotIn('motion_policy', source)
        for task in ('swap_T', 'align_blocks'):
            with self.subTest(task=task), patch.dict(os.environ, ASTRA_CONTROLLER_TASK=task):
                other, _, _ = build_pair(context(value=1.5), images)
                self.assertNotIn('motion_policy', other['observation'])


if __name__ == '__main__':
    unittest.main()
