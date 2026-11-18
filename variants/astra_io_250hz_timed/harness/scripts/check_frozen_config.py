"""Detect unpinned runtime changes while retaining the original accepted batch."""
import copy
import json
from pathlib import Path
from audit_frozen_batch import audit, pinned_config
from egg_harness.freeze import verify

bundle = Path("artifacts/releases/align-v1").resolve()
manifest = verify(bundle)
config = json.loads(Path("artifacts/align-frozen23-v1/config-23.json").read_text())
assert pinned_config(bundle, manifest, config)
for field, value in [("motion_op", "move_local"), ("max_model_calls", 999), ("model", "changed-model")]:
    altered = copy.deepcopy(config)
    altered[field] = value
    try:
        pinned_config(bundle, manifest, altered)
    except ValueError:
        pass
    else:
        raise AssertionError("Unpinned runtime change accepted: " + field)
report = audit(Path("artifacts/align-evaluation-plan.json"),
               [Path(f"artifacts/align-frozen{index}-v1") for index in (23, 24, 25)])
assert report["mean_score"] == 100 and report["recorded_integrity_and_independence"]
print("Pinned configuration changes rejected; original three-layout align batch remains valid")
