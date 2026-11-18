"""Recovery must retain controller evidence when final observation transport fails."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from egg_harness import recover

with tempfile.TemporaryDirectory() as directory:
    run = Path(directory)
    episode = run / "seed-20"
    (episode / "commands").mkdir(parents=True)
    (episode / "responses").mkdir()
    config = run / "config.json"
    config.write_text(json.dumps({"task": "align_blocks"}))
    (run / "held.json").write_text(json.dumps({"episode": str(episode), "config": str(config)}))
    trace = run / "recovery-01"
    original_replace = Path.replace
    requests = []
    def replace(path, target):
        result = original_replace(path, target)
        target = Path(target)
        if target.parent == episode / "commands":
            requests.append(target)
            if len(requests) == 1:
                (episode / "responses" / target.name).write_text(json.dumps({"step": 42, "ended": False}))
        return result
    class Process:
        pid = 12345
        returncode = 7
        def __init__(self, *args, **kwargs):
            kwargs["stdout"].close()
        def wait(self, **kwargs):
            return self.returncode
    ticks = iter([0, 1, 2, 3, 4, 35])
    argv = ["recover", "--run", str(run), "--trace", str(trace)]
    with patch.object(sys, "argv", argv), patch.object(Path, "replace", replace), patch.object(recover.subprocess, "Popen", Process), patch.object(recover.subprocess, "check_output", return_value="test-commit"), patch.object(recover.time, "monotonic", side_effect=lambda: next(ticks)):
        try:
            recover.main()
        except TimeoutError:
            pass
        else:
            raise AssertionError("Transport failure did not propagate")
    record = json.loads(trace.with_suffix(".result.json").read_text())
    assert record["returncode"] == 7 and record["start_step"] == 42
    assert record["policy_pid"] == 12345 and record["recovery_pid"] > 0
    assert record["status"] == "observation_failed"
    assert "TimeoutError" in record["finalization_error"]
    assert "native_terminal" not in record
print("Recovery records survive final observation failure without inventing terminal evidence")
