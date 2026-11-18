"""A startup failure still belongs to its declared immutable release."""
import hashlib
import json
from pathlib import Path
from audit_curriculum import summarize
from egg_harness.freeze import verify

run = Path("artifacts/classify-language-dev20-local-v7")
launch = json.loads((run / "launch-record.json").read_text())
manifest = verify(Path(launch["source"]))
expected = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode()).hexdigest()
row = summarize(run)[0]
assert row["source_hash"] == expected, "Startup failure omitted from release measurements"
assert row["status"] == "error_or_timeout" and row["actions"] == 0
assert row["score"] is None and not row["success"] and not row["native_terminal"]
print("Startup-only failure attributed to declared verified release without inventing a score")
