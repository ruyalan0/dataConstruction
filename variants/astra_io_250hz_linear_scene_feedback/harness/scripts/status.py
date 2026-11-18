import json
import sys
from pathlib import Path
x=json.loads((Path(sys.argv[1])/'latest.json').read_text())
print(json.dumps({k:x[k] for k in ('step','ended')}))
for arm in ('left','right'):
    print(arm, [round(v,5) for v in x['state'][arm+'_ee_pose']], 'grip', x['state'][arm+'_ee_joint_state'])
