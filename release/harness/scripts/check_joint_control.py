"""Independent FK sampling checks conservative multijoint control bounds."""
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from egg_harness.kinematics import Kinematics
from egg_harness.joint_control import (ARMS, FINGER_PROXY, geometry, joint_increment,
    joint_preview, joint_semantics, joint_targets, plan_unfold, validate_joint_action, validate_joint_segment, validate_unfold)

MODEL = Kinematics(json.loads(Path("configs/x5-kinematics.json").read_text()))


def state_for(q=None):
    result = {}
    for arm, yaw in (("left", 0.), ("right", np.pi)):
        values = np.array([.3, 3., 4.6, -1.9, .16, .48]) if q is None else np.array(q)
        base = np.eye(4); base[:3, :3] = Rotation.from_euler("z", yaw).as_matrix()
        base[:3, 3] = [.1, .2 if arm == "left" else -.2, .1]
        pose = base @ MODEL.forward(values)
        result[arm + "_arm_joint_state"] = values.tolist()
        result[arm + "_ee_pose"] = np.r_[pose[:3, 3], Rotation.from_matrix(pose[:3, :3]).as_quat()[[3, 0, 1, 2]]].tolist()
    return result


def reference_points(q, base):
    transform = base.copy(); result = []
    for origin, axis, angle in zip(MODEL.origins, MODEL.axes, q):
        rotation = np.eye(4); rotation[:3, :3] = Rotation.from_rotvec(axis * angle).as_matrix()
        transform = transform @ origin @ rotation
        result.append(transform[:3, 3].copy())
    return np.vstack([result, transform[:3, 3] + transform[:3, :3] @ FINGER_PROXY])


def reject(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("Invalid request accepted")


state = state_for()
changes = {"left": {"joint2": .6, "joint3": -.6, "joint4": .35}, "right": {"joint1": -.5, "joint6": .3}}
targets = joint_targets(MODEL, state, changes)
joints, poses, metadata = joint_increment(MODEL, state, targets)
for arm in ARMS:
    q = np.asarray(state[arm + "_arm_joint_state"])
    delta = joints[arm] - q
    selected = {int(name[5:]) - 1 for name in changes[arm]}
    assert all(delta[index] != 0 for index in selected), "Simultaneous selected joints must progress"
    assert all(delta[index] == 0 for index in set(range(6)) - selected), "Unselected joints must hold"
    assert np.max(np.abs(delta)) <= .15 + 1e-12
    base = MODEL.observed_base(q, state[arm + "_ee_pose"])
    path = np.asarray([reference_points(q + delta * fraction, base) for fraction in np.linspace(0, 1, 1001)])
    sampled_lengths = np.linalg.norm(np.diff(path, axis=0), axis=2).sum(axis=0)
    bounds = np.asarray(metadata["arms"][arm]["per_point_swept_bound_m"])
    assert np.all(sampled_lengths <= bounds + 1e-10), (sampled_lengths, bounds)
    assert bounds.max() <= .024 + 1e-12

single = joint_targets(MODEL, state, {"left": {"joint3": -.2}})
held, _, _ = joint_increment(MODEL, state, single)
assert np.array_equal(held["right"], state["right_arm_joint_state"])
assert np.array_equal(held["left"][[0, 1, 3, 4, 5]], np.asarray(state["left_arm_joint_state"])[[0, 1, 3, 4, 5]])
drifted = state_for()
drifted["left_arm_joint_state"][0] += .005
correction = {a: np.asarray(state[a + "_arm_joint_state"]) for a in ARMS}
_, exact_bounds = validate_joint_segment(MODEL, drifted, correction)
assert abs(exact_bounds["arms"]["left"]["delta_rad"][0] + .005) < 1e-12
oversized = {a: q.copy() for a, q in correction.items()}
oversized["left"][0] += .14
oversized["left"][1] += .14
reject(lambda: validate_joint_segment(MODEL, state, oversized))
preview = joint_preview(MODEL, state, changes, 6)
assert len(preview["path"]) == 6 and not preview["next_motion_verified"]
assert np.max(np.abs(preview["remaining_delta_rad"]["left"])) > 0, "Large bounded commands honestly report partial progress"

semantics = joint_semantics(MODEL, state)
for index in range(6):
    left = semantics["arms"]["left"][index]
    right = semantics["arms"]["right"][index]
    assert left["urdf_name"] == MODEL.joints[index]["name"]
    expected = Rotation.from_euler("z", np.pi).apply(left["preview"]["positive"]["finger_delta_world_m"])
    assert np.allclose(right["preview"]["positive"]["finger_delta_world_m"], expected, atol=1e-10), "Mirrored arm preview must follow actual base"

for value in (True, float("nan"), float("inf"), .6001, "0.1"):
    reject(lambda value=value: validate_joint_action({"joint_delta": {"left": {"joint2": value}}, "steps": 1}, "transit"))
for steps in (True, 0, 7, 1.5):
    reject(lambda steps=steps: validate_joint_action({"joint_delta": {"left": {"joint2": .1}}, "steps": steps}, "transit"))
reject(lambda: validate_joint_action({"joint_delta": {"left": {"joint7": .1}}, "steps": 1}, "transit"))
reject(lambda: validate_joint_action({"joint_delta": {"left": {"joint2": .1}}, "steps": 1}, "pickup"))
limit_state = state_for([0., 0., 0., 0., 0., 3.1])
reject(lambda: joint_targets(MODEL, limit_state, {"left": {"joint6": .1}}))

measured = geometry(MODEL, state, "left")
points = np.vstack([measured["origins_world_m"], measured["finger_proxy_world_m"]])
box = [(points.min(axis=0) - .19).tolist(), (points.max(axis=0) + .19).tolist()]
spec = {"arm": "left", "reason": "descent_blocked", "clearance_box": box, "max_steps": 24}
plan = plan_unfold(MODEL, state, spec)
assert 1 <= plan["planned_steps"] <= 24 and plan["geometry_score_gain"] > 0
assert not plan["next_motion_verified"]
delta = plan["targets"]["left"] - state["left_arm_joint_state"]
assert delta[1] != 0 and delta[2] != 0, "Unfold must support coordinated shoulder and elbow"
before = np.asarray(state["left_arm_joint_state"])
base = MODEL.observed_base(before, state["left_ee_pose"])
for frame in plan["path"]:
    after = np.asarray(frame["joints"]["left"])
    for fraction in np.linspace(0, 1, 101):
        points = reference_points(before + (after - before) * fraction, base)
        assert np.all(points >= np.asarray(box[0]) + .03 - 1e-10)
        assert np.all(points <= np.asarray(box[1]) - .03 + 1e-10)
    assert np.array_equal(frame["joints"]["right"], state["right_arm_joint_state"])
    before = after
reject(lambda: plan_unfold(MODEL, state, {**spec, "clearance_box": [[0, 0, 0], [.1, .1, .1]]}))
reject(lambda: validate_unfold({"unfold_arm": {**spec, "max_steps": 25}}, "transit"))
reject(lambda: validate_unfold({"unfold_arm": {**spec, "clearance_box": [[False, 0, 0], [1, 1, 1]]}}, "transit"))
print("Joint control: simultaneous and held joints, conservative complete-path bounds, mirrored previews, limits, coordinated unfold and unsafe-plan rejection passed")
