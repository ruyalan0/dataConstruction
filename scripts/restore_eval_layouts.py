"""Restore selected pinned layout files without changing the upstream cache."""

import argparse
import hashlib
import json
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--source-repo", type=Path, required=True)
parser.add_argument("--destination", type=Path, required=True)
parser.add_argument("tasks", nargs="+")
args = parser.parse_args()
revision = subprocess.check_output(
    ["git", "-C", str(args.source_repo), "rev-parse", "HEAD"], text=True
).strip()
args.destination.mkdir(parents=True, exist_ok=False)


def restore(task):
    relative = f"RoboDojo/arx_x5/0/{task}_0.json"
    name = f"Assets/Eval_Layout/{relative}"
    blob = subprocess.check_output(["git", "-C", str(args.source_repo), "show", f"{revision}:{name}"])
    if blob.startswith(b"version https://git-lfs.github.com/spec/v1"):
        lines = blob.decode().splitlines()
        expected = next(line.split("sha256:")[1] for line in lines if line.startswith("oid "))
        size = int(next(line.split()[1] for line in lines if line.startswith("size ")))
        url = f"https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{revision}/{name}"
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected or len(data) != size:
            raise ValueError(f"Layout integrity failure: {name}")
    else:
        data = blob
    json.loads(data)
    target = args.destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"path": name, "sha256": hashlib.sha256(data).hexdigest()}


with ThreadPoolExecutor(max_workers=32) as pool:
    records = list(pool.map(restore, args.tasks))
(args.destination / "manifest.json").write_text(
    json.dumps({"revision": revision, "files": records}, indent=2)
)
