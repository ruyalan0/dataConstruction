"""Reject recovery response contamination and false progress claims at Robot boundary."""
import json
import tempfile
from pathlib import Path
from egg_harness.client import Robot

base={'step':0,'state':{},'cameras':{},'instruction':'test','ended':False,'budget':{}}
feedback={'status':'target_reached','target_reached':True,'executed_steps':0,'next_motion_verified':False,'instruction':'Inspect next motion'}

def exercise(op,obs,accepted):
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        for name in ('commands','responses','trace'):(root/name).mkdir()
        robot=Robot.__new__(Robot);robot.episode=root;robot.trace=root/'trace';robot.counter=0;robot.session='test'
        (root/'responses/policy-test-00001.json').write_text(json.dumps(obs))
        try:robot.command(op,**({'recover_posture':{'max_steps':4}} if op=='recover_posture' else {}))
        except ValueError:
            if accepted:raise
        else:
            assert accepted,'Invalid response accepted'

exercise('observe',base,True)
exercise('recover_posture',{**base,'posture_recovery':feedback},True)
exercise('observe',{**base,'posture_recovery':feedback},False)
exercise('recover_posture',base,False)
for mutation in ({'executed_steps':True},{'executed_steps':5},{'next_motion_verified':True},{'status':'success'},{'target_reached':1},{'instruction':None}):
    exercise('recover_posture',{**base,'posture_recovery':{**feedback,**mutation}},False)
exercise('recover_posture',{**base,'posture_recovery':feedback,'hidden_objects':[]},False)
print('Robot response boundary: exact operation-specific fields, step budget and unverified next-motion claims passed')
