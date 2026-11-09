"""Validate recorded failures and exhaustion without manufacturing physical success."""
import json
from pathlib import Path
import numpy as np
from egg_harness.kinematics import Kinematics
from egg_harness.contraction import solve_contracted

model=Kinematics(json.loads(Path('configs/x5-kinematics.json').read_text()))
reports=json.loads(Path('artifacts/bilateral-v1-ik-contraction-boundaries.json').read_text())
count=0
for report in reports['reports']:
    episode=Path(report['episode'])
    for row in report['rows']:
        frame='initial.json' if row['step']==0 else f"step-{row['step']:04d}.json"
        state=json.loads((episode/'frames'/frame).read_text())['state']
        nominal=row['attempts'][0]['arms']
        targets={arm:nominal[arm]['target'] if arm in nominal else state[arm+'_ee_pose'] for arm in ('left','right')}
        result=solve_contracted(model,state,targets,nominal)
        assert result['scale']==row['first_solved_scale']
        assert all(np.max(np.abs(q-state[arm+'_arm_joint_state']))<=.150001 for arm,q in result['solutions'].items())
        count+=1
targets={arm:np.asarray(state[arm+'_ee_pose']).copy() for arm in ('left','right')}
targets['left'][0]+=10
result=solve_contracted(model,state,targets,{'left'})
assert result['scale'] is None and result['solutions'] is None and len(result['attempts'])==4
print(f'{count} recorded boundaries match offline contraction evidence; exhaustion returns no joint action')
