"""Bounded public-kinematics joint control and deterministic empty-hand unfolding.

Bounds cover chain origins and a fingertip proxy, not collision meshes. A declared
clear box is supplied from visual reasoning; this module cannot certify it is free.
"""
from itertools import product
from numbers import Real

import numpy as np
from scipy.spatial.transform import Rotation

ARMS = ("left", "right")
JOINT_NAMES = tuple("joint%d" % n for n in range(1, 7))
FINGER_PROXY = np.array([.138, 0., 0.])
MAX_JOINT_STEP = .15
MAX_SWEEP = .024
ENVELOPE_MARGIN = .03


def validate_joint_action(action, phase):
    if not isinstance(action, dict) or set(action) != {"joint_delta", "steps"} or phase != "transit":
        raise ValueError("Joint adjustment requires transit and joint_delta, steps")
    deltas = action["joint_delta"]
    if not isinstance(deltas, dict) or not deltas or set(deltas) - set(ARMS):
        raise ValueError("joint_delta must select left and/or right arm")
    for joints in deltas.values():
        if not isinstance(joints, dict) or not joints or set(joints) - set(JOINT_NAMES):
            raise ValueError("Select one or more joint1..joint6 increments")
        for delta in joints.values():
            if isinstance(delta, bool) or not isinstance(delta, Real) or not np.isfinite(delta) or abs(delta) > .6:
                raise ValueError("Joint increments must be finite radians within +/-0.6")
    if type(action["steps"]) is not int or not 1 <= action["steps"] <= 6:
        raise ValueError("Joint adjustment requires 1..6 native steps")
    return deltas, action["steps"]


def joint_targets(model, state, deltas):
    validate_joint_action({"joint_delta": deltas, "steps": 1}, "transit")
    targets = {}
    for arm, changes in deltas.items():
        target = model.joints_array(state[arm + "_arm_joint_state"]).copy()
        for name, delta in changes.items():
            target[JOINT_NAMES.index(name)] += delta
        targets[arm] = model.joints_array(target)
    return targets


def _geometry(model, q, base):
    transform = base.copy()
    origins, axes = [], []
    for origin, axis, angle in zip(model.origins, model.axes, q):
        transform = transform @ origin
        origins.append(transform[:3, 3].copy())
        axes.append(transform[:3, :3] @ axis)
        rotation = np.eye(4)
        rotation[:3, :3] = Rotation.from_rotvec(axis * angle).as_matrix()
        transform = transform @ rotation
    finger = transform[:3, 3] + transform[:3, :3] @ FINGER_PROXY
    return np.vstack([origins, finger]), np.asarray(axes), transform


def geometry(model, state, arm):
    q = model.joints_array(state[arm + "_arm_joint_state"])
    base = model.observed_base(q, state[arm + "_ee_pose"])
    points, _, _ = _geometry(model, q, base)
    return {"origins_world_m": points[:6].tolist(), "elbow_world_m": points[2].tolist(),
            "wrist_world_m": points[5].tolist(), "finger_proxy_world_m": points[6].tolist()}


def _radii(model):
    # A downstream point cannot be farther from joint i than the sum of the
    # intervening fixed translation lengths, independently of all joint angles.
    # Integral |dq_i| * radius bounds the entire simultaneous trajectory, even
    # when its endpoints coincide or intermediate joints sweep farther.
    lengths = np.array([np.linalg.norm(origin[:3, 3]) for origin in model.origins])
    radii = np.zeros((7, 6))
    for point in range(7):
        for joint in range(6):
            if point > joint:
                radii[point, joint] = sum(lengths[joint + 1:min(point + 1, 6)])
                if point == 6:
                    radii[point, joint] += np.linalg.norm(FINGER_PROXY)
    return radii * np.linalg.norm(np.asarray(model.axes), axis=1)[None, :]


