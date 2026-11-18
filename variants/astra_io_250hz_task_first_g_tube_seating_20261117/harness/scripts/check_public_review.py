"""Exercise real historical RGB, hostile paths and the zero-motion policy route."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

from egg_harness.public_review import CAMERAS, review_frames
from egg_harness import skill_policy


def reject(callback):
    try:
        callback()
    except (ValueError, KeyError, TypeError, FileNotFoundError):
        return
    raise AssertionError("Unsafe review request accepted")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", type=Path, default=Path("/workspace/Astra-Robodojo-RSI-0914/artifacts/press-by-number-dev21-visible-v5/seed-21"))
    args = parser.parse_args()
    requests = [{"step": step, "camera": "cam_head"} for step in (38, 40, 48)]
    images, records = review_frames(args.episode, 48, requests)
    assert len(images) == 3 and [row["step"] for row in records] == [38, 40, 48]
    public_keys = {arm + suffix for arm in ("left", "right") for suffix in
                   ("_ee_pose", "_ee_joint_state", "_gripper_measured_joints", "_arm_joint_state")}
    required_keys = {key for key in public_keys if not key.endswith("_arm_joint_state")}
    for row in records:
        assert required_keys <= set(row["state"]) <= public_keys
        assert Path(images[row["image"]]).is_file()

    for invalid in ([{"step": 49, "camera": "cam_head"}],
                    [{"step": True, "camera": "cam_head"}],
                    [{"step": -1, "camera": "cam_head"}],
                    [{"step": 38, "camera": "depth"}],
                    [{"step": 38, "camera": "../cam_head"}],
                    [{"step": 38, "camera": "cam_head", "path": "/tmp/x"}],
                    [requests[0], requests[0]], [], requests * 3):
        reject(lambda invalid=invalid: review_frames(args.episode, 48, invalid))

    with tempfile.TemporaryDirectory(prefix="public-review-check-") as directory:
        root = Path(directory); episode = root / "episode"; frames = episode / "frames"
        frames.mkdir(parents=True)
        originals = {}
        for step in (38, 40, 48):
            original = json.loads((args.episode / "frames" / f"step-{step:04d}.json").read_text())
            for camera in CAMERAS:
                destination = frames / f"step-{step:04d}-{camera}.jpg"
                shutil.copyfile(original["cameras"][camera], destination)
                original["cameras"][camera] = str(destination)
            original["state"]["hidden_object_poses"] = {"secret": "HIDDEN_SENTINEL"}
            original["evaluator_score"] = "HIDDEN_SENTINEL"
            originals[step] = original
            (frames / f"step-{step:04d}.json").write_text(json.dumps(original))
        _, safe = review_frames(episode, 48, requests)
        assert "HIDDEN_SENTINEL" not in json.dumps(safe), "Top-level hidden state leaked"
        record_path = frames / "step-0038.json"
        outside = root / "outside.jpg"; shutil.copyfile(images["review_38_cam_head"], outside)
        for rgb in (outside, frames / "linked.jpg"):
            if rgb.name == "linked.jpg": rgb.symlink_to(outside)
            changed = deepcopy(originals[38]); changed["cameras"]["cam_head"] = str(rgb)
            record_path.write_text(json.dumps(changed))
            reject(lambda: review_frames(episode, 48, [requests[0]]))
        record_path.unlink()
        outside_record = root / "outside.json"; outside_record.write_text(json.dumps(originals[38]))
        record_path.symlink_to(outside_record)
        reject(lambda: review_frames(episode, 48, [requests[0]]))
        record_path.unlink(); record_path.write_text(json.dumps(originals[38]))
        other_episode = root / "other_episode"; other_episode.mkdir()
        (other_episode / "frames").symlink_to(frames, target_is_directory=True)
        reject(lambda: review_frames(other_episode, 48, [requests[0]]))
        changed = deepcopy(originals[38]); changed["step"] = 39
        record_path.write_text(json.dumps(changed))
        reject(lambda: review_frames(episode, 48, [requests[0]]))
        for corrupt in ({"hidden_object_poses": "HIDDEN_SENTINEL"}, ["HIDDEN_SENTINEL"] * 7, [float("nan")] * 7):
            changed = deepcopy(originals[38]); changed["state"]["left_ee_pose"] = corrupt
            record_path.write_text(json.dumps(changed))
            reject(lambda: review_frames(episode, 48, [requests[0]]))
        record_path.write_text(json.dumps(originals[38]))

        calls = []; observed = []
        current = deepcopy(originals[48]); current["state"].pop("hidden_object_poses")
        current.pop("evaluator_score"); current["ended"] = False
        class RobotStub:
            def __init__(self, episode, trace):
                self.obs = {**deepcopy(current), "action_interface": "tcp"}
                self.home = {arm: self.obs["state"][arm + "_ee_pose"] for arm in ("left", "right")}
            def command(self, *positional, **keywords):
                calls.append((positional, keywords))
                raise AssertionError("Review must not issue robot commands")
        class VisionStub:
            def __init__(self, config, trace): self.calls = 0
            def observe(self, feedback, step, ended): pass
            def fail_pending(self, error, step): pass
            def ask(self, context, camera_images, system):
                self.calls += 1; context = json.loads(context); observed.append((context, dict(camera_images)))
                if self.calls == 1:
                    return {"memory": "Inspect recorded touch", "phase": "transit", "action": {"review": requests}}
                assert context["step"] == 48
                assert [row["step"] for row in context["reviewed_observations"]] == [38, 40, 48]
                assert context["previous_result"]["native_steps_executed"] == 0
                assert all("review_%d_cam_head" % step in camera_images for step in (38, 40, 48))
                assert "HIDDEN_SENTINEL" not in json.dumps(context)
                return {"memory": "Historical RGB received", "phase": "transit", "action": {"stop": True, "reason": "test_complete"}}
        skill = root / "skills" / "fixture"; skill.mkdir(parents=True); (skill / "SKILL.md").write_text("Inspect public RGB.")
        memory = root / "memory.json"; memory.write_text(json.dumps({"head_camera": {}}))
        config = root / "config.json"; config.write_text(json.dumps({"action_interface":"tcp", "skills_root": str(root / "skills"), "skill_names": ["fixture"],
            "memory": str(memory), "max_model_calls": 3, "public_task_requirements": "Press visible buttons"}))
        trace = root / "trace"
        with patch.object(skill_policy, "Robot", RobotStub), patch.object(skill_policy, "Vision", VisionStub), patch.object(sys, "argv",
                ["skill_policy", "--episode", str(episode), "--config", str(config), "--trace", str(trace)]):
            try: skill_policy.main()
            except RuntimeError as error: assert str(error) == "Astra stopped: test_complete"
            else: raise AssertionError("Expected mock stop")
        assert len(observed) == 2 and calls == []
        result = json.loads((trace / "policy-result.json").read_text())
        assert result["steps"] == 48 and result["model_calls"] == 2 and result["invalid_actions"] == 0
    print("Public review: real press 38/40/48, public-only poses, invalid/future/duplicate/cross-episode paths, nested value rejection and policy zero-motion review passed")


if __name__ == "__main__": main()
