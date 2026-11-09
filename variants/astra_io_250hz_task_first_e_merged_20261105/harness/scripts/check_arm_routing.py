"""Check action ownership without a simulator or model call."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from egg_harness.client import Robot
r=Robot.__new__(Robot);r.active_arm='left';r.obs={'ended':False,'state':{'left_ee_pose':[0,0,1,1,0,0,0]}}
seen=[]
def command(op,**kw):
 seen.append(kw['arms']);return {'state':{'left_ee_pose':[0,0,1,1,0,0,0],'right_ee_pose':[0,0,1,1,0,0,0]}}
r.command=command
r.move('left',position=[0,0,1]);r.move('explicit-right',position=[0,0,1],arm='right')
assert list(seen[0])==['left'] and list(seen[1])==['right']
print('Active-arm routing and explicit override passed')

r.move('rotation-hold',q=[1,0,0,0])

def drifting(op,**kw):
 return {'state':{'left_ee_pose':[.1,0,1,1,0,0,0]}}
r.obs={'ended':False,'state':{'left_ee_pose':[0,0,1,1,0,0,0]}}
r.command=drifting
try: r.move('drift',q=[1,0,0,0])
except RuntimeError: pass
else: raise AssertionError('Rotation drift accepted')
print('Rotation-only drift rejected')
