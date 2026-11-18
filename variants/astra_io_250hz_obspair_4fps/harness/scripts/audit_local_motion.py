"""Audit a fixed completed prefix using recorded public robot observations."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


def audit(episode, through_step):
    if through_step < 1:
        raise ValueError("Select a positive, already observed final step")
    records = [json.loads(line) for line in (episode / "actions.jsonl").read_text().splitlines()]
    records = [record for record in records if record["step"] <= through_step]
    if [record["step"] for record in records] != list(range(1, through_step + 1)):
        raise ValueError("Action prefix is missing steps or contains duplicates")
    evidence = {}
    def state(step):
        path = episode / "frames" / ("initial.json" if step == 0 else f"step-{step:04d}.json")
        raw = path.read_bytes()
        evidence[str(path)] = hashlib.sha256(raw).hexdigest()
        record = json.loads(raw)
        if record["step"] != step:
            raise ValueError("Observation step mismatch")
        return record["state"]
    before = state(0)
    rows = []
    for record in records:
        after = state(record["step"])
        for arm in ("left", "right"):
            target_joints = np.asarray(record["action"][arm + "_arm_joint_state"])
            delta = float(np.max(np.abs(target_joints - before[arm + "_arm_joint_state"])))
            desired = np.asarray(record["cartesian_target"][arm + "_ee_pose"])
            achieved = np.asarray(after[arm + "_ee_pose"])
            position = float(np.linalg.norm(achieved[:3] - desired[:3]))
            rotation = float(np.rad2deg((Rotation.from_quat(achieved[[4, 5, 6, 3]]) * Rotation.from_quat(desired[[4, 5, 6, 3]]).inv()).magnitude()))
            if not np.isfinite([delta, position, rotation]).all():
                raise ValueError("Nonfinite motion evidence")
            rows.append(dict(step=record["step"], arm=arm, joint_delta_rad=delta, wrist_error_m=position, rotation_error_deg=rotation))
        before = after
    interruptions = [json.loads(line) for line in (episode / "interruptions.jsonl").read_text().splitlines()]
    counts = Counter(row["reason"] for row in interruptions if row["step"] <= through_step)
    maximum = max(row["joint_delta_rad"] for row in rows)
    return dict(episode=str(episode), through_step=through_step, actions=len(records),
                max_joint_delta_rad=maximum, joint_bound_passed=maximum <= .150001,
                max_wrist_error_m=max(row["wrist_error_m"] for row in rows),
                max_rotation_error_deg=max(row["rotation_error_deg"] for row in rows),
                interruption_counts=dict(counts),
                action_prefix_sha256=hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(),
                observation_sha256=evidence, rows=rows,
                scope="Fixed observed prefix; kinematic continuity and tracking only, not grasp retention, collision freedom or task completion")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("episode", type=Path)
    parser.add_argument("--through-step", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit(args.episode, args.through_step)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key not in ("rows", "observation_sha256")}))
    if not report["joint_bound_passed"]:
        raise SystemExit("Recorded joint command exceeds the configured local bound")
