"""Check bounded local IK on reachable poses and reject unreachable requests."""
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from egg_harness.kinematics import Kinematics

model = Kinematics(json.loads(Path("configs/x5-kinematics.json").read_text()))
def pose(matrix):
    return np.r_[matrix[:3, 3], Rotation.from_matrix(matrix[:3, :3]).as_quat()[[3, 0, 1, 2]]]
rng = np.random.default_rng(914)
for _ in range(20):
    start = rng.uniform(-1, 1, 6)
    target = start + rng.uniform(-.03, .03, 6)
    before, after = pose(model.forward(start)), pose(model.forward(target))
    solved = model.solve_local(start, before, after)
    assert np.max(np.abs(solved-start)) <= .150001
    assert np.linalg.norm(model.forward(solved)[:3, 3]-model.forward(target)[:3, 3]) < .001
start = np.zeros(6)
near_zero = np.array([5e-13,-1e-15,1e-17,8e-16,-1e-12,2.5e-12])
reachable = near_zero.copy(); reachable[2] += .03
solution = model.solve_local(near_zero, pose(model.forward(near_zero)), pose(model.forward(reachable)))
assert np.linalg.norm(model.forward(solution)[:3,3]-model.forward(reachable)[:3,3]) < .001
before = pose(model.forward(start))
far = before.copy(); far[0] += 10
try:
    model.solve_local(start, before, far)
except ValueError:
    pass
else:
    raise AssertionError("Unreachable target accepted")
for bad in ([0]*5, [np.nan]*6, [11]*6):
    try:
        model.forward(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid joint state accepted")
print("20 reachable local solves bounded; unreachable and invalid states rejected")
