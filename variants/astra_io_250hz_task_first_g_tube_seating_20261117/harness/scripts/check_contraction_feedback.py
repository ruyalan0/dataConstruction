"""A contracted target must not become a false tracking failure in policy feedback."""
import json
import tempfile
from pathlib import Path
import numpy as np
from egg_harness.skill_policy import execute_bilateral,compile_motion,validate
from egg_harness.motion_evidence import executed_motion,target_for_arm

with tempfile.TemporaryDirectory() as directory:
    class Robot:
        episode=Path(directory)
        obs={'step':0,'ended':False,'state':{a+'_ee_pose':[0,0,1,1,0,0,0] for a in ('left','right')}}
        last_command_id='contraction-test'
        def command(self,op,**kwargs):
            assert kwargs['contract_local_ik'] is True
            targets={};state={}
            for arm,spec in kwargs['arms'].items():
                selected=[.005,0,1,1,0,0,0]
                targets[arm+'_ee_pose']=selected
                state[arm+'_ee_pose']=selected
                state[arm+'_gripper_measured_joints']=[.02]
                state[arm+'_ee_joint_state']=[0]
            self.obs={'step':1,'ended':False,'state':state}
            (self.episode/'frames').mkdir()
            (self.episode/'frames/step-0001.json').write_text(json.dumps(self.obs))
            (self.episode/'actions.jsonl').write_text(json.dumps({'step':1,'command':self.last_command_id,'cartesian_target':targets})+chr(10))
            (self.episode/'ik-contractions.jsonl').write_text(json.dumps({'step':0,'command':self.last_command_id,'scale':.25,'attempts':[]})+chr(10))
    robot=Robot()
    compiled,steps,settle=compile_motion({'arms':{a:{'position':[.02,0,1]} for a in ('left','right')},'steps':1},'fine',robot.obs,validate)
    result=execute_bilateral(robot,'move_local',compiled,steps,settle,{'left':0,'right':0},'fine',contract_local_ik=True)
    assert result['local_ik_contraction']['contracted']
    for arm,row in result['arms'].items():
        assert row['wrist_error_m']<1e-10 and not row['requires_fine_recovery']
        assert np.isclose(row['unexecuted_target_distance_m'],.015)
    evidence=executed_motion(robot.episode,robot.last_command_id,0,1)
    position,_=target_for_arm(evidence,'left',[0,0,1,1,0,0,0],1)
    assert np.linalg.norm(position-[.055,0,1])>.025,'Actual drift must remain detectable'
    try:executed_motion(robot.episode,'wrong-command',0,1)
    except ValueError:pass
    else:raise AssertionError('Cross-controller action accepted')
    try:executed_motion(robot.episode,robot.last_command_id,0,2)
    except ValueError:pass
    else:raise AssertionError('Missing native action accepted')
print('Contracted target drives bilateral/single-arm feedback; remaining goal and genuine drift retained; corrupt provenance fails')
