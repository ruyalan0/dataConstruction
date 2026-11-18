"""Aggregate recorded evidence, never estimate unlogged token or monetary costs."""
import argparse
import json
from pathlib import Path
from egg_harness.usage import model_usage
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('artifacts'));p.add_argument('--output',type=Path,required=True);a=p.parse_args()
episodes=[]
for actions in sorted(a.root.rglob('actions.jsonl')):
    folder=actions.parent
    row={'episode':str(folder),'actions':sum(1 for _ in actions.open()),'success':False,'native_terminal':False}
    for name in ['runner-result.json','result.json']:
        f=folder/name
        if f.exists():
            r=json.loads(f.read_text());terminal=bool(r.get('native_terminal',r.get('ended',False)));row.update(success=terminal and bool(r.get('success',False)),native_terminal=terminal,score=r.get('score'));break
    if (folder/'provenance.json').exists():row['provenance']=json.loads((folder/'provenance.json').read_text())
    episodes.append(row)
calls=[]
usage_by_controller=[{'trace':str(folder),**model_usage(folder)} for folder in sorted({p.parent for p in a.root.rglob('*.request.json') if p.parent.name=='model'})]
damaged=[item for controller in usage_by_controller for item in controller['damaged_evidence']]
damaged_paths={item['path'] for item in damaged}
for f in a.root.rglob('*.cost.json'):
    if str(f) in damaged_paths:continue
    r=json.loads(f.read_text());calls.append({'path':str(f),**r})
failures=[str(p) for p in a.root.rglob('*.failure.json') if str(p) not in damaged_paths]
result={'episodes':episodes,'total_actions':sum(r['actions'] for r in episodes),'model_requests_succeeded':len(calls),'model_requests_failed':len(failures),'failed_model_requests':failures,'api_seconds':sum(r['seconds'] for r in calls),'api_tokens':sum(r['usage']['total_tokens'] for r in calls),'interactive_codex_tokens':None,'monetary_cost':None,'unknown_cost_reason':'No billing data or interactive model usage export available','model_calls':calls}
result.update(model_requests_recorded=sum(u['requests'] for u in usage_by_controller),requests_without_usage=sum(len(u['requests_without_usage']) for u in usage_by_controller),unresolved_requests=sum(len(u['unresolved_requests']) for u in usage_by_controller),usage_by_controller=usage_by_controller)
result.update(damaged_evidence=damaged,accounting_complete=all(u['accounting_complete'] for u in usage_by_controller),cost_scope='Recorded costs only; incomplete accounting is a lower bound, not a zero-cost failure.')
a.output.write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ['model_calls','episodes','usage_by_controller']}))
