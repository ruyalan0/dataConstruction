"""Restore a bounded set of official asset categories with pinned Git/LFS hashes."""

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
parser.add_argument("--category", action="append", required=True)
args = parser.parse_args()
revision = subprocess.check_output(
    ["git", "-C", str(args.source_repo), "rev-parse", "HEAD"], text=True
).strip()
prefixes = [f"Assets/Object/RoboDojo/{c}/" for c in args.category]
files = subprocess.check_output(
    ["git", "-C", str(args.source_repo), "ls-tree", "-r", "--name-only", revision, *prefixes], text=True
).splitlines()
if not files:
    raise RuntimeError("No pinned asset files")
args.destination.mkdir(parents=True, exist_ok=False)


def restore(name):
    blob = subprocess.check_output(["git", "-C", str(args.source_repo), "show", f"{revision}:{name}"])
    if blob.startswith(b"version https://git-lfs.github.com/spec/v1"):
        lines = blob.decode().splitlines()
        expected = next(line.split("sha256:")[1] for line in lines if line.startswith("oid "))
        size = int(next(line.split()[1] for line in lines if line.startswith("size ")))
        url = f"https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{revision}/{name}"
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Asset integrity failure: {name}")
    else:
        data = blob
    target = args.destination / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


with ThreadPoolExecutor(max_workers=32) as pool:
    records = list(pool.map(restore, files))
(args.destination / "manifest.json").write_text(
    json.dumps({"revision": revision, "files": records}, indent=2)
)
print(json.dumps({"files": len(records), "bytes": sum(r["bytes"] for r in records)}))