def joint_increment(model, state, targets):
    if not isinstance(targets, dict) or not targets or set(targets) - set(ARMS):
        raise ValueError("Joint targets must select known arms")
    radii = _radii(model)
    solutions, poses = {}, {}
    metadata = {"arms": {}, "max_joint_delta_rad": MAX_JOINT_STEP, "max_swept_bound_m": MAX_SWEEP}
    for arm in ARMS:
        q = model.joints_array(state[arm + "_arm_joint_state"])
        target = model.joints_array(targets[arm]) if arm in targets else q
        requested = target - q
        scale = min(1., MAX_JOINT_STEP / max(float(np.max(np.abs(requested))), 1e-30))
        raw_sweep = radii @ np.abs(requested)
        scale = min(scale, MAX_SWEEP / max(float(raw_sweep.max()), 1e-30))
        delta = requested * scale
        candidate = model.joints_array(q + delta)
        base = model.observed_base(q, state[arm + "_ee_pose"])
        predicted = base @ model.forward(candidate)
        poses[arm] = np.r_[predicted[:3, 3], Rotation.from_matrix(predicted[:3, :3]).as_quat()[[3, 0, 1, 2]]]
        solutions[arm] = candidate
        point_bounds = radii @ np.abs(delta)
        if np.max(np.abs(delta)) > MAX_JOINT_STEP + 1e-12 or point_bounds.max() > MAX_SWEEP + 1e-12:
            raise ValueError("Joint increment violated conservative motion bounds")
        metadata["arms"][arm] = {"delta_rad": delta.tolist(), "per_point_swept_bound_m": point_bounds.tolist(),
                                  "swept_bound_m": float(point_bounds.max()),
                                  "remaining_delta_rad": (target - candidate).tolist()}
    return solutions, poses, metadata


def joint_increment_with_holds(model, state, targets, initial, masks):
    """Reserve each point's sweep budget for exact held-coordinate correction."""
    radii = _radii(model)
    joints = {}
    for arm in ARMS:
        current = model.joints_array(state[arm + '_arm_joint_state'])
        target = model.joints_array(targets[arm])
        fixed = model.joints_array(initial[arm])
        mask = np.asarray(masks[arm])
        if mask.shape != (6,) or mask.dtype != bool:
            raise ValueError('Joint hold mask must contain six booleans')
        correction = np.where(mask, 0., fixed-current)
        reserved = radii @ np.abs(correction)
        if np.max(np.abs(correction)) > MAX_JOINT_STEP or np.max(reserved) > MAX_SWEEP:
            raise ValueError('Held correction alone exceeds motion budget')
        requested = np.where(mask, target-current, 0.)
        moving = radii @ np.abs(requested)
        scale = min(1., MAX_JOINT_STEP/max(float(np.max(np.abs(requested))), 1e-30))
        positive = moving > 1e-30
        if positive.any():
            scale = min(scale, float(np.min((MAX_SWEEP-reserved[positive])/moving[positive])))
        joints[arm] = current + correction + requested*scale
    poses, bounds = validate_joint_segment(model, state, joints)
    for arm in ARMS:
        bounds['arms'][arm]['remaining_delta_rad'] = (np.asarray(targets[arm])-joints[arm]).tolist()
    return joints, poses, bounds


def validate_joint_segment(model, state, joints):
    """Validate an exact commanded segment, including initial-hold corrections."""
    if not isinstance(joints, dict) or set(joints) != set(ARMS):
        raise ValueError("Exact joint segment must specify both arms")
    radii = _radii(model)
    poses = {}
    metadata = {"arms": {}, "max_joint_delta_rad": MAX_JOINT_STEP, "max_swept_bound_m": MAX_SWEEP}
    for arm in ARMS:
        current = model.joints_array(state[arm + "_arm_joint_state"])
        candidate = model.joints_array(joints[arm])
        delta = candidate - current
        point_bounds = radii @ np.abs(delta)
        if np.max(np.abs(delta)) > MAX_JOINT_STEP + 1e-12 or point_bounds.max() > MAX_SWEEP + 1e-12:
            raise ValueError("Exact held-joint correction exceeds conservative motion bounds")
        predicted = model.observed_base(current, state[arm + "_ee_pose"]) @ model.forward(candidate)
        poses[arm] = np.r_[predicted[:3, 3], Rotation.from_matrix(predicted[:3, :3]).as_quat()[[3, 0, 1, 2]]]
        metadata["arms"][arm] = {"delta_rad": delta.tolist(), "per_point_swept_bound_m": point_bounds.tolist(),
                                  "swept_bound_m": float(point_bounds.max()), "remaining_delta_rad": [0.] * 6}
    return poses, metadata


def _advance(state, joints, poses):
    result = dict(state)
    for arm in ARMS:
        result[arm + "_arm_joint_state"] = joints[arm].tolist()
        result[arm + "_ee_pose"] = poses[arm].tolist()
    return result


def _frame(model, state, joints, poses, bounds):
    return {"joints": {a: joints[a].tolist() for a in ARMS},
            "poses": {a: poses[a].tolist() for a in ARMS},
            "geometry": {a: geometry(model, state, a) for a in ARMS}, "bounds": bounds}


