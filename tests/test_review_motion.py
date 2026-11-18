import numpy as np
import pytest
from test_control import decision, raw
from test_harness_boundary import act, client_for, three_views

from dojo_astra.agent import Agent
from dojo_astra.arx_x5_interpreter import Decision, rotation
from dojo_astra.evidence_tools import ToolInputError
from dojo_astra.executor import Executor
from dojo_astra.observation_adapter import adapt
from dojo_astra.trajectory import Trajectory


def trajectory(motion=4, settle=0, translation=(12, 12, 0), turn=(0, 0, 12), gripper='hold'):
    hold = {'translation_mm': [0, 0, 0], 'rotation_vector_deg': [0, 0, 0], 'frame': 'world',
            'reference': 'current', 'gripper': 'hold'}
    return Trajectory(waypoints=[{'left': {**hold, 'translation_mm': translation,
                       'rotation_vector_deg': turn, 'gripper': gripper}, 'right': hold}],
                      motion_steps=motion, settle_steps=settle, note='Generic interface test')


def test_slow_pose_changes_native_targets_and_visits_same_endpoint():
    ex = Executor()
    ex.observe(adapt(raw(), 0))
    slow, fast = ex.plan(trajectory()), ex.plan(trajectory(motion=1))
    np.testing.assert_allclose(slow[-1]['left_ee_pose'], fast[-1]['left_ee_pose'])
    np.testing.assert_allclose((slow[0]['left_ee_pose'][:3] - ex.obs.poses['left'][:3]) * 1000, [3, 3, 0])
    assert np.rad2deg(rotation(slow[0]['left_ee_pose']).magnitude()) == pytest.approx(3)
    for target in slow:
        np.testing.assert_array_equal(target['right_ee_pose'], ex.obs.poses['right'])


def test_entire_waypoint_path_is_bounded_not_only_each_offset():
    ex = Executor()
    ex.observe(adapt(raw(), 0))
    command = trajectory(translation=(20, 0, 0), turn=(0, 0, 0))
    other = command.waypoints[0].model_copy(deep=True)
    other.left.translation_mm = (-20, 0, 0)
    command.waypoints.append(other)
    with pytest.raises(ToolInputError, match='Total'):
        ex.plan(command)
    assert ex.remaining == 0 and not ex.pending


def test_low_progress_returns_control_before_all_eight_calls():
    ex = Executor(detailed_feedback=True)
    ex.observe(adapt(raw(), 0))
    command = decision(amount=30).model_dump()
    command['steps'] = 8
    ex.load(Decision.model_validate(command))
    sequence = 0
    while ex.remaining:
        ex.next_action()
        sequence += 1
        ex.observe(adapt(raw(), sequence))
    assert sequence == 3
    assert ex.feedback['interruption_reason'] == 'sustained_low_progress'
    assert ex.feedback['progress']['left']['along_requested_direction_mm'] == 0
    assert ex.feedback['error_mm']['left'] == pytest.approx(30)
    assert ex.feedback['semantic_status'] == 'unconfirmed'


def test_transient_low_progress_resets_without_inventing_contact():
    ex = Executor()
    ex.observe(adapt(raw(), 0))
    command = decision(amount=30).model_dump()
    command['steps'] = 8
    ex.load(Decision.model_validate(command))
    for sequence in (1, 2, 3):
        ex.next_action()
        data = raw()
        if sequence == 3:
            data['state']['left_ee_pose'][2] += 0.004
        ex.observe(adapt(data, sequence))
    assert ex.remaining == 5
    assert ex.feedback['progress']['left']['consecutive_low_progress_boundaries'] == 0
    assert not ex.feedback['interrupted']


def test_pose_reached_does_not_cancel_gripper_settle_steps():
    ex = Executor()
    ex.observe(adapt(raw(), 0))
    ex.load(trajectory(motion=1, settle=5, translation=(0, 0, 0), turn=(0, 0, 0), gripper='close'))
    for sequence in range(1, 7):
        action = ex.next_action()
        assert action['left_ee_joint_state'][0] == 0
        data = raw()
        data['state']['left_ee_joint_state'] = [0]
        ex.observe(adapt(data, sequence))
        assert ex.remaining == 6 - sequence
        assert ex.feedback['pose_reached'] and not ex.feedback['interrupted']
    assert ex.feedback['phase'] == 'settle'


def test_latched_hold_keeps_reference_until_explicit_latch():
    ex = Executor()
    ex.observe(adapt(raw(), 0))
    initial = ex.obs.poses['left'].copy()
    hold = decision(operation='hold_latched', amount=0).model_dump()
    hold['steps'] = 1
    for sequence in range(1, 11):
        ex.load(Decision.model_validate(hold))
        np.testing.assert_array_equal(ex.next_action()['left_ee_pose'], initial)
        data = raw()
        data['state']['left_ee_pose'][0] += sequence / 1000
        ex.observe(adapt(data, sequence))
    current = decision(operation='hold_current', amount=0)
    np.testing.assert_array_equal(ex.plan(current)[0]['left_ee_pose'], ex.obs.poses['left'])
    latch = Decision.model_validate({**hold, 'left': {**hold['left'], 'operation': 'latch'}})
    ex.load(latch)
    np.testing.assert_array_equal(ex.latched['left'], ex.obs.poses['left'])


def test_invalid_trajectory_repairs_without_latching_or_dispatching(tmp_path):
    invalid = trajectory().model_dump()
    invalid['waypoints'][0]['left']['translation_mm'] = [30, 30, 0]
    requests = []
    agent = Agent(client_for(iter([{'name': 'trajectory', 'arguments': invalid}, act()]), requests),
                  tmp_path, protocol='json-high')
    agent.update_obs(three_views())
    initial = agent.executor.latched['left'].copy()
    agent.get_action()
    assert len(requests) == 2 and agent.executor.steps == 1
    np.testing.assert_array_equal(agent.executor.latched['left'], initial)
