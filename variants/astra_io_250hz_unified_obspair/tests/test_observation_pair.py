"""Exercise actual three-cycle requests, policy execution and observation-only memory."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness'), str(ROOT / 'scripts')]
import policy
from egg_harness import native_responses
from egg_harness.control_timing import timing_record
from egg_harness.observation_pair import CAMERAS, PROTOCOL, build_pair, snapshot


def state(value):
    result = {}
    for arm in ('left', 'right'):
        result[arm+'_arm_joint_state'] = [value, .123456789, 0., 0., 0., 0.]
        result[arm+'_ee_pose'] = [value, 0., 1., 1., 0., 0., 0.]
        result[arm+'_ee_joint_state'] = [.4]
        result[arm+'_gripper_measured_joints'] = [.0123456789, .0123]
    return result


def context(step=0, value=0., memory='fresh memory'):
    return dict(step=step, state=state(value), instruction='Pick up the target.', memory=memory,
                action_interface='joint', control_timing=timing_record(),
                budget=dict(total_control_ticks=2000, executed_control_ticks=step),
                desired_gripper_commands=dict(left=.4, right=.4),
                joint_semantics=dict(arms='public'), home_joints=dict(left=[0.]*6,right=[0.]*6),
                arm_posture=dict(source_step=step))


def arguments(number):
    return dict(assessment='observed', plan='next goal', memory=f'memory from decision {number}',
                left=None, right=dict(control='joint', joints=[.123456, 0., 0., 0., 0., 0.], gripper=.4),
                steps=125, settle=2)


class PairBuilderTests(unittest.TestCase):
    def test_first_and_previous_frames_keep_full_precision_and_source_objects(self):
        images={name: name+'.jpg' for name in CAMERAS}
        original=context(); before=deepcopy(original)
        first, first_images, prior=build_pair(original,images)
        self.assertEqual(first['observation']['memory'],'fresh memory')
        self.assertFalse(first['history']['available'])
        self.assertEqual(list(first_images),['current_'+name for name in CAMERAS])
        current=context(83,.012345678901,'updated memory')
        pair, views, _=build_pair(current,images,prior)
        self.assertEqual(pair['history']['duration_ms'],332.)
        self.assertEqual(pair['history']['previous_observation']['state'],original['state'])
        self.assertEqual(pair['observation']['state'],current['state'])
        self.assertNotIn('memory',pair['history']['previous_observation'])
        self.assertEqual(list(views),['previous_'+name for name in CAMERAS]+['current_'+name for name in CAMERAS])
        self.assertEqual(original,before)
        pair['history']['previous_observation']['state']['left_arm_joint_state'][0]=999
        self.assertEqual(prior['state']['left_arm_joint_state'][0],0.)

    def test_action_history_feedback_and_future_labels_do_not_enter_request(self):
        source=context()
        for key in ('previous_result','recent_executed_segments','stalled_segments_this_controller',
                    'history_references','previous_observation','success','score','future_action'):
            source[key]={'secret_marker':key}
        result,_,_=build_pair(source,{name:name for name in CAMERAS})
        self.assertNotIn('secret_marker',json.dumps(result))
        self.assertNotIn('memory',result)
        self.assertIn('memory',result['observation'])

    def test_zero_motion_rejection_still_has_previous_decision_at_the_same_step(self):
        current=context(20)
        previous=snapshot(current,{name:name for name in CAMERAS})
        result,_,_=build_pair(current,{name:name for name in CAMERAS},previous)
        self.assertTrue(result['history']['available'])
        self.assertEqual(result['history']['duration_ms'],0.)

    def test_future_incomplete_nonfinite_and_wrong_timing_inputs_are_rejected(self):
        images={name:name for name in CAMERAS}
        previous=snapshot(context(20),images)
        with self.assertRaisesRegex(ValueError,'future'):build_pair(context(10),images,previous)
        with self.assertRaisesRegex(ValueError,'three camera'):build_pair(context(),{'cam_head':'head'})
        source=context();source['state']['left_arm_joint_state'][0]=float('nan')
        with self.assertRaisesRegex(ValueError,'Invalid public state'):build_pair(source,images)
        source=context();source['control_timing']['action_hz']=25
        with self.assertRaisesRegex(ValueError,'250 Hz'):build_pair(source,images)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.folder=Path(self.temporary.name)
        self.images={}
        for number,name in enumerate(CAMERAS):
            path=self.folder/(name+'.jpg');Image.new('RGB',(12,8),(30+number,50,70)).save(path)
            self.images[name]=path
        self.config=json.loads((HERE/'general_pickup.json').read_text())
        self.provider=SimpleNamespace(base_url='https://example.invalid',api_key='unused',
            audit=lambda:dict(provider='mock'),headers=lambda session:{})
        self.payloads=[]
        def create(**kwargs):
            self.payloads.append(deepcopy(kwargs));n=len(self.payloads)
            raw=dict(id=f'response{n}',status='completed',output=[
                dict(type='reasoning',id=f'reason{n}',encrypted_content='NEVER_REPLAY_THIS',summary=[]),
                dict(type='function_call',name='control_arms',status='completed',call_id=f'call{n}',
                     arguments=json.dumps(arguments(n)))])
            return SimpleNamespace(id=raw['id'],usage=None,status='completed',model_dump=lambda **options:deepcopy(raw))
        self.patches=[patch.object(native_responses,'OpenAI',return_value=SimpleNamespace(responses=SimpleNamespace(create=create))),
                      patch.object(native_responses,'select_provider',return_value=self.provider)]
        for item in self.patches:item.start()
        self.model=native_responses.NativeToolVision(self.config,self.folder/'model')

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temporary.cleanup()

    def test_three_real_calls_use_only_observation_pairs_and_latest_memory(self):
        memory='fresh memory'
        for number,step in enumerate((0,83,83),1):
            decision=self.model.ask(json.dumps(context(step,step/1000,memory)),self.images)
            self.assertEqual(decision['action']['control_arms']['arms']['right']['joints'][0],.1235)
            self.model.observe(dict(status='completed' if number==1 else 'rejected_before_motion',
                                    executed_steps=83 if number==1 else 0),83,False)
            request=self.payloads[-1]
            self.assertEqual(len(request['input']),1)
            self.assertEqual(request['input'][0]['role'],'user')
            self.assertNotIn('include',request)
            self.assertNotIn('NEVER_REPLAY_THIS',json.dumps(request))
            text=json.loads(request['input'][0]['content'][0]['text'])
            self.assertEqual(text['observation']['memory'],memory)
            self.assertEqual(text['observation']['state'],state(step/1000))
            self.assertNotIn('memory',text)
            if number>1:
                self.assertEqual(text['history']['previous_observation']['step'],0 if number==2 else 83)
                self.assertEqual(text['history']['duration_ms'],332 if number==2 else 0)
                self.assertEqual(len([p for p in request['input'][0]['content'] if p['type']=='input_image']),6)
            memory=decision['memory']
        self.assertFalse(hasattr(self.model,'previous'))
        self.assertNotIn('output',self.model.pending or {})
        self.assertEqual(self.model.last_memory,'memory from decision 3')
        self.assertEqual(json.loads((self.folder/'model/0002.request.json').read_text())['memory_location'],'observation.memory')
        effective=json.loads((self.folder/'model/0001.response.json').read_text())
        raw=json.loads((self.folder/'model/0001.response.raw.json').read_text())
        self.assertNotEqual(raw['output'][-1]['arguments'],effective['output'][-1]['arguments'])

    def test_pending_result_is_required_and_stale_memory_is_rejected(self):
        self.model.ask(json.dumps(context()),self.images)
        with self.assertRaisesRegex(RuntimeError,'execution result'):self.model.ask(json.dumps(context()),self.images)
        self.model.observe(dict(status='completed',executed_steps=1),1,False)
        with self.assertRaisesRegex(ValueError,'latest executed decision memory'):
            self.model.ask(json.dumps(context(1)),self.images)

    def test_failed_pending_action_does_not_commit_a_new_previous_observation(self):
        self.model.ask(json.dumps(context()),self.images)
        self.model.fail_pending(RuntimeError('partial failure'),10)
        self.assertIsNone(self.model.last_observation)
        self.assertIsNone(self.model.pending)
        self.assertEqual(json.loads((self.folder/'model/0001.tool-result.json').read_text())['status'],'aborted')

    def test_invalid_raw_gripper_cannot_round_into_bounds(self):
        def bad(**kwargs):
            raw=dict(status='completed',output=[dict(type='function_call',status='completed',
                name='control_arms',call_id='bad',arguments=json.dumps(dict(arguments(1),
                    right=dict(control='joint',joints=[0.]*6,gripper=1.00001))))])
            return SimpleNamespace(status='completed',usage=None,model_dump=lambda **options:raw)
        self.model.client.responses.create=bad
        with self.assertRaises(ValueError):self.model.ask(json.dumps(context()),self.images)

    def test_new_instance_has_no_observations_from_another_episode(self):
        self.model.ask(json.dumps(context()),self.images)
        self.model.observe(dict(status='completed',executed_steps=1),1,False)
        other=native_responses.NativeToolVision(self.config,self.folder/'other-model')
        other.ask(json.dumps(context(memory='another episode')),self.images)
        text=json.loads(self.payloads[-1]['input'][0]['content'][0]['text'])
        self.assertFalse(text['history']['available'])
        self.assertEqual(text['observation']['memory'],'another episode')

    def test_swift_export_keeps_exact_observation_pair_and_rejects_corrupt_labels(self):
        import export_swift
        memory='fresh memory'
        for number,step in enumerate((0,83),1):
            decision=self.model.ask(json.dumps(context(step,memory=memory)),self.images)
            self.model.observe(dict(status='completed' if number==1 else 'episode_ended',
                                    executed_steps=83 if number==1 else 7),83 if number==1 else 90,number==2)
            memory=decision['memory']
        run=self.folder/'completed-run';(run/'policy-20').mkdir(parents=True);(run/'seed-20').mkdir()
        shutil.copytree(self.folder/'model',run/'policy-20/model')
        export_swift.save(run/'seed-20/control-timing.json',timing_record())
        export_swift.save(run/'launch-record.json',dict(io_variant='astra_io_250hz_unified_obspair',input_protocol=PROTOCOL,
                                                        model_history_replay=False))
        export_swift.save(run/'results.json',[dict(seed_index=20,status='native_end',action_count=90,
            official_observation=dict(native_terminal=True,valid_sample=True,unstable=False,steps=90),
            policy=dict(model_calls=2),policy_returncode=0,simulator_returncode=0,release_unchanged=True)])
        output=self.folder/'swift'
        result=export_swift.export([run],output)
        self.assertTrue(result['passed'])
        samples=[json.loads(line) for line in (output/'train.jsonl').read_text().splitlines()]
        self.assertEqual(len(samples[0]['images']),3)
        self.assertEqual(len(samples[1]['images']),6)
        source=json.loads((run/'policy-20/model/0002.request.json').read_text())
        self.assertTrue(samples[1]['messages'][1]['content'].startswith(source['question']))
        self.assertNotIn('preceding_function_call',samples[1]['messages'][1]['content'])
        samples[1]['messages'][-1]['content']=json.dumps(arguments(999))
        contents=''.join(json.dumps(row)+'\n' for row in samples)
        for name in ('train.jsonl','all.jsonl'):(output/name).write_text(contents)
        with self.assertRaisesRegex(ValueError,'Source input or label mismatch'):export_swift.validate(output)

    def test_real_policy_loop_moves_memory_and_actual_prefix_into_second_request(self):
        from egg_harness import skill_policy
        original=skill_policy.Vision
        observations=[]
        source=json.loads((ROOT/'runs/general_pickup-io-250hz-timed-20261020-084923/policy-20/model/0001.request.json').read_text())
        initial=json.loads(source['question'])
        images=self.images
        class Robot:
            def __init__(robot,episode,trace):
                robot.home,robot.home_joints=initial['home'],initial['home_joints']
                robot.obs=dict(step=0,state=initial['state'],instruction=initial['instruction'],
                    budget=initial['budget'],control_timing=timing_record(),action_interface='joint',ended=False,cameras=images)
                robot.episode=episode
            def command(robot,operation,**kwargs):
                observations.append(deepcopy(robot.obs));step=83 if len(observations)==1 else 90
                robot.obs={**robot.obs,'step':step,'ended':len(observations)==2,
                    'action_feedback':dict(status='tracking_or_gripper_change' if step==83 else 'episode_ended',
                                          executed_steps=83 if step==83 else 7)}
        config=dict(self.config,memory=str(HERE/'harness/memory/skills.json'),skills_root=str(HERE/'harness/skills'))
        path=self.folder/'config.json';path.write_text(json.dumps(config))
        episode=self.folder/'episode';episode.mkdir()
        try:
            installed=policy.install()
            with patch.object(skill_policy,'Robot',Robot),patch.object(skill_policy,'Vision',installed),\
                 patch.object(sys,'argv',['policy','--config',str(path),'--episode',str(episode),'--trace',str(self.folder/'loop')]),\
                 patch('builtins.print'):
                skill_policy.main()
        finally:
            native_responses.NativeToolVision=installed.__bases__[0]
            skill_policy.Vision=original
        second=json.loads(self.payloads[1]['input'][0]['content'][0]['text'])
        self.assertEqual(second['observation']['memory'],'memory from decision 1')
        self.assertEqual(second['history']['previous_observation']['step'],0)
        self.assertEqual(second['observation']['step'],83)
        self.assertEqual(second['history']['duration_ms'],332)
        self.assertNotIn('tracking',json.dumps(second))
        result=json.loads((self.folder/'loop/policy-result.json').read_text())
        self.assertEqual(result['status'],'native_end')
        self.assertEqual(result['model_calls'],2)


if __name__=='__main__':
    unittest.main()