def joint_preview(model, state, deltas, steps):
    validate_joint_action({"joint_delta": deltas, "steps": steps}, "transit")
    targets = joint_targets(model, state, deltas)
    simulated, path = dict(state), []
    for _ in range(steps):
        joints, poses, bounds = joint_increment(model, simulated, targets)
        simulated = _advance(simulated, joints, poses)
        path.append(_frame(model, simulated, joints, poses, bounds))
    return {"targets": {a: q.tolist() for a, q in targets.items()}, "path": path, "planned_steps": steps,
            "remaining_delta_rad": {a: (q - simulated[a + "_arm_joint_state"]).tolist() for a, q in targets.items()},
            "next_motion_verified": False,
            "scope": "Public FK prediction; swept bounds cover chain origins and fingertip proxy, not mesh collisions"}


def joint_semantics(model, state):
    result = {"sign_convention": "Positive radians rotate about the URDF axis by the right-hand rule; previews use each measured arm base",
              "preview_delta_rad": .05, "arms": {}}
    for arm in ARMS:
        q = model.joints_array(state[arm + "_arm_joint_state"])
        base = model.observed_base(q, state[arm + "_ee_pose"])
        points, axes, _ = _geometry(model, q, base)
        records = []
        for i, definition in enumerate(model.joints):
            previews = {}
            for label, delta in (("positive", .05), ("negative", -.05)):
                candidate = q.copy(); candidate[i] += delta
                if candidate[i] < model.lower[i] or candidate[i] > model.upper[i]:
                    previews[label] = {"available": False, "reason": "joint_limit"}
                    continue
                moved, _, _ = _geometry(model, candidate, base)
                previews[label] = {"available": True,
                                   "elbow_delta_world_m": (moved[2] - points[2]).tolist(),
                                   "wrist_delta_world_m": (moved[5] - points[5]).tolist(),
                                   "finger_delta_world_m": (moved[6] - points[6]).tolist()}
            records.append({"control_name": JOINT_NAMES[i], "urdf_name": definition["name"],
                            "topology_role": ("base yaw", "shoulder pitch", "elbow pitch", "wrist pitch", "wrist yaw", "wrist roll")[i],
                            "axis_joint_frame": model.axes[i].tolist(), "axis_world": axes[i].tolist(),
                            "origin_world_m": points[i].tolist(), "angle_rad": float(q[i]),
                            "limits_rad": [float(model.lower[i]), float(model.upper[i])], "preview": previews})
        result["arms"][arm] = records
    return result


