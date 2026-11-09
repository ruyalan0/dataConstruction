"""Replay the real dev004 partial transfer and retain genuine drift detection."""
import json
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from egg_harness.skill_policy import executed_target
episode=Path(sys.argv[1])
before=json.loads((episode/'frames/step-0357.json').read_text())['state']['right_ee_pose']
after=json.loads((episode/'frames/step-0360.json').read_text())['state']['right_ee_pose']
actions=[json.loads(line) for line in (episode/'actions.jsonl').read_text().splitlines()]
native=next(x['action']['right_ee_pose'] for x in actions if x['step']==360)
target,quat=executed_target(before,[-.008,-.28,1.065],before[3:],3,18)
assert np.allclose(target,native[:3],atol=1e-7)
assert np.allclose(quat,native[3:],atol=1e-7)
error=float(np.linalg.norm(np.asarray(after[:3])-target))
assert error<.005
assert np.linalg.norm(np.asarray(after[:3])-[-.008,-.28,1.065])>.20
assert np.linalg.norm(np.asarray(after[:3])+[.12,0,0]-target)>.10
complete,_=executed_target(before,[-.008,-.28,1.065],before[3:],20,18)
assert np.allclose(complete,[-.008,-.28,1.065])
print(json.dumps({'partial_tracking_error_m':error,'false_abort_reproduced_and_corrected':True,'genuine_drift_still_rejected':True}))
