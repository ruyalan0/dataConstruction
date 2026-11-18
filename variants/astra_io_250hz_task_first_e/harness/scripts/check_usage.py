"""Accounting distinguishes interrupted calls, failures, and billed responses."""
import json
import tempfile
from pathlib import Path
from egg_harness.usage import model_usage

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    for i in range(1,5): (root/f"{i:04d}.request.json").write_text("{}")
    for i in (1,2):
        (root/f"{i:04d}.cost.json").write_text(json.dumps({"usage":{"total_tokens":i*100},"seconds":i}))
    for i in (2,3): (root/f"{i:04d}.failure.json").write_text("{}")
    usage=model_usage(root)
    assert usage["requests"]==4 and usage["calls_with_usage"]==2
    assert usage["tokens"]==300 and usage["failed_requests"]==2
    assert usage["requests_without_usage"]==["0003","0004"]
    assert usage["unresolved_requests"]==["0004"]
    (root/"0004.cost.json").write_text("")
    (root/"0004.failure.json").write_text('{"type":')
    damaged=model_usage(root)
    assert damaged["tokens"]==300 and damaged["calls_with_usage"]==2
    assert damaged["failed_requests"]==2
    assert damaged["unresolved_requests"]==["0004"]
    assert damaged["accounting_complete"] is False
    assert {Path(r["path"]).name for r in damaged["damaged_evidence"]}=={"0004.cost.json","0004.failure.json"}
    assert (root/"0004.cost.json").read_bytes()==b""
    (root/"0005.cost.json").write_text(json.dumps({"usage":{"total_tokens":1},"seconds":1}))
    try:model_usage(root)
    except ValueError:pass
    else:raise AssertionError("Orphaned usage accepted")
print("Usage accounting preserves partial, failed and unresolved requests")
