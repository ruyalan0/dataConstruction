"""Create an immutable, allowlisted policy release from a clean Git commit."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def digest(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file() and p.name!='manifest.json' and '__pycache__' not in p.parts}


def verify(root):
    manifest=json.loads((root/"manifest.json").read_text())
    if digest(root)!=manifest["files"]:raise RuntimeError("Frozen capability hash mismatch")
    return manifest


def main():
    p=argparse.ArgumentParser();p.add_argument('destination',type=Path);a=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    if subprocess.check_output(['git','status','--porcelain'],cwd=root).strip(): raise RuntimeError('Commit changes before freezing')
    a.destination.mkdir(parents=True,exist_ok=False)
    for name in ['egg_harness','memory','skills','configs','scripts','pyproject.toml','uv.lock']:
        src=root/name;dst=a.destination/name
        if src.is_dir(): shutil.copytree(src,dst,ignore=shutil.ignore_patterns('__pycache__'))
        else: shutil.copy2(src,dst)
    manifest={'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'files':digest(a.destination),'status':'frozen_candidate_not_automatically_validated'}
    (a.destination/'manifest.json').write_text(json.dumps(manifest,indent=2))
    for f in a.destination.rglob('*'):
        if f.is_file(): f.chmod(0o444)
    print(a.destination.resolve())

if __name__=='__main__':main()
