"""Goal prefixes preserve direction, rotation and strict actuator bounds."""
import numpy as np
from scipy.spatial.transform import Rotation
from egg_harness.segments import compile_segment
from egg_harness.skill_policy import validate

rng = np.random.default_rng(17)
for phase in ("fine", "transit"):
    for _ in range(500):
        p = rng.uniform(-1, 1, 3)
        start = Rotation.random(random_state=rng)
        end = Rotation.random(random_state=rng)
        q = start.as_quat()[[3, 0, 1, 2]]
        goal = p + rng.uniform(-.5, .5, 3)
        target_q = end.as_quat()[[3, 0, 1, 2]] * rng.uniform(.8, 1.2)
        obs = {"state": {"left_ee_pose": [*p, *q]}}
        action = {"arm": "left", "toward": {"position": goal.tolist(), "quaternion": target_q.tolist()}, "steps": 3}
        segment, info = compile_segment(action, phase, obs)
        validate(segment, phase, obs)
        assert np.allclose(np.asarray(segment["position"]) - p, (goal-p)*info["fraction"])
        actual = Rotation.from_quat(np.asarray(segment["quaternion"])[[1,2,3,0]])
        assert np.isclose((actual*start.inv()).magnitude(), (end*start.inv()).magnitude()*info["fraction"])
obs = {"state": {"left_ee_pose": [0,0,1,1,0,0,0]}}
direct = {"arm":"left", "position":[.03,0,1], "steps":2}
segment, info = compile_segment(direct, "fine", obs)
assert segment == direct and info is None
try:
    validate(segment, "fine", obs)
except ValueError:
    pass
else:
    raise AssertionError("Direct out-of-bounds request silently repaired")
for goal in ({"quaternion":[0,0,0,0]}, {"position":[float("nan"),0,1]}, {"unknown":1}):
    try:
        compile_segment({"arm":"left", "toward":goal, "steps":2}, "fine", obs)
    except ValueError:
        pass
    else:
        raise AssertionError("Malformed goal accepted")
print("1000 pose prefixes satisfy bounds and geometry; malformed/direct requests fail fast")
