"""Extract the task score matrix from the downloaded official JS, without executing it."""
import hashlib,json,re
from pathlib import Path
source=Path("sources/official/leaderboard-index.js")
text=source.read_text()
matches=re.findall("JSON.parse\('(.*?)'\)",text)
matrices=[json.loads(s) for s in matches if "align_blocks" in s]
if len(matrices)!=1:raise ValueError("Ambiguous leaderboard matrix")
data=matrices[0]
tasks=json.loads(Path("configs/public-tasks.json").read_text())
maxima={task:max(v[task]["score"] for v in data.values() if task in v) for task in tasks}
record={"source":"https://robodojo-benchmark.com/assets/index-6eSUp5NB.js","sha256":hashlib.sha256(source.read_bytes()).hexdigest(),"maxima":maxima,"matrix":data}
Path("sources/official/leaderboard.json").write_text(json.dumps(record,indent=2))
plan=json.loads(Path("configs/curriculum.json").read_text())
for row in plan["tasks"]: row["target_mean_score"]=maxima[row["task"]]
Path("configs/curriculum.json").write_text(json.dumps(plan,indent=2))
print(json.dumps(maxima,indent=2))
