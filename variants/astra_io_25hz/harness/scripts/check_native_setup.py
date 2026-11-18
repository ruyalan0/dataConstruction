"""Check official setup order and exclusion of unstable native samples."""
import json
import tempfile
from pathlib import Path
from native_setup import prepare_native_evaluation
from audit_curriculum import summarize

class Env:
    interact = True
    def __init__(self): self.events = []
    def run_reward(self): self.events.append("reward")
    def get_score(self): self.events.append("score")
    def get_running_env_idx_list(self): return [0, 2]
    def query_support_arm_traj(self, env_idx): self.events.append(("support", env_idx))

env = Env()
assert prepare_native_evaluation(env)["support_trajectory_primed_for"] == [0, 2]
assert env.events == ["reward", "score", ("support", 0), ("support", 2)]
env = Env(); env.interact = False
assert prepare_native_evaluation(env)["support_trajectory_primed_for"] == []
assert env.events == ["reward", "score"]
with tempfile.TemporaryDirectory() as directory:
    run = Path(directory); episode = run / "seed-23"; episode.mkdir()
    (run / "results.json").write_text(json.dumps([{
        "seed_index": 23, "mode": "frozen", "status": "native_end",
        "action_count": 9, "wall_seconds": 1, "human_interventions": 0}]))
    (episode / "provenance.json").write_text(json.dumps({"task": "imitate_sorting_sequence"}))
    (episode / "runner-result.json").write_text(json.dumps({
        "native_terminal": True, "success": True, "score": 100,
        "valid_sample": False, "unstable": True}))
    row = summarize(run)[0]
    assert row["native_terminal"] and not row["valid_sample"]
    assert not row["success"] and row["score"] is None
print("Official startup order matches; unstable terminal samples are excluded from scoring")
