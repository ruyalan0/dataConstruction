"""Reject the archived wrist conflict before IPC motion and preserve model feedback."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.optimize import linprog, OptimizeResult

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness')]
from egg_harness.pose_guard import default_pose_guard, PoseGuard, PoseValidationError
from egg_harness.joint_goal import compile_goal, controller_settings
from egg_harness.interpolation_profiles import LinearTrajectory, make_trajectory
from egg_harness.input_contract import compact_motion
from egg_harness.observation_pair import build_pair, CAMERAS
from egg_harness.client import validate_goal_feedback
from test_joint_goal import RateLimitedPhysics, run_goal, command
from test_transport import context

ACTUAL = [.4750122129917145, 2.3639724254608154, 1.0079960823059082,
          1.783470630645752, -.0014298418536782265, .0009293584153056145]
REQUESTED = [.475, 2.3628, 1.0076, 2.0735, -.0013, .0009]


def wrist_request(joints):
    return dict(id='archived-wrist-conflict', op='control_action', phase='transit',
                action=dict(arms=dict(right=dict(control='joint', joints=joints, gripper=0.))))


def cube_pair(distance):
    normals = np.vstack([np.eye(3), -np.eye(3)])
    return [dict(arm='right', shape=dict(body=body, approximation='convexHull'),
                 normals=normals, offsets=-np.ones(6) - normals @ center,
                 low=center - 1., high=center + 1.)
            for body, center in [('link2', np.zeros(3)), ('link4', np.array([distance, 0., 0.]))]]


class PoseGuardTests(unittest.TestCase):
    def test_recorded_first_action_completes_full_linear_geometry_sweep(self):
        fixture = json.loads((HERE / 'tests/fixtures/pose-guard-first-action.json').read_text())
        native = RateLimitedPhysics()
        plan = compile_goal(native.model, fixture['state'], fixture['action'], dict(left=1., right=1.))
        with patch.dict('os.environ', ASTRA_INTERPOLATION_PROFILE='linear'):
            trajectory = make_trajectory(plan, controller_settings())
        result = default_pose_guard().validate(plan, fixture['state'], trajectory)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['violations'], [])
        # At the full-tool rate the whole 1.4 rad goal fits in this segment;
        # retain the 0.02 rad geometry sampling through the full longer sweep.
        self.assertEqual(trajectory.goal_fraction, 1.)
        self.assertEqual(result['sweep_samples'], 71)

    def test_signed_convex_radius_preserves_overlap_touching_and_disjoint_results(self):
        for distance, expected in [(1., .5), (2., 0.), (3., 0.)]:
            with self.subTest(distance=distance):
                self.assertAlmostEqual(default_pose_guard()._convex_margin(*cube_pair(distance)), expected, places=7)

    def test_solver_failure_retries_with_interior_point_method(self):
        failure = OptimizeResult(success=False, status=4, message='Unknown solver status')
        with patch('egg_harness.pose_guard.linprog', side_effect=[failure, linprog(
                [0., 0., 0., -1.], A_ub=np.c_[np.vstack([np.eye(3), -np.eye(3)]), np.ones(6)],
                b_ub=np.ones(6), bounds=[(None, None)] * 4, method='highs-ipm')]) as solve:
            margin = default_pose_guard()._convex_margin(*cube_pair(0.))
        self.assertAlmostEqual(margin, 1.)
        self.assertEqual([call.kwargs['method'] for call in solve.call_args_list], ['highs', 'highs-ipm'])

    def test_unresolved_solver_failure_rejects_before_motion_and_keeps_service_contract(self):
        failure = OptimizeResult(success=False, status=4, message='Unknown solver status')
        native = RateLimitedPhysics()
        with patch.object(PoseGuard, '_world', return_value=cube_pair(0.)), \
                patch('egg_harness.pose_guard.linprog', return_value=failure):
            result, actions, _ = run_goal(native, command())
        feedback = result['action_feedback']
        self.assertEqual(feedback['status'], 'rejected_before_motion')
        self.assertEqual(feedback['goal_submissions'], 0)
        self.assertEqual(feedback['executed_steps'], 0)
        self.assertEqual(actions, [])
        self.assertEqual(native.queued_frames, [])
        self.assertEqual(feedback['pose_validation']['stage'], 'geometry_solver')
        violation = feedback['pose_validation']['violations'][0]
        self.assertEqual(violation['code'], 'collision_geometry_solver_failed')
        self.assertEqual(len(violation['attempts']), 2)

    def test_invalid_successful_solver_result_cannot_pass_geometry_validation(self):
        for solution in ([0., 0., 0., float('nan')], [0., 0., 0., 2.]):
            with self.subTest(solution=solution):
                invalid = OptimizeResult(success=True, status=0, message='Optimal', x=np.asarray(solution))
                with patch('egg_harness.pose_guard.linprog', return_value=invalid):
                    with self.assertRaises(PoseValidationError) as caught:
                        default_pose_guard()._convex_margin(*cube_pair(0.))
                self.assertEqual(caught.exception.details['stage'], 'geometry_solver')

    def test_archived_goal_rejects_both_arms_before_any_native_submission(self):
        native = RateLimitedPhysics()
        native.positions['right'] = np.asarray(ACTUAL)
        native.openings['right'] = [.013, .013]
        before = deepcopy(native.state())
        grips = dict(left=1., right=0.)
        result, actions, captures = run_goal(native, wrist_request(REQUESTED), grips)
        feedback = result['action_feedback']
        self.assertEqual(feedback['status'], 'rejected_before_motion')
        self.assertEqual(feedback['executed_steps'], 0)
        self.assertEqual(feedback['goal_submissions'], 0)
        self.assertEqual(captures, [('archived-wrist-conflict', 0)])
        self.assertEqual(actions, [])
        self.assertEqual(native.queued_frames, [])
        self.assertEqual(grips, dict(left=1., right=0.))
        for key in before:
            np.testing.assert_array_equal(result['state'][key], before[key])
        validation = feedback['pose_validation']
        self.assertFalse(validation['accepted'])
        self.assertEqual(validation['stage'], 'requested_goal')
        hit = validation['violations'][0]
        self.assertEqual(hit['code'], 'self_collision_geometry_overlap')
        self.assertEqual(hit['arms'], ['right', 'right'])
        self.assertEqual(hit['links'], ['link3', 'link5'])
        self.assertGreater(hit['geometry_margin_m'], .006)
        self.assertEqual(validation['requested_joint_goals']['right'], REQUESTED)
        # The same allowlisted summary survives both compaction boundaries.
        source = context()
        source['last_motion'] = compact_motion(feedback)
        payload, _, _ = build_pair(source, {name: name + '.jpg' for name in CAMERAS})
        restored = json.loads(json.dumps(payload))['observation']['last_motion']
        self.assertEqual(restored['status'], 'rejected_before_motion')
        self.assertEqual(restored['pose_validation']['violations'][0]['links'], ['link3', 'link5'])
        self.assertEqual(restored['pose_validation']['requested_joint_goals']['right'], REQUESTED)
        self.assertNotIn('tracking', restored)

    def test_joint_range_rejection_reports_exact_arm_joint_angle_and_limits(self):
        request = command()
        request['action']['arms']['left']['joints'][5] = 4.
        result, actions, _ = run_goal(RateLimitedPhysics(), request)
        validation = compact_motion(result['action_feedback'])['pose_validation']
        self.assertEqual(actions, [])
        self.assertEqual(validation['stage'], 'joint_limits')
        self.assertEqual(validation['violations'][0], dict(code='joint_limit', arm='left',
            joint_index_1_based=6, requested_rad=4., limits_rad=[-3.14, 3.14]))

    def test_smaller_wrist_goal_allows_motion(self):
        native = RateLimitedPhysics(speed=2.)
        native.positions['right'] = np.asarray(ACTUAL)
        target = ACTUAL.copy()
        target[3] = 1.7
        result, actions, _ = run_goal(native, wrist_request(target), dict(left=1., right=0.))
        self.assertTrue(result['action_feedback']['pose_validation']['accepted'])
        self.assertGreater(len(actions), 0)
        self.assertLess(result['state']['right_arm_joint_state'][3], ACTUAL[3])

    def test_existing_overlap_can_be_reduced_but_not_deepened(self):
        for target, accepted in [(1.7, True), (2.1735, False)]:
            native = RateLimitedPhysics()
            native.positions['right'] = np.asarray(REQUESTED)
            joints = REQUESTED.copy()
            joints[3] = target
            result, actions, _ = run_goal(native, wrist_request(joints), dict(left=1., right=0.))
            validation = result['action_feedback']['pose_validation']
            self.assertEqual(validation['accepted'], accepted)
            self.assertTrue(validation['existing_overlaps'])
            self.assertEqual(bool(actions), accepted)

    def test_inter_arm_target_and_intermediate_sweep_are_screened(self):
        native = RateLimitedPhysics()
        native.bases['left'][0, 3], native.bases['right'][0, 3] = -.2, .2
        for angle, stage in [(np.pi, 'requested_goal'), (2 * np.pi, 'segment_sweep')]:
            action = dict(arms=dict(left=dict(control='joint', joints=[angle, 0., 0., 0., 0., 0.])))
            plan = compile_goal(native.model, native.state(), action, dict(left=1., right=1.))
            trajectory = LinearTrajectory({a: p['start'] for a,p in plan.items()},
                                          {a: p['target'] for a,p in plan.items()}, 10, 1.)
            with self.assertRaises(PoseValidationError) as caught:
                default_pose_guard().validate(plan, native.state(), trajectory)
            self.assertEqual(caught.exception.details['stage'], stage)
            self.assertEqual(caught.exception.details['violations'][0]['code'], 'inter_arm_geometry_overlap')

    def test_geometry_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'x5-collision-geometry.json'
            shutil.copy2(HERE / 'harness/configs/x5-collision-geometry.json', path)
            path.with_suffix('.npz').write_bytes(b'corrupt geometry')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                PoseGuard(path)

    def test_client_rejects_fabricated_acceptance_for_rejected_motion(self):
        result, _, _ = run_goal(RateLimitedPhysics(), command(steps=100))
        result['action_feedback']['pose_validation']['accepted'] = True
        with self.assertRaisesRegex(ValueError, 'pose validation'):
            validate_goal_feedback(result, 0)


if __name__ == '__main__':
    unittest.main()
