"""Audit authoritative run artifacts without exposing evaluator data to a policy."""
import argparse
import hashlib
import json
from pathlib import Path
from egg_harness.freeze import verify

def read(path):
    return json.loads(path.read_text())

def summarize(run):
    path=run/"results.json"
    if not path.exists(): return {"run":str(run),"status":"no_final_results"}
    results=read(path)
    rows=[]
    for result in results:
        index=result["seed_index"]
        episode=run/f"seed-{index}"
        provenance=read(episode/"provenance.json") if (episode/"provenance.json").exists() else None
        source=read(run/f"source-{index}.json") if (run/f"source-{index}.json").exists() else None
        if source is None and (run/"launch-record.json").exists():
            launch = read(run/"launch-record.json")
            bundle = Path(launch["source"])
            if (bundle/"manifest.json").exists():
                manifest = verify(bundle)
                if manifest["commit"] != launch["source_commit"]:
                    raise ValueError("Startup release does not match declared commit")
                source = {"files": manifest["files"]}
        native=read(episode/"runner-result.json") if (episode/"runner-result.json").exists() else None
        costs=[read(p) for p in (run/f"policy-{index}"/"model").glob("*.cost.json")]
        terminal=bool(native and native["native_terminal"] and result["status"]=="native_end")
        valid_sample=bool(native and native.get("valid_sample",True) and not native.get("unstable",False))
        success=bool(terminal and native["success"] and result["status"]=="native_end")
        row={"run":str(run),"layout_index":index,"task":provenance["task"] if provenance else result.get("task"),"mode":result["mode"],"status":result["status"],"native_terminal":terminal,"success":success,"score":native["score"] if terminal else None,"actions":result["action_count"],"wall_seconds":result["wall_seconds"],"api_calls_completed":len(costs),"api_tokens":sum(c["usage"]["total_tokens"] for c in costs),"api_seconds":sum(c["seconds"] for c in costs),"human_interventions":result["human_interventions"],"source_hash":hashlib.sha256(json.dumps(source["files"],sort_keys=True).encode()).hexdigest() if source else None}
        rows.append(row)
        row["valid_sample"]=valid_sample
        row["episode_now_native_terminal"]=bool(native and native["native_terminal"])
        if not valid_sample:
            row.update(success=False,score=None)
    return rows

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--artifacts",type=Path,default=Path("artifacts"))
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    plan=read(Path("configs/curriculum.json"))
    rows=[]
    for path in sorted(args.artifacts.glob("*/results.json")): rows.extend(summarize(path.parent))
    tasks=[]
    for task in plan["tasks"]:
        selected=[r for r in rows if r["task"]==task["task"]]
        development=[r for r in selected if r["mode"]=="development"]
        frozen=[r for r in selected if r["mode"]=="frozen"]
        by_version={}
        for row in frozen: by_version.setdefault(row["source_hash"],[]).append(row)
        batches=[]
        for version,measurements in by_version.items():
            complete=version is not None and len(measurements)==3 and {r["layout_index"] for r in measurements}==set(plan["heldout_layouts"]) and all(r["native_terminal"] and r["status"]=="native_end" for r in measurements)
            complete=complete and all(r["valid_sample"] for r in measurements)
            mean=sum(r["score"] for r in measurements)/3 if complete else None
            batches.append({"source_hash":version,"complete":complete,"mean_score":mean,"successes":sum(r["success"] for r in measurements),"episodes":len(measurements),"meets_score_target":complete and mean>=task["target_mean_score"]})
        tasks.append({"task":task["task"],"target":task["target_mean_score"],"development_layouts_with_terminal_result":sorted({r["layout_index"] for r in development if r["native_terminal"]}),"frozen_batches":batches,"acceptance":"unproven; curriculum ordering, frozen integrity and experience isolation require separate audit"})
    report={"tasks":tasks,"attempts":rows,"total_actions":sum(r["actions"] for r in rows),"monetary_cost":None,"interactive_developer_cost":None,"missing_cost_reason":"No billing export; interrupted API calls may lack usage"}
    args.output.write_text(json.dumps(report,indent=2))
    print(json.dumps({"completed_attempts":len(rows),"total_actions":report["total_actions"],"output":str(args.output)}))

if __name__=="__main__":main()
