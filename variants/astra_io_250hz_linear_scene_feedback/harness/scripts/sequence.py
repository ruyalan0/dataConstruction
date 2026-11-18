"""Execute a recoverable series of primitives, stopping on failed postconditions."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
p=argparse.ArgumentParser()
p.add_argument('episode',type=Path)
p.add_argument('plan',type=Path)
a=p.parse_args()
for command in json.loads(a.plan.read_text()):
    response=a.episode/'responses'/(command['id']+'.json')
    if not response.exists():
        subprocess.run([sys.executable,str(Path(__file__).with_name('command.py')),str(a.episode),json.dumps(command)],check=True,stdout=subprocess.DEVNULL)
    result=json.loads(response.read_text())
    for label, minimum in command.get('require_object_above',{}).items():
        if result['objects'][label]['position'][2] < minimum:
            raise RuntimeError(f'{command["id"]}: {label} not lifted above {minimum}')
    print(command['id'],result['step'],result['score'],flush=True)
    if result['ended']:
        if not result['success']: raise RuntimeError('Native task failed')
        break
