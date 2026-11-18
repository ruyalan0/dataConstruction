"""Verify the full80-case objective; do not certify an unfinished round."""
import argparse
import ast
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request

import diagnose_suite as base
import five_layout_suite as suite
from prepare_eight_eval import save
import run_eight_eval as runner


def complete_scope(data, tasks):
    expected={(task,layout) for task in tasks for layout in range(20,25)}
    assert data['total']==data['finished']==80
    assert len(data['rows'])==80 and {(r['task'],r['layout']) for r in data['rows']}==expected
    assert all(r['phase']=='finished' for r in data['rows'])
    return Counter(r['end_reason'] for r in data['rows'])


def retry_records(records):
    groups=defaultdict(list)
    for record in records:
        assert record['provider']=='azure-astra'
        groups[record['call']].append(record)
    for rows in groups.values():
        assert [r['attempt'] for r in rows]==list(range(1,len(rows)+1))
        assert len(rows)<=4
        assert len({r['request_sha256'] for r in rows})==1
        assert len({r['step'] for r in rows})==1
        assert len({r['scene_sha256'] for r in rows if 'scene_sha256' in r})<=1
        for row in rows:
            if row.get('will_retry'):
                status=row.get('http_status')
                assert row['type'] in ('APIConnectionError','APITimeoutError') or status==429 or (status is not None and 500<=status<600)
                assert row['attempt']<=3 and row['delay_seconds']==[10,30,60][row['attempt']-1]
    return len(groups)


def runtime_boundary():
    sys.path.insert(0,str(base.HARNESS))
    from egg_harness.freeze import verify
    original=base.OLD/'artifacts/baseline-5e17233'
    old=verify(original); current=verify(base.HARNESS)
    assert old['commit'].startswith('5e17233') and current['commit']==base.HARNESS_COMMIT
    paths={name for name in set(old['files'])|set(current['files']) if name.startswith(('egg_harness/','configs/'))}
    paths.update('scripts/'+name for name in ('serve.py','server_loop.py','worker_driver.py','native_setup.py'))
    changed=[name for name in sorted(paths) if old['files'].get(name)!=current['files'].get(name)]
    assert changed==['egg_harness/skill_policy.py'],changed
    trees=[]
    for folder in (original,base.HARNESS):
        tree=ast.parse((folder/'egg_harness/skill_policy.py').read_text())
        nodes=[node for node in tree.body if isinstance(node,ast.Assign)
               and any(isinstance(target,ast.Name) and target.id=='JOINT_INSTRUCTIONS' for target in node.targets)]
        assert len(nodes)==1 and isinstance(nodes[0].value,ast.Constant)
        nodes[0].value=ast.Constant(value='approved joint-limit prompt')
        trees.append(ast.dump(tree,include_attributes=False))
    assert trees[0]==trees[1],'Runtime changed outside approved joint prompt'
    return {'baseline':old['commit'],'approved_runtime':current['commit'],'changed_runtime_files':changed,
            'only_joint_prompt_diff':True}


def case_durability(folder, hdfs_root):
    record=suite.read(folder/'durability.json')
    assert record['final'] and record['shards']>0
    receipts=[]
    for index in range(record['shards']):
        path=folder/f'shard-{index:04d}.json'; receipt=suite.read(path)
        assert receipt['verified_readback'] and receipt['bytes']>0
        assert receipt['uri']==hdfs_root+'/'+folder.name+f'-shard-{index:04d}.tar.gz'
        assert len(receipt['sha256'])==64
        receipts.append(receipt)
    return receipts


