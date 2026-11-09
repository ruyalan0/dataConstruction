"""Both arms receive guards, feedback and temporal wrist observations."""
import json,tempfile
from pathlib import Path
from egg_harness.skill_policy import compile_motion,validate,execute_bilateral,temporal_inputs

with tempfile.TemporaryDirectory() as d:
    class Robot:
        episode=Path(d)
        obs={"step":0,"ended":False,"state":{a+"_ee_pose":[0,0,1,1,0,0,0] for a in ("left","right")}}
        def command(self,op,**kw):
            assert set(kw["arms"])=={"left","right"}
            assert {g["arm"] for g in kw["guards"]}=={"left","right"}
            assert all(g["watch_grip"] for g in kw["guards"])
            state={}
            for arm,spec in kw["arms"].items():
                state[arm+"_ee_pose"]=[*spec["position"],*spec["quaternion"]]
                state[arm+"_gripper_measured_joints"]=[.02]
                state[arm+"_ee_joint_state"]=[0]
            self.obs={"step":1,"ended":False,"state":state}
            (self.episode/"frames").mkdir()
            (self.episode/"frames/step-0001.json").write_text(json.dumps(self.obs))
    robot=Robot()
    compiled,steps,settle=compile_motion({"arms":{a:{"toward":{"position":[.01,0,1]}} for a in ("left","right")},"steps":1},"fine",robot.obs,validate)
    result=execute_bilateral(robot,"move_local",compiled,steps,settle,{"left":0,"right":0},"fine")
    assert set(result["arms"])=={"left","right"}
    assert all(v["wrist_error_m"]<1e-9 for v in result["arms"].values())
    cameras={"cam_head":"h","cam_left_wrist":"l","cam_right_wrist":"r"}
    images,_=temporal_inputs({"cameras":cameras},{"cameras":cameras,"arm":["left","right"],"step":0,"state":{}})
    assert images["previous_left_wrist"]=="l" and images["previous_right_wrist"]=="r"
print("Bilateral model action guards, feedback and temporal wrist images verified")
