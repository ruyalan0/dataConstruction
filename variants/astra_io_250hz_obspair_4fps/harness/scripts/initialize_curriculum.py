import json
from pathlib import Path
root=Path.cwd()
tasks=json.loads(Path("configs/public-tasks.json").read_text())
maxima=json.loads(Path("sources/official/leaderboard.json").read_text())["maxima"]
base=json.loads(Path("configs/skill-development.json").read_text())
base.update(task="align_blocks",assets=str(root/"artifacts/suite-assets-v4"),skill_names=["visual-contact-control","align-blocks"],max_model_calls=160,request_timeout=300)
Path("configs/align-development.json").write_text(json.dumps(base,indent=2))
Path("configs/align-frozen.json").write_text(json.dumps(base,indent=2))
plan={"protocol":"three development layouts then three frozen heldout layouts per task; single sequential task curriculum", "asset_revision":"cfb06d1dadcdf03ccc923102b0e3f3b0a68dfc43","seed_group":0,"development_layouts":[20,21,22],"heldout_layouts":[23,24,25],"heldout_policy":"Once revealed, heldout layouts cannot be used for tuning and still claimed heldout. Failed batches remain reported.","tasks":[{"task":task,"target_mean_score":maxima[task],"status":"not_run"} for task in tasks]}
Path("configs/curriculum.json").write_text(json.dumps(plan,indent=2))
print([(r["task"],r["target_mean_score"]) for r in plan["tasks"]])