def public_observation_boundary(case):
    """Match actual public robot state and RGB to this episode's past frames."""
    frames = case / 'episode/frames'
    model = case / 'policy/model'
    cameras = {'cam_head', 'cam_left_wrist', 'cam_right_wrist'}
    sizes = {arm + '_' + field: size for arm in ('left', 'right')
             for field, size in (('arm_joint_state', 6), ('ee_pose', 7),
                                 ('ee_joint_state', 1), ('gripper_measured_joints', 2))}
    reviewed_fields = set(sizes) - {'left_arm_joint_state', 'right_arm_joint_state'}
    frame_cache = {}
    image_hashes = {}

    def validate_frame(item, step):
        assert set(item) <= {'step', 'state', 'cameras', 'camera_calibration_id',
                            'action_interface', 'instruction', 'ended', 'budget'}
        assert item['step'] == step and set(item['state']) == set(sizes)
        assert set(item['cameras']) == cameras
        for field, size in sizes.items():
            values = item['state'][field]
            assert isinstance(values, list) and len(values) == size
            assert all(type(v) in (int, float) and math.isfinite(v) for v in values)
        for path in item['cameras'].values():
            path = Path(path)
            assert not path.is_symlink() and path.resolve().parent == frames.resolve()
            assert path.suffix == '.jpg' and path.is_file()

    def frame(step, current):
        assert type(step) is int and 0 <= step <= current, 'Non-public future frame'
        if step not in frame_cache:
            name = 'initial.json' if step == 0 else f'step-{step:04d}.json'
            item = suite.read(frames / name)
            validate_frame(item, step)
            frame_cache[step] = item
        return frame_cache[step]

    initial = frame(0, 0)
    requests = reviews = 0
    for path in sorted(model.glob('*.request.json')):
        request = suite.read(path)
        context = json.loads(request['question'])
        step = context['step']
        assert request['step'] == step
        current = frame(step, step)
        assert context['state'] == current['state']
        assert context['home'] == {a: initial['state'][a + '_ee_pose'] for a in ('left', 'right')}
        assert context['home_joints'] == {a: initial['state'][a + '_arm_joint_state'] for a in ('left', 'right')}
        images = {camera: (current, camera) for camera in cameras}
        previous = context['previous_observation']
        if previous is not None:
            assert set(previous) == {'step', 'state', 'active_arm'}
            prior = frame(previous['step'], step)
            assert previous['state'] == prior['state']
            images['previous_head'] = (prior, 'cam_head')
            active = previous['active_arm']
            arms = active if isinstance(active, list) else ([] if active is None else [active])
            assert len(arms) == len(set(arms)) and set(arms) <= {'left', 'right'}
            for arm in arms:
                images['previous_' + arm + '_wrist'] = (prior, 'cam_' + arm + '_wrist')
        records = context['reviewed_observations']
        assert isinstance(records, list) and len(records) <= 6
        for record in records:
            assert set(record) == {'image', 'step', 'camera', 'state', 'camera_calibration_id', 'image_sha256'}
            old = frame(record['step'], step)
            assert record['camera'] in cameras
            assert record['state'] == {field: old['state'][field] for field in reviewed_fields}
            assert record['camera_calibration_id'] == old['camera_calibration_id']
            name = f"review_{record['step']}_{record['camera']}"
            assert record['image'] == name and name not in images
            images[name] = (old, record['camera'])
            reviews += 1
        assert context['temporal_geometry']['reviewed_samples'] == [
            {k: record[k] for k in ('step', 'camera', 'image')} for record in records]
        assert len(request['images']) == len(images)
        assert {item['name'] for item in request['images']} == set(images)
        actual_hashes = {}
        for item in request['images']:
            source, camera = images[item['name']]
            image = Path(item['path'])
            assert not image.is_symlink() and image.resolve().parent == frames.resolve()
            suffix = '-' + camera + '.jpg'
            assert image.name.endswith(suffix)
            tag = image.name[:-len(suffix)]
            paired_path = frames / (tag + '.json')
            assert not paired_path.is_symlink()
            paired = suite.read(paired_path)
            validate_frame(paired, source['step'])
            assert paired['state'] == source['state']
            assert paired['camera_calibration_id'] == source['camera_calibration_id']
            assert image.resolve() == Path(paired['cameras'][camera]).resolve()
            if item['name'].startswith('review_'):
                assert image.resolve() == Path(source['cameras'][camera]).resolve()
            else:
                assert tag.startswith('policy-') and (case / 'episode/commands' / (tag + '.json')).is_file()
            if image not in image_hashes:
                image_hashes[image] = base.digest(image)
            assert item['sha256'] == image_hashes[image]
            actual_hashes[item['name']] = item['sha256']
        assert all(record['image_sha256'] == actual_hashes[record['image']] for record in records)
        requests += 1
    assert requests > 0
    return {'case': case.name, 'requests': requests, 'reviewed_frames': reviews,
            'unique_rgb_files': len(image_hashes), 'public_robot_fields': sorted(sizes),
            'scope': 'Exact recorded public state, same-episode past-frame paths and RGB hashes; not a semantic proof of model reasoning.'}


