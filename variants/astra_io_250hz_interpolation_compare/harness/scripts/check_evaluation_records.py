"""Exercise immutable release checking and fail-closed score aggregation."""
import json,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from egg_harness.freeze import digest,verify
from audit_curriculum import summarize
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    (root/"policy.py").write_text("original")
    (root/"manifest.json").write_text(json.dumps({"files":digest(root)}))
    verify(root)
    (root/"policy.py").write_text("changed")
    try:verify(root)
    except RuntimeError:pass
    else:raise AssertionError("Mutated capability accepted")
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);episode=root/"seed-23";episode.mkdir()
    (root/"results.json").write_text(json.dumps([{"seed_index":23,"mode":"frozen","status":"error_or_timeout","action_count":10,"wall_seconds":1,"human_interventions":0}]))
    (episode/"provenance.json").write_text(json.dumps({"task":"align_blocks"}))
    (episode/"runner-result.json").write_text(json.dumps({"native_terminal":False,"success":True,"score":100}))
    row=summarize(root)[0]
    assert not row["success"] and row["score"] is None
    (root/"results.json").write_text(json.dumps([{"seed_index":23,"mode":"frozen","status":"native_end","action_count":10,"wall_seconds":1,"human_interventions":0}]))
    (episode/"runner-result.json").write_text(json.dumps({"native_terminal":True,"success":True,"score":100}))
    row=summarize(root)[0]
    assert row["success"] and row["score"]==100
    record=json.loads((root/"results.json").read_text())
    record[0]["mode"]="development";record[0]["status"]="held_for_development_recovery"
    (root/"results.json").write_text(json.dumps(record))
    row=summarize(root)[0]
    assert not row["success"] and not row["native_terminal"] and row["score"] is None, "Later continuation score leaked into original version"
print("Immutable release and terminal-only score checks passed")
