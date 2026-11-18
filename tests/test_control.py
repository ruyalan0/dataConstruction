import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from dojo_astra.arx_x5_interpreter import Decision, interpret
from dojo_astra.executor import Executor
from dojo_astra.observation_adapter import adapt


def raw():
    return {
        "data_format_version": "v1.0",
        "instruction": "Lift the object",
        "state": {
            "left_ee_pose": [0.1, 0.2, 0.8, 1, 0, 0, 0],
            "right_ee_pose": [-0.1, 0.2, 0.8, 1, 0, 0, 0],
            "left_ee_joint_state": [0.3],
            "right_ee_joint_state": [0.7],
        },
        "vision": {
            "head": {
                "color": np.zeros((8, 8, 3), dtype=np.uint8),
                "depth": "SECRET",
                "extrinsic_matrix": "SECRET",
            }
        },
        "success": True,
        "object_pose": "SECRET",
    }


def decision(**kwargs):
    hold = {"operation": "hold", "frame": "world", "axis": "+z", "amount": 0, "gripper": "hold"}
    left = dict(hold, operation="translate", amount=10)
    left.update(kwargs)
    return Decision(left=left, right=hold, steps=3, note="test")


def test_units_and_inactive_arm():
    obs = adapt(raw(), 0)
    result = interpret(decision(), obs)
    np.testing.assert_allclose(result["left_ee_pose"][:3], [0.1, 0.2, 0.81])
    np.testing.assert_array_equal(result["right_ee_pose"], obs.poses["right"])
    np.testing.assert_array_equal(result["right_ee_joint_state"], [0.7])


def test_tool_translation_uses_wxyz():
    data = raw()
    data["state"]["left_ee_pose"][3:] = Rotation.from_euler("z", 90, degrees=True).as_quat(scalar_first=True)
    result = interpret(decision(frame="tool", axis="+x"), adapt(data, 0))
    np.testing.assert_allclose(result["left_ee_pose"][:3], [0.1, 0.21, 0.8])


@pytest.mark.parametrize("frame", ["world", "tool"])
def test_rotation_composition(frame):
    data = raw()
    old = Rotation.from_euler("z", 90, degrees=True)
    data["state"]["left_ee_pose"][3:] = old.as_quat(scalar_first=True)
    result = interpret(decision(operation="rotate", axis="+x", frame=frame), adapt(data, 0))
    delta = Rotation.from_euler("x", 10, degrees=True)
    expected = old * delta if frame == "tool" else delta * old
    np.testing.assert_allclose(result["left_ee_pose"][3:], expected.as_quat(scalar_first=True))


def test_no_truth_in_observation_and_copy():
    data = raw()
    obs = adapt(data, 0)
    assert set(vars(obs)) == {"sequence", "instruction", "poses", "gripper_commands", "images"}
    data["vision"]["head"]["color"][:] = 255
    assert obs.images["head"].max() == 0


@pytest.mark.parametrize(
    "kwargs",
    [{"amount": float("nan")}, {"amount": 31}, {"operation": "rotate", "amount": 16}, {"frame": "camera"}],
)
def test_invalid_actions_fail(kwargs):
    with pytest.raises(ValueError):
        decision(**kwargs)


def test_queue_requires_feedback_and_does_not_accumulate():
    ex = Executor()
    ex.observe(adapt(raw(), 0))
    ex.load(decision())
    first = ex.next_action()
    with pytest.raises(RuntimeError):
        ex.next_action()
    ex.observe(adapt(raw(), 1))
    assert ex.feedback["displacement_mm"]["left"] == [0, 0, 0]
    assert ex.feedback["semantic_status"] == "unconfirmed"
    np.testing.assert_array_equal(ex.next_action()["left_ee_pose"], first["left_ee_pose"])
    ex.observe(adapt(raw(), 2))
    ex.next_action()
    ex.observe(adapt(raw(), 3))
    assert ex.remaining == 0 and ex.feedback["steps"] == 3
    ex.load(decision())
    np.testing.assert_array_equal(ex.next_action()["left_ee_pose"], first["left_ee_pose"])


def test_interrupt_at_control_boundary():
    ex = Executor()
    ex.observe(adapt(raw(), 0))
    ex.load(decision())
    ex.next_action()
    data = raw()
    data["state"]["left_ee_pose"][0] += 0.1
    ex.observe(adapt(data, 1))
    assert ex.remaining == 0 and ex.feedback["interrupted"]


def test_invalid_quaternion_fails():
    data = raw()
    data["state"]["left_ee_pose"][3:] = [0, 0, 0, 0]
    with pytest.raises(ValueError):
        adapt(data, 0)
