"""CPU checks for observation rejection, projection and immutable bundle detection."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from egg_harness.geometry import unproject
from egg_harness.freeze import digest
s=json.load(open('memory/skills.json'))
assert np.allclose(unproject([316,266],.77,s['head_camera']),[-.008197,-.160910,.77],atol=1e-5)
try: unproject([-1,100],.77,s['head_camera'])
except ValueError: pass
else: raise AssertionError('Invalid pixels accepted')
with tempfile.TemporaryDirectory() as d:
    p=Path(d);(p/'a').write_text('before');initial=digest(p);(p/'a').write_text('after');assert digest(p)!=initial
# Prevent accidental legacy observation dependencies in policy source.
for name in ['policy.py','skill_policy.py','client.py','geometry.py','vision.py','motion_evidence.py','posture_action.py']:
    text=(Path('egg_harness')/name).read_text()
    for forbidden in ['runner-result','intrinsic_matrix','get_obj','reward_manager','approximate_depth']:
        assert forbidden not in text,(name,forbidden)
print('RGB geometry and contract checks passed')
