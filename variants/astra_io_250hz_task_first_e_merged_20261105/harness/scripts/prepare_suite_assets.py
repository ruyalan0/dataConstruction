"""Create a hash-verified official layout view without exposing scene values."""
import argparse, hashlib, json, subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument("--source-repo",type=Path,required=True)
p.add_argument("--runtime",type=Path,required=True)
p.add_argument("--output",type=Path,required=True)
a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=False)
for item in a.runtime.iterdir():
    if item.name!="Eval_Layout": (a.output/item.name).symlink_to(item.resolve(),target_is_directory=item.is_dir())
revision=subprocess.check_output(["git","-C",str(a.source_repo),"rev-parse","HEAD"],text=True).strip()
tasks=list(json.loads(Path("configs/public-tasks.json").read_text()))
def restore(pair):
    task,index=pair
    relative=f"Assets/Eval_Layout/RoboDojo/arx_x5/0/{task}_{index}.json"
    blob=subprocess.check_output(["git","-C",str(a.source_repo),"show",f"{revision}:{relative}"])
    if not blob.startswith(b"version https://git-lfs"):
        data=blob
    elif (a.source_repo/relative).is_file():
        data=(a.source_repo/relative).read_bytes()
    else:
        url=f"https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{revision}/{relative}"
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({"https":"http://internal.example.invalid:8118"}))
        with opener.open(url,timeout=60) as response: data=response.read()
    if blob.startswith(b"version https://git-lfs"):
        expected=next(line.split("sha256:")[1] for line in blob.decode().splitlines() if line.startswith("oid "))
        if hashlib.sha256(data).hexdigest()!=expected: raise ValueError(relative)
    elif data!=blob: raise ValueError(relative)
    json.loads(data)
    target=a.output/relative.removeprefix("Assets/")
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(data)
    return {"task":task,"layout_index":index,"seed_group":0,"sha256":hashlib.sha256(data).hexdigest()}
with ThreadPoolExecutor(max_workers=32) as pool:
    records=list(pool.map(restore,[(task,index) for task in tasks for index in range(26)]))
(a.output/"layout-manifest.json").write_text(json.dumps({"revision":revision,"files":records,"unaltered":True},indent=2))
print("Verified",len(records),"official layout files; object values not disclosed")
