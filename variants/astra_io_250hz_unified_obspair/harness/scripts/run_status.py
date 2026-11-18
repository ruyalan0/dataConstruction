"""Summarize live telemetry without treating files alone as liveness proof."""
import argparse,json,os
from pathlib import Path
from egg_harness.usage import model_usage
p=argparse.ArgumentParser();p.add_argument("runs",type=Path,nargs="+");a=p.parse_args()
for run in a.runs:
    records=[]
    for episode in sorted(run.glob("seed-*")):
        latest=episode/"latest.json"
        obs=json.loads(latest.read_text()) if latest.exists() else None
        index=episode.name.removeprefix("seed-")
        model=run/("policy-"+index)/"model"
        controllers=[{"trace":str(model.parent),**model_usage(model)}]
        for path in sorted(run.glob("*.result.json")):
            record=json.loads(path.read_text())
            if record.get("kind")!="development_continuation" or Path(record["episode"]).resolve()!=episode.resolve():continue
            trace=Path(record["trace"])
            if not trace.resolve().is_relative_to(run.resolve()):raise ValueError("Continuation trace outside run")
            controllers.append({"trace":str(trace),**model_usage(trace/"model")})
        records.append({"episode":episode.name,"step":obs["step"] if obs else None,"ended":obs["ended"] if obs else None,"error":(episode/"error.txt").exists(),"exit":json.loads((episode/"exit-status.json").read_text()) if (episode/"exit-status.json").exists() else None})
        records[-1]["controllers"]=controllers
        records[-1]["calls_completed"]=sum(c["calls_with_usage"] for c in controllers)
        records[-1]["tokens"]=sum(c["tokens"] for c in controllers)
        records[-1]["failed_model_requests"]=sum(c["failed_requests"] for c in controllers)
        records[-1]["accounting_complete"]=all(c["accounting_complete"] for c in controllers)
    print(json.dumps({"run":str(run),"telemetry":records,"liveness":"verify runner/process separately"}))