def validate_unfold(action, phase):
    if not isinstance(action, dict) or phase != "transit" or set(action) != {"unfold_arm"}:
        raise ValueError("Unfold requires transit and only unfold_arm")
    spec = action["unfold_arm"]
    if not isinstance(spec, dict) or set(spec) != {"arm", "reason", "clearance_box", "max_steps"}:
        raise ValueError("Unfold requires arm, reason, clearance_box, max_steps")
    if spec["arm"] not in ARMS or spec["reason"] not in ("approach_blocked", "descent_blocked", "withdrawal_blocked"):
        raise ValueError("Unfold requires one arm and an obstructed next motion")
    if type(spec["max_steps"]) is not int or not 1 <= spec["max_steps"] <= 24:
        raise ValueError("Unfold requires max_steps in 1..24")
    raw = spec["clearance_box"]
    if not isinstance(raw, (list, tuple)) or any(isinstance(v, bool) or not isinstance(v, Real) for row in raw if isinstance(row, (list, tuple)) for v in row):
        raise ValueError("clearance_box requires numeric world coordinates")
    try:
        box = np.asarray(raw, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid clearance_box") from error
    if box.shape != (2, 3) or not np.isfinite(box).all() or np.any(box[1] - box[0] <= 2 * ENVELOPE_MARGIN) or np.any(box[1] - box[0] > 1.):
        raise ValueError("clearance_box must be ordered finite bounds, 6cm..1m per axis")
    return spec


def check_joint_envelope(model, state, arm, box):
    q = model.joints_array(state[arm + "_arm_joint_state"])
    points, _, _ = _geometry(model, q, model.observed_base(q, state[arm + "_ee_pose"]))
    low, high = np.asarray(box, dtype=float)
    if np.any(points < low + ENVELOPE_MARGIN) or np.any(points > high - ENVELOPE_MARGIN):
        raise ValueError("Joint geometry leaves declared clear envelope (3cm margin)")
    return points


def check_joint_envelope_segment(model, before, after, arm, box):
    q0 = model.joints_array(before[arm + "_arm_joint_state"])
    q1 = model.joints_array(after[arm + "_arm_joint_state"])
    base = model.observed_base(q0, before[arm + "_ee_pose"])
    low, high = np.asarray(box, dtype=float)
    # Every point stays within its per-subinterval path-length radius from the
    # sampled start. Thus the 3cm envelope holds BETWEEN samples as well.
    radius = (_radii(model) @ np.abs(q1 - q0)) / 4
    for fraction in (0., .25, .5, .75):
        points, _, _ = _geometry(model, q0 + (q1 - q0) * fraction, base)
        if np.any(points - radius[:, None] < low + ENVELOPE_MARGIN) or np.any(points + radius[:, None] > high - ENVELOPE_MARGIN):
            raise ValueError("Unfold swept path leaves declared clear envelope")
    check_joint_envelope(model, after, arm, box)


def _score(model, state, arm, box):
    q = model.joints_array(state[arm + "_arm_joint_state"])
    points, _, _ = _geometry(model, q, model.observed_base(q, state[arm + "_ee_pose"]))
    upper, fore = points[2] - points[1], points[3] - points[2]
    extension = float(np.dot(upper, fore) / (np.linalg.norm(upper) * np.linalg.norm(fore)))
    separation = float(np.linalg.norm(points[3] - points[1]))
    low, high = np.asarray(box, dtype=float)
    space = float(np.min(np.minimum(points - low, high - points)))
    height = float(upper[2] / np.linalg.norm(upper))
    return 1.2 * extension + 1.5 * separation + .3 * height + .5 * space


def plan_unfold(model, state, spec):
    validate_unfold({"unfold_arm": spec}, "transit")
    arm, box = spec["arm"], spec["clearance_box"]
    check_joint_envelope(model, state, arm, box)
    current = model.joints_array(state[arm + "_arm_joint_state"])
    baseline = _score(model, state, arm, box)
    best = None
    ranked = []
    # 144 deterministic candidates coordinate shoulder and elbow with wrist
    # pitch, plus both proximal yaw directions. No measured object state.
    for shoulder, elbow, wrist, yaw in product((-.5, -.25, .25, .5), (-.6, -.3, .3, .6), (-.35, 0., .35), (-.2, 0., .2)):
        target = current.copy()
        target[[0, 1, 2, 3]] += [yaw, shoulder, elbow, wrist]
        if np.any(target < model.lower) or np.any(target > model.upper):
            continue
        base = model.observed_base(current, state[arm + "_ee_pose"])
        predicted = base @ model.forward(target)
        terminal = dict(state)
        terminal[arm + "_arm_joint_state"] = target.tolist()
        terminal[arm + "_ee_pose"] = np.r_[predicted[:3, 3], Rotation.from_matrix(predicted[:3, :3]).as_quat()[[3, 0, 1, 2]]].tolist()
        gain = _score(model, terminal, arm, box) - baseline
        if gain > .015:
            ranked.append((gain, target))
    # Rank cheaply, then construct bounded paths for the 12 strongest geometry
    # candidates. Explicitly reject if this finite search finds no safe plan.
    ranked.sort(key=lambda item: item[0], reverse=True)
    for _, target in ranked[:12]:
        simulated, path = dict(state), []
        feasible = True
        for _ in range(spec["max_steps"]):
            if np.max(np.abs(target - simulated[arm + "_arm_joint_state"])) < 1e-8:
                break
            joints, poses, bounds = joint_increment(model, simulated, {arm: target})
            following = _advance(simulated, joints, poses)
            try:
                check_joint_envelope_segment(model, simulated, following, arm, box)
            except ValueError:
                feasible = False
                break
            simulated = following
            path.append(_frame(model, simulated, joints, poses, bounds))
        if not feasible or not path:
            continue
        gain = _score(model, simulated, arm, box) - baseline
        if gain <= .015:
            continue
        ranking = (gain, -len(path), -float(np.linalg.norm(target - current)))
        if best is None or ranking > best[0]:
            achieved = np.asarray(simulated[arm + "_arm_joint_state"])
            best = (ranking, {"targets": {arm: achieved}, "path": path, "planned_steps": len(path),
                              "geometry_score_gain": float(gain), "candidate_target_rad": target.tolist(),
                              "target_reachable_within_budget": bool(np.max(np.abs(target - achieved)) < 1e-8),
                              "next_motion_verified": False,
                              "scope": "Clear-box proxy geometry only; execute empty-handed and reattempt blocked motion to assess recovery"})
    if best is None:
        raise ValueError("No improving unfold path within declared clear envelope and step budget")
    return best[1]
