"""Predeclare a frozen task evaluation after three completed development layouts."""
import argparse,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from egg_harness.freeze import verify
from egg_harness.layout_split import heldout_layouts
from egg_harness.development_record import terminal_development

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--task",required=True)
    p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--development",type=Path,nargs=3,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();bundle=a.bundle.resolve();manifest=verify(bundle)
    curriculum=json.loads((bundle/"configs/curriculum.json").read_text())
    records=[]
    for run in a.development:
        records.append(terminal_development(run,a.task))
    if {r["layout_index"] for r in records}!=set(curriculum["development_layouts"]):raise ValueError("Three distinct development layouts required")
    if a.output.exists():raise FileExistsError(a.output)
    record={"task":a.task,"created_at":datetime.now(timezone.utc).isoformat(),"bundle":str(bundle),"commit":manifest["commit"],"files":manifest["files"],"development":records,"heldout_layouts":heldout_layouts(curriculum,a.task),"seed_group":curriculum["seed_group"],"asset_revision":curriculum["asset_revision"],"test_policy":"No capability updates between heldout episodes; all attempts retained"}
    a.output.write_text(json.dumps(record,indent=2))
    print(a.output)

if __name__=="__main__":main()