def audit(root):
    assert os.environ['ARNOLD_WORKER_ID']=='0000000'
    assert not suite.git(root,'status','--porcelain'), 'Commit the final auditor and review first'
    output=root/'artifacts/five-layout-suite'; plan=suite.read(output/'plan.json')
    tasks=base.task_names()
    assert plan['tasks']==tasks and plan['layouts']==list(range(20,25))
    assert plan['tasks_sha256']==base.digest(base.TASK_FILE)
    data=suite.report(root,output); outcomes=complete_scope(data,tasks)
    runtime=runtime_boundary()
    old=root/'artifacts/diagnose-suite'
    prior=suite.read(old/'completion-audit.json')
    assert prior['requirements_verified'] and len(prior['cases'])==32
    assert {(r['task'],r['layout']) for r in prior['cases']}=={(t,l) for t in tasks for l in (20,21)}
    assert suite.read(old/'final-report-receipt.json')['verified_readback']
    subprocess.run([sys.executable,str(root/'scripts/audit_diagnose_live.py'),'--root',str(root)],check=True)
    old_rows={(r['task'],r['layout']):r for r in suite.read(old/'coverage-plan.json')['rows']}
    public_inputs=[]
    for item in prior['cases']:
        row=old_rows[(item['task'],item['layout'])]; folder=Path(row['folder'])
        if row['origin']=='historical_action_comparison':
            assert base.digest(Path(row['native_result']))==item['native_sha256']
            config=folder/f"config-{row['layout']}.json"
            assert base.digest(config)==row['config_sha256'] and suite.read(config)['control_setting']=='joint'
        else:
            assert base.digest(folder/'result.json')==item['result_sha256']
            config=suite.read(folder/'config.json'); launch=suite.read(folder/'launch.json')
            assert config['task']==row['task'] and config['action_interface']=='joint'
            assert launch['native_initial_state'] and launch['layout_index']==row['layout']
            if row['origin']=='historical_eight_task': assert base.digest(folder/'config.json')==row['config_sha256']
            if row['origin']=='new':
                public_inputs.append(public_observation_boundary(Path(suite.read(folder/'status.json')['worker_case'])))
    rounds=[]; all_receipts=[]
    for layout in suite.LAYOUTS:
        folder=output/f'L{layout}'; previous=suite.prior_round(root,output,layout)
        campaign=suite.read(folder/'campaign.json')
        assert datetime.fromisoformat(campaign['at'])>datetime.fromisoformat(suite.read(previous/'final-report-receipt.json')['at'])
        assert suite.read(folder/'completed.json')['cases']==16
        assert suite.read(folder/'final-report-receipt.json')['verified_readback']
        checked=suite.audit_round(root,output,layout,final=True)
        retried=0
        for row in checked:
            case=folder/f"cases/{row['task']}-L{layout}-attempt1"
            all_receipts+=case_durability(case,suite.HDFS+f'/L{layout}')
            worker_case=Path(suite.read(case/'status.json')['worker_case'])
            public_inputs.append(public_observation_boundary(worker_case))
            policy=suite.read(worker_case/'policy/model/transport-policy.json')
            assert policy['max_retries']==3 and policy['delays_seconds']==[10,30,60]
            assert policy['same_request_and_scene'] and policy['robot_action_retries']==0 and not policy['provider_switching']
            path=worker_case/'policy/model/transport-retries.jsonl'
            retried+=retry_records([json.loads(line) for line in path.read_text().splitlines()] if path.exists() else [])
        rounds.append({'layout':layout,'cases':checked,'retried_model_calls':retried,
            'source_commit':suite.read(folder/'ready.json')['source_commit'],
            'skill_manifest':suite.read(folder/'skills-manifest.json'),
            'input_review_sha256':base.digest(folder/'input-review.md')})
    assert len(public_inputs)==62
    record={'at':runner.now(),'requirements_verified':True,'total_cases':80,'tasks':tasks,'layouts':list(range(20,25)),
        'audit_source_commit':suite.git(root,'rev-parse','HEAD'),
        'auditor_sha256':base.digest(Path(__file__)),
        'runtime':runtime,'outcomes':dict(outcomes),'historical_stage':prior,'new_rounds':rounds,
        'archive_shards_verified_by_recorded_readback':len(all_receipts),
        'new_azure_cases':62,'historical_provider_exempt_cases':18,
        'public_observation_checks':public_inputs,
        'scope':'All requested development attempts ended; this does not mean80 native successes. Older inputs keep their original provenance.'}
    save(output/'objective-audit.json',record)
    save(output/'all-round-shard-receipts.json',all_receipts)
    return output,record


