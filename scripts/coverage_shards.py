"""Run independent task episodes; persist failures and continue other shards."""

import argparse
import json
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--prefix", required=True)
parser.add_argument("tasks", nargs="+")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
records = []
for task in args.tasks:
    name = f"{args.prefix}-{task}"
    output = root / "artifacts" / name
    if output.exists():
        raise FileExistsError(f"Refusing to repeat existing shard: {output}")
    with (root / "artifacts" / f"{name}.log").open("x") as log:
        run = subprocess.run(
            ["bash", str(root / "scripts/run_worker_episode.sh"), name, "0", "0", task],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,  # A failed shard is retained; other independent tasks still run.
        )
    record = {"task": task, "output": name, "exit_code": run.returncode}
    if run.returncode == 0:
        record["result"] = json.loads((output / "episode-result.json").read_text())
        subprocess.run(
            [str(root / ".venv/bin/python"), str(root / "scripts/summarize_episode.py"), str(output)],
            check=True,
        )
    records.append(record)
    manifest = root / "artifacts" / f"{args.prefix}-manifest.json"
    temporary = manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, indent=2))
    temporary.replace(manifest)
    print(json.dumps(record), flush=True)
raise SystemExit(1 if any(r["exit_code"] != 0 for r in records) else 0)
