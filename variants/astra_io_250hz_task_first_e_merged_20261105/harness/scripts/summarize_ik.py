"""Developer-only native IK versus achieved tracking report; never policy input."""
import argparse,json
from collections import Counter
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('episode',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
rows=[json.loads(line) for line in (a.episode/'developer-only/ik.jsonl').read_text().splitlines()]
counts=Counter((r['arm'],r['result']['status']) for r in rows)
tracking=[]
for command_path in (a.episode/'commands').glob('*.json'):
    command=json.loads(command_path.read_text());response=a.episode/'responses'/command_path.name
    if command['op']!='move' or not response.exists():continue
    obs=json.loads(response.read_text())
    for arm,target in command.get('arms',{}).items():
        if 'position' not in target:continue
        error=float(np.linalg.norm(np.asarray(target['position'])-np.asarray(obs['state'][arm+'_ee_pose'][:3])))
        if error>.015:
            start=obs['step']-command.get('steps',1)-command.get('settle',0)
            calls=[r for r in rows if start<=r['step']<=obs['step'] and r['arm'].startswith(arm)]
            tracking.append({'command':command['id'],'end_step':obs['step'],'arm':arm,'position_error_m':error,'ik_statuses':dict(Counter(r['result']['status'] for r in calls))})
out={'counts':{arm+':'+status:n for (arm,status),n in counts.items()},'tracking_errors':tracking,'note':'IK success does not establish absence of contact. Pose error with IK failure is evidence of rejected target; intervals include boundary calls.'}
a.output.write_text(json.dumps(out,indent=2));print(json.dumps(out))