def archive_and_notify(root, output, audit):
    archive=suite.SCRATCH/'final-80-case-report.tar.gz'
    with tarfile.open(archive,'w:gz') as target:
        for name in ('plan.json','coverage.json','coverage.md','objective-audit.json','all-round-shard-receipts.json'):
            target.add(output/name,arcname=name)
        for name in ('AZURE-FIVE-LAYOUT-PROGRESS.md','SKILL-INPUT-AUDIT.md','TASK-SKILL-ITERATION.md'):
            target.add(root/name,arcname=name)
        target.add(root/'scripts/audit_five_layout_completion.py',arcname='audit_five_layout_completion.py')
        for layout in suite.LAYOUTS:
            folder=output/f'L{layout}'
            for name in ('completion-audit.json','final-report-receipt.json','source-inputs-receipt.json',
                         'campaign.json','ready.json','skills-manifest.json','input-review.md'):
                target.add(folder/name,arcname=f'L{layout}/{name}')
        old=root/'artifacts/diagnose-suite'
        for name in ('coverage-plan.json','completion-audit.json','final-report-receipt.json','coverage.md'):
            target.add(old/name,arcname='L20-L21/'+name)
    runner.hdfs_archive(archive,suite.HDFS+'/final-80-case-report.tar.gz',output/'final-report-receipt.json')
    message=('【General-Pickup】16任务×5布局共80场评测已结束并通过完整审查。新增62场均核验Azure请求，'
             '18场历史joint记录按授权复用；L22/L23/L24逐轮固定Skill并审查实际提示输入，Harness保持批准版本。'
             '结果分类：'+json.dumps(audit['outcomes'],ensure_ascii=False)+'。最终报告及证据已HDFS回读验证：'+str(output/'coverage.md'))
    request=urllib.request.Request(os.environ['EVAL_COMPLETION_WEBHOOK'],data=json.dumps(
        {'msg_type':'text','content':{'text':message}}).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=30) as response: result=json.load(response)
    save(output/'completion-notification.json',{'at':runner.now(),'message':message,'response':result})
    assert result['code']==0


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--archive-and-notify',action='store_true')
    args=parser.parse_args(); root=args.root.resolve()
    output,record=audit(root)
    if args.archive_and_notify: archive_and_notify(root,output,record)
    print(json.dumps({'verified_cases':record['total_cases'],'outcomes':record['outcomes']}))


if __name__=='__main__': main()
