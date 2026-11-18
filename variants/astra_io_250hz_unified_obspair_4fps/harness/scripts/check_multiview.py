"""CPU regressions for native multiview geometry, rejection and public capture."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

from egg_harness import multiview, skill_policy, native_responses
from egg_harness.native_tools import ToolRegistry
from scripts.check_native_tools import Client, META, call, response
from scripts.public_capture import capture_public_observation, validate_camera_rig


def camera(origin):
    t=np.eye(4);t[:3,3]=origin
    return {'intrinsic':[[400.,0,320],[0,400.,240],[0,0,1]],'world_from_camera':t.tolist(),'resolution':[640,480]}


class MultiviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.cameras=[camera([-.2,0,0]),camera([.2,0,0]),camera([0,.2,0])]
        self.point=np.array([.02,.03,-1.])
        self.pixels=[multiview.project(self.point,c) for c in self.cameras]
        rig={'schema_version':1,'status':'render_qualified','cameras':{}}
        for name,c in zip(multiview.CAMERAS,self.cameras):
            data=deepcopy(c)
            if name!='cam_head':data['ee_from_camera']=data.pop('world_from_camera')
            rig['cameras'][name]=data
        path=self.root/'calibration.json';path.write_text(json.dumps(rig))
        self.geometry=multiview.MultiViewGeometry(path)
        images={}
        for name in multiview.CAMERAS:
            f=self.root/(name+'.jpg');Image.new('RGB',(640,480)).save(f);images[name]=str(f)
        self.obs={'step':4,'state':{arm+'_ee_pose':[0,0,0,1,0,0,0] for arm in ('left','right')},'cameras':images,'camera_calibration_id':self.geometry.calibration_id}
        self.request={'step':4,'feature':'Same visible corner','views':[{'camera':name,'pixel':p.tolist()} for name,p in zip(multiview.CAMERAS,self.pixels)]}

    def test_exact_two_and_three_view_geometry(self):
        for count in (2,3):
            result=multiview.triangulate(self.pixels[:count],self.cameras[:count])
            np.testing.assert_allclose(result['position'],self.point,atol=1e-9)
            self.assertLess(result['reprojection_rms_px'],1e-7)
            self.assertGreater(result['pixel_noise_sensitivity']['largest_axis_sigma_m'],0)
        result=self.geometry.locate(self.request,self.obs)
        self.assertEqual(result['step'],4);self.assertEqual(result['native_steps_executed'],0)
        self.assertEqual(result['calibration_id'],self.geometry.calibration_id)

    def test_moving_wrist_updates_extrinsics(self):
        moved=deepcopy(self.obs)
        q=Rotation.from_euler('z',20,degrees=True).as_quat()[[3,0,1,2]]
        moved['state']['left_ee_pose']=[.04,.03,0,*q.tolist()]
        cameras=self.geometry.cameras(moved)
        request=deepcopy(self.request)
        for view in request['views']:view['pixel']=multiview.project(self.point,cameras[view['camera']]).tolist()
        result=self.geometry.locate(request,moved)
        np.testing.assert_allclose(result['position'],self.point,atol=1e-8)
        with self.assertRaises(ValueError):self.geometry.locate(request,self.obs)

    def test_correspondence_and_frame_rejections(self):
        with self.assertRaisesRegex(ValueError,'qualification'):
            self.geometry.locate(self.request,{**self.obs,'camera_calibration_id':'old'})
        for step in (3,5,True):
            with self.assertRaises(ValueError):self.geometry.locate({**self.request,'step':step},self.obs)
        for names in (['cam_head','cam_head'],['cam_head','hidden']):
            views=[{'camera':n,'pixel':[320,240]} for n in names]
            with self.assertRaises(ValueError):self.geometry.locate({**self.request,'views':views},self.obs)
        for pixel in ([-1,0],[640,0],[0,480],[float('nan'),0]):
            bad=deepcopy(self.request);bad['views'][0]['pixel']=pixel
            with self.assertRaises(ValueError):self.geometry.locate(bad,self.obs)
        Image.new('RGB',(320,240)).save(self.obs['cameras']['cam_head'])
        with self.assertRaisesRegex(ValueError,'resolution'):self.geometry.locate(self.request,self.obs)

    def test_bad_geometry_and_third_view_never_ignored(self):
        with self.assertRaisesRegex(ValueError,'parallax'):
            multiview.triangulate([[320,240],[320,240]],self.cameras[:2])
        bad=deepcopy(self.pixels);bad[2]=bad[2]+[40,20]
        with self.assertRaises(ValueError):multiview.triangulate(bad,self.cameras)
        behind=[p.copy() for p in self.pixels[:2]];behind.reverse()
        with self.assertRaises(ValueError):multiview.triangulate(behind,self.cameras[:2])
        wide=[camera([-2,0,0]),camera([2,0,0])]
        point=[0,0,-10]
        with self.assertRaisesRegex(ValueError,'sensitivity'):
            multiview.triangulate([multiview.project(point,c) for c in wide],wide)

    def test_conditional_sensitivity_matches_noise_scale(self):
        expected=multiview.triangulate(self.pixels,self.cameras)['pixel_noise_sensitivity']['axis_sigma_m']
        rng=np.random.default_rng(42)
        # Small perturbations keep all samples inside the validated local regime.
        estimates=[]
        for _ in range(80):
            noisy=np.asarray(self.pixels)+rng.normal(0,.25,(3,2))
            estimates.append(multiview.triangulate(noisy,self.cameras)['position'])
        measured=np.std(estimates,axis=0)*8
        ratio=measured/expected
        self.assertTrue(np.all((ratio>.65)&(ratio<1.35)))

    def test_native_schema_and_mapping(self):
        registry=ToolRegistry({})
        decision=registry.decision('triangulate_point',json.dumps({**META,**self.request}))
        self.assertEqual(decision['action'],{'triangulate':self.request})
        for changed in ({'step':True},{'path':'/private'},{'views':self.request['views'][:1]},{'feature':''}):
            with self.assertRaises(ValueError):registry.decision('triangulate_point',json.dumps({**META,**self.request,**changed}))

    def test_capture_flushes_without_physics(self):
        calls=[]
        env=SimpleNamespace(take_action_cnt=[7],render=lambda:calls.append('render'),get_obs=lambda:{'state':'public'})
        self.assertEqual(capture_public_observation(env),{'state':'public'})
        self.assertEqual(calls,['render','render']);self.assertEqual(env.take_action_cnt,[7])
        def advance():env.take_action_cnt[0]+=1;return {}
        env.get_obs=advance
        with self.assertRaises(RuntimeError):capture_public_observation(env)

    def test_runtime_camera_certificate_rejects_changed_rig(self):
        cameras=self.geometry.cameras(self.obs)
        manager=SimpleNamespace(camera_names=[list(multiview.CAMERAS)],
            cameras=[[SimpleNamespace(get_resolution=lambda:(640,480)) for _ in range(3)]],
            get_camera_intrinsics=lambda i,e:cameras[multiview.CAMERAS[i]]['intrinsic'],
            get_camera_extrinsics=lambda i,e:cameras[multiview.CAMERAS[i]]['world_from_camera'])
        env=SimpleNamespace(camera_manager=manager)
        self.assertEqual(validate_camera_rig(env,self.geometry,self.obs),self.geometry.calibration_id)
        manager.get_camera_extrinsics=lambda i,e:np.eye(4)
        with self.assertRaisesRegex(ValueError,'mounting'):validate_camera_rig(env,self.geometry,self.obs)

    def test_real_policy_rejects_stale_then_returns_xyz_without_motion(self):
        observation=deepcopy(self.obs);observation.update(ended=False,instruction='Fixture only',budget={})
        for arm in ('left','right'):
            observation['state'].update({arm+'_ee_joint_state':[1.],arm+'_gripper_measured_joints':[.04,.04]})
        episode=self.root/'episode';episode.mkdir()
        skills=self.root/'skills/fixture';skills.mkdir(parents=True);(skills/'SKILL.md').write_text('Inspect public RGB.')
        memory=self.root/'memory.json';memory.write_text('{"head_camera":{}}')
        credential=self.root/'credential.py';credential.write_text('client=AzureOpenAI(api_key="TEST_ONLY")')
        config={'action_interface':'tcp','credentials':str(credential),'astra_provider':'azure-astra','model':'gpt-6-astra','reasoning_effort':'xhigh',
                'request_timeout':10,'max_model_calls':3,'skills_root':str(skills.parent),'skill_names':['fixture'],
                'memory':str(memory),'public_task_requirements':'Fixture only'}
        config_path=self.root/'policy.json';config_path.write_text(json.dumps(config))
        client=Client([response([call('triangulate_point',{**META,**self.request,'step':3},1)]),
                       response([call('triangulate_point',{**META,**self.request},2)]),
                       response([call('verify_completion',META,3)])])
        commands=[]
        class Robot:
            def __init__(self,episode,trace):
                self.obs={**deepcopy(observation),'action_interface':'tcp'};self.home={arm:observation['state'][arm+'_ee_pose'] for arm in ('left','right')}
            def command(self,op,**kwargs):
                commands.append((op,kwargs));self.obs={**self.obs,'step':10,'ended':True}
        trace=self.root/'policy-trace'
        with patch.object(skill_policy,'MultiViewGeometry',return_value=self.geometry), patch.object(skill_policy,'Robot',Robot), \
             patch.object(native_responses,'OpenAI',return_value=client), \
             patch.object(sys,'argv',['policy','--config',str(config_path),'--episode',str(episode),'--trace',str(trace)]):
            skill_policy.main()
        self.assertEqual(commands,[('move',{'arms':{},'steps':6,'settle':0,'label':'model_verify'})])
        rejection=json.loads(client.requests[1]['input'][-2]['output'])['feedback']
        self.assertEqual(rejection['native_steps_executed'],0)
        actual=json.loads(client.requests[2]['input'][-2]['output'])['feedback']['triangulation']
        np.testing.assert_allclose(actual['position'],self.point,atol=1e-9)
        self.assertEqual(actual['native_steps_executed'],0)


if __name__=='__main__':unittest.main()
