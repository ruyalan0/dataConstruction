"""Sequential, independently audited L22/L23/L24 rounds after the L20/L21 suite."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace
import urllib.request

import diagnose_suite as base
from prepare_eight_eval import ASSET_REPO, BASE, REVISION, Tables, save
from restore_task_assets import restore
import run_eight_eval as runner
from report_eight_eval import end_reason, outcome_text

LAYOUTS = (22, 23, 24)
SCRATCH = Path('/tmp/astra-five-layouts-azure-20260917')
HDFS = runner.HDFS + '/five-layout-suite'


def read(path):
    return json.loads(path.read_text())


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def require_worker(root, output, label):
    assert os.environ['ARNOLD_WORKER_ID'] == '0000000'
    worker = base.resources(root)
    local = read(output/'preflight/local.json')
    assert worker['cpu'] > local['cpu'] and worker['memory_limit'] > local['memory_limit']
    assert worker['nas'] and worker['memory_used'] < worker['memory_limit'] * .7
    assert shutil.disk_usage('/tmp').free > 100 * 1024**3
    save(output/f'preflight/{label}-worker.json', worker)
    sys.path.insert(0, str(base.HARNESS))
    from egg_harness.freeze import verify
    assert verify(base.HARNESS)['commit'] == base.HARNESS_COMMIT


def inventory(root, output):
    assert not (output/'plan.json').exists()
    tasks = base.task_names()
    old = read(root/'artifacts/diagnose-suite/coverage-plan.json')
    assert old['tasks'] == tasks and old['layouts'] == [20, 21]
    save(output/'plan.json', {'at':runner.now(), 'tasks':tasks, 'layouts':[20,21,*LAYOUTS],
        'total':80, 'historical_stage':str(root/'artifacts/diagnose-suite'),
        'tasks_sha256':base.digest(base.TASK_FILE), 'new_provider':'azure-astra',
        'action_interface':'joint', 'native_initial_state':True,
        'round_order':list(LAYOUTS), 'harness_commit':base.HARNESS_COMMIT,
        'historical_provider_reuse':'Retain the previously authorized18 historical joint-only cases.'})
    save(output/'preflight/local.json', base.resources(root))
    report(root, output)


def assets(root, output):
    """Prepare all three future layouts; policy skills are pinned separately per round."""
    require_worker(root, output, 'assets')
    prep = output/'preparation'
    assert not (prep/'ready.json').exists(), 'Assets already prepared'
    assert base.digest(base.TASK_FILE) == read(output/'plan.json')['tasks_sha256']
    tasks = base.task_names()
    route, evidence = base.route_probe(
        f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{REVISION}/README.md')
    save(prep/'asset-route.json', evidence)
    doc_route, evidence = base.route_probe('https://robodojo-benchmark.com/doc/sim-tasks/organize-table/')
    save(prep/'documentation-route.json', evidence)

    def document(task):
        url = 'https://robodojo-benchmark.com/doc/sim-tasks/'+task.replace('_','-')+'/'
        data = base.get(url, doc_route)
        parser = Tables(); parser.feed(data.decode())
        assert any(row and row[0] == 'Instruction' for row in parser.rows), task
        assert any(row and row[0] == '100' for row in parser.rows), task
        path = prep/(task+'.html'); path.write_bytes(data)
        save(prep/(task+'.json'), {'task':task, 'url':url, 'fetched_at':runner.now(),
                                 'sha256':base.digest(path), 'rows':parser.rows})
    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(document, tasks))

    materialized = SCRATCH/'materialized'
    caches = [ASSET_REPO, base.SCRATCH/'materialized', Path('/tmp/astra-eight-tasks-5e17233/materialized')]

    def materialize(name):
        import hashlib
        pointer = subprocess.check_output(['git','-C',str(ASSET_REPO),'show',f'{REVISION}:{name}'])
        if pointer.startswith(b'version https://git-lfs'):
            expected = next(line.split('sha256:')[1] for line in pointer.decode().splitlines() if line.startswith('oid '))
            cached = next((p/name for p in caches if (p/name).exists() and base.digest(p/name) == expected), None)
            data = cached.read_bytes() if cached else base.get(
                f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{REVISION}/{name}',route,120)
        else:
            data = pointer; expected = hashlib.sha256(data).hexdigest()
        assert hashlib.sha256(data).hexdigest() == expected, name
        path = materialized/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        return {'path':name, 'sha256':expected, 'bytes':len(data)}

    names = [f'Assets/Eval_Layout/RoboDojo/arx_x5/0/{task}_{index}.json' for task in tasks for index in range(25)]
    names += [f'Assets/Traj/RoboDojo/imitate_sorting_sequence/0/{index}.pkl' for index in LAYOUTS]
    names += subprocess.check_output(['git','-C',str(ASSET_REPO),'ls-tree','-r','--name-only',REVISION,
              'Assets/Traj/RoboDojo/play_tic_tac_toe','Assets/Traj/RoboDojo/make_kong'],text=True).splitlines()
    with ThreadPoolExecutor(max_workers=32) as pool:
        records = list(pool.map(materialize, names))
    save(prep/'layouts-and-support-trajectory.json', {'revision':REVISION,'files':records})
    for task in tasks:
        receipt = prep/('assets-'+task)
        if receipt.exists():
            assert (receipt/'manifest.json').exists(), 'Incomplete asset restoration: '+task
            continue
        restore(SimpleNamespace(source_repo=ASSET_REPO,destination=materialized,
            curriculum=base.HARNESS/'configs/curriculum.json',
            layouts=materialized/'Assets/Eval_Layout/RoboDojo/arx_x5/0',task=task,
            indices=list(LAYOUTS),proxy=route,workers=32,receipts=receipt,
            reuse_root=[*caches[1:],BASE/'astra-Robodojo/artifacts/task-assets-v1',
                        BASE/'astra-Robodojo/artifacts/runtime']))
    for name in ('Robots','Sensor'):
        link = materialized/'Assets'/name
        if not link.exists():
            link.symlink_to((BASE/'Astra-Robodojo-RSI-0914/artifacts/suite-assets-v4'/name).resolve(),target_is_directory=True)
    save(prep/'ready.json', {'at':runner.now(),'tasks':tasks,'layouts':list(LAYOUTS),
                            'asset_revision':REVISION,'assets':str(materialized/'Assets')})
    print('ASSETS READY', flush=True)


def prepare(root, output, layout):
    require_worker(root, output, f'L{layout}-prepare')
    assert not git(root,'status','--porcelain'), 'Commit skills and orchestration before snapshotting'
    ready = read(output/'preparation/ready.json')
    assert layout in ready['layouts'] and ready['tasks'] == base.task_names()
    round_dir = output/f'L{layout}'; round_dir.mkdir(exist_ok=False)
    selection = read(root/'skills/suite-task-selection.json')
    assert set(selection['tasks']) == set(ready['tasks'])
    selected=set(selection['common']+[name for names in selection['tasks'].values() for name in names])
    for name in sorted(selected):
        destination=round_dir/'skills'/name/'SKILL.md'; destination.parent.mkdir(parents=True)
        shutil.copyfile(root/'skills'/name/'SKILL.md',destination)
    shutil.copytree(root/'scripts',round_dir/'source/scripts',ignore=shutil.ignore_patterns('__pycache__'))
    calibration=read(base.HARNESS/'memory/skills.json')['head_camera']
    save(round_dir/'calibration.json',{'head_camera':{key:calibration[key]
         for key in ('intrinsic','world_from_camera','convention')}})
    shutil.copyfile(root/'SKILL-INPUT-AUDIT.md',round_dir/'input-review.md')
    manifest = {str(p.relative_to(round_dir/'skills')):base.digest(p) for p in (round_dir/'skills').rglob('*') if p.is_file()}
    save(round_dir/'skills-manifest.json',{'skill_commit':git(root,'log','-1','--format=%H','--','skills'),'files':manifest})
    for task in ready['tasks']:
        config = read(base.HARNESS/'configs/classify-language-joint-development.json')
        config.update(task=task,astra_provider='azure-astra',action_interface='joint',
            assets=ready['assets'],object_assets=str(Path(ready['assets'])/'Object/RoboDojo'),
            memory=str(round_dir/'calibration.json'),skills_root=str(round_dir/'skills'),
            skill_names=selection['common']+selection['tasks'][task],
            public_task_requirements=public_requirements(read(output/f'preparation/{task}.json')),
            max_model_calls=500 if task=='imitate_sorting_sequence' else 400,request_timeout=300)
        for name in config['skill_names']: assert (round_dir/'skills'/name/'SKILL.md').is_file()
        save(round_dir/f'configs/{task}.json',config)
    pinned = {str(p.relative_to(round_dir)):base.digest(p) for prefix in ('skills','configs','source')
              for p in (round_dir/prefix).rglob('*') if p.is_file()}
    pinned['calibration.json']=base.digest(round_dir/'calibration.json')
    pinned['input-review.md']=base.digest(round_dir/'input-review.md')
    save(round_dir/'ready.json',{'at':runner.now(),'layout':layout,'source_commit':git(root,'rev-parse','HEAD'),
         'tasks_sha256':base.digest(base.TASK_FILE),'harness_commit':base.HARNESS_COMMIT,'pinned_files':pinned})
    for name in pinned: (round_dir/name).chmod(0o444)
    print(f'L{layout} PREPARED',flush=True)


def public_requirements(document):
    rows=[row for row in document['rows'] if row and row[0] in ('Instruction','Description','Comment')]
    completion=[row for row in document['rows'] if row and row[0]=='100']
    assert len(completion)==1 and any(row[0]=='Instruction' for row in rows)
    # Keep the public full-task definition; numeric partial rewards stay in evaluator reports.
    rows.append(['Completion',completion[0][1]])
    return {**document,'rows':rows}


def prior_round(root, output, layout):
    previous = root/'artifacts/diagnose-suite' if layout==22 else output/f'L{layout-1}'
    audit = read(previous/'completion-audit.json')
    assert audit['requirements_verified'] is True
    expected={(t,l) for t in base.task_names() for l in ((20,21) if layout==22 else (layout-1,))}
    assert len(audit['cases'])==len(expected)
    assert {(c['task'],c['layout']) for c in audit['cases']}==expected
    assert read(previous/'final-report-receipt.json')['verified_readback'] is True
    return previous


def allocate_cases(tasks, layout, devices):
    projected={gpu:vals[1] for gpu,vals in devices.items()}; cases=[]
    for task in tasks:
        available=[g for g,(total,_) in devices.items() if projected[g]+10000<=total-8000]
        assert available, 'Insufficient measured GPU memory for the requested cases'
        gpu=min(available,key=lambda g:projected[g]); projected[gpu]+=10000
        cases.append((task,layout,gpu))
    return cases, projected


def report(root, output):
    historical = read(root/'artifacts/diagnose-suite/coverage.json')
    rows = [{**row,'stage':'L20/L21'} for row in historical['rows']]
    for layout in LAYOUTS:
        for task in base.task_names():
            folder = output/f'L{layout}/cases/{task}-L{layout}-attempt1'
            row = {'task':task,'layout':layout,'stage':f'L{layout}','folder':str(folder),'phase':'queued'}
            if (folder/'status.json').exists(): row.update(phase='running',status=read(folder/'status.json'))
            if (folder/'result.json').exists():
                result = read(folder/'result.json')
                row.update(phase='finished',result=result,native=result.get('native'),end_reason=end_reason(folder,result))
            rows.append(row)
    assert len(rows)==80 and len({(r['task'],r['layout']) for r in rows})==80
    record={'at':runner.now(),'total':80,'finished':sum(r['phase']=='finished' for r in rows),'rows':rows}
    save(output/'coverage.json',record)
    lines=['# Sixteen tasks, five layouts','',f"Updated {record['at']}; ended {record['finished']}/80.",'',
           'L20/L21 reuse is unchanged. All new L22/L23/L24 episodes use Azure and joint-only control.',
           'Skill revisions differ by round; these are development results, not an unseen benchmark success rate.','',
           '| Task | L20 | L21 | L22 | L23 | L24 |','|---|---|---|---|---|---|']
    for task in base.task_names():
        cells=[]
        for layout in (20,21,*LAYOUTS):
            row=next(r for r in rows if r['task']==task and r['layout']==layout)
            if row['phase']=='finished':
                cells.append(outcome_text(row.get('native'),row['end_reason']))
            elif row['phase']=='running': cells.append(f"step {row['status']['step']}")
            else: cells.append('queued')
        lines.append('| '+task+' | '+' | '.join(cells)+' |')
    lines += ['','Prefix scores belong to incomplete attempts. Invalid native samples are unscored; policy stops, native failures and infrastructure errors remain separate.']
    (output/'coverage.md').write_text('\n'.join(lines)+'\n')
    return record


def audit_round(root, output, layout, *, final):
    sys.path.insert(0,str(base.HARNESS))
    from egg_harness.freeze import verify
    from egg_harness.native_responses import TOOL_INSTRUCTIONS
    from egg_harness.skill_policy import system_prompt
    assert verify(base.HARNESS)['commit']==base.HARNESS_COMMIT
    round_dir=output/f'L{layout}'; campaign=read(round_dir/'campaign.json')
    if not (round_dir/'completed.json').exists():
        pid=campaign['pid']; stat=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]
        assert stat!='Z' and 'five_layout_suite.py' in Path(f'/proc/{pid}/cmdline').read_bytes().decode()
    ready=read(round_dir/'ready.json')
    assert base.digest(base.TASK_FILE)==ready['tasks_sha256']
    for name,sha in ready['pinned_files'].items(): assert base.digest(round_dir/name)==sha,name
    manifest=read(round_dir/'skills-manifest.json')['files']
    calibration=read(round_dir/'calibration.json')
    assert set(calibration)=={'head_camera'}
    assert set(calibration['head_camera'])=={'intrinsic','world_from_camera','convention'}
    checked=[]
    for task,index,gpu in campaign['cases']:
        assert index==layout
        case=SCRATCH/f'L{layout}/runs/{task}-L{layout}-attempt1'
        folder=round_dir/'cases'/case.name
        config=read(case/'config.json'); launch=read(case/'launch.json')
        assert config['astra_provider']==launch['provider']=='azure-astra'
        assert config['task']==launch['task']==task
        assert config['action_interface']==launch['action_interface']=='joint'
        assert launch['layout_index']==layout and launch['native_initial_state']
        assert 'continuation_request' not in config, 'Historical continuation is outside this round'
        assert config['memory']==str(round_dir/'calibration.json')
        public=public_requirements(read(output/f'preparation/{task}.json'))
        assert config['public_task_requirements']==public
        skill_text='\n'.join((round_dir/'skills'/name/'SKILL.md').read_text() for name in config['skill_names'])
        expected_system=TOOL_INSTRUCTIONS+'\n'+system_prompt('joint')+'\n'+skill_text
        entry={'task':task,'layout':layout,'azure_requests':0,'started':False,'finished':(folder/'result.json').exists()}
        model=case/'policy/model'
        if (model/'native-tools.json').exists():
            native=read(case/'episode/provenance.json')
            assert native['task']==task and native['seeds']==[layout] and native['action_interface']=='joint'
            assert native['object_teleportation'] is False and native['depth_calibration'] is False
            tools=read(model/'native-tools.json')
            assert tools['provider']=='azure-astra' and tools['provider_allocation'] is None
            control=next(t for t in tools['tools'] if t['name']=='control_arms')
            assert set(control['parameters']['$defs'])=={'JointArm'}
            assert control['parameters']['$defs']['JointArm']['properties']['control']['const']=='joint'
            assert read(case/'policy/skills.json')=={str(round_dir/'skills'/name/'SKILL.md'):manifest[name+'/SKILL.md'] for name in config['skill_names']}
            for path in model.glob('*.request.json'):
                if not final and time.time()-path.stat().st_mtime<2: continue
                req=read(path)
                assert req['provider']=='azure-astra' and req['base_url']==tools['base_url']
                if path.name=='0001.request.json': assert req['step']==0, 'Policy did not begin at native step0'
                assert req['system']==expected_system, 'Unreviewed instructions in actual request'
                context=json.loads(req['question'])
                assert context['public_task_requirements']==public and context['action_interface']=='joint'
                assert set(context) <= {'step','state','home','instruction','memory','previous_result',
                    'recent_executed_segments','stalled_segments_this_controller','desired_gripper_commands',
                    'contact_estimates','previous_observation','review_capability','reviewed_observations',
                    'multiview_geometry','temporal_geometry','action_interface','joint_semantics','home_joints',
                    'arm_posture','previous_arm_posture','budget','public_task_requirements'}, 'Unexpected policy context'
                entry['azure_requests']+=1
            entry['started']=True
        if final:
            result=read(folder/'result.json')
            assert entry['started'] and entry['azure_requests']>0 and result['model_calls']>0, task
            assert entry['azure_requests']==result['model_calls'], 'Request evidence is missing'
            assert {p.name for p in model.glob('*.request.json')}=={
                f'{i:04d}.request.json' for i in range(1,result['model_calls']+1)}, 'Request sequence is incomplete'
            assert read(folder/'durability.json')['final'] is True, task
            entry.update(result_sha256=base.digest(folder/'result.json'),final_archive_verified=True)
        checked.append(entry)
    assert {(c['task'],c['layout']) for c in checked}=={(t,layout) for t in base.task_names()}
    save(round_dir/('completion-audit.json' if final else 'live-audit.json'),
         {'at':runner.now(),'requirements_verified':final,'cases':checked})
    return checked


def finalize(root, output, layout):
    round_dir=output/f'L{layout}'
    assert read(round_dir/'completed.json')['cases']==16
    checked=audit_round(root,output,layout,final=True)
    report(root,output)
    archive=SCRATCH/f'L{layout}/final-report.tar.gz'
    with tarfile.open(archive,'w:gz') as target:
        for name in ('campaign.json','ready.json','skills-manifest.json','completed.json','completion-audit.json','source-inputs-receipt.json'):
            target.add(round_dir/name,arcname=name)
        target.add(output/'coverage.json',arcname='coverage.json')
        target.add(output/'coverage.md',arcname='coverage.md')
        for item in checked:
            folder=round_dir/f"cases/{item['task']}-L{layout}-attempt1"
            for name in ('config.json','launch.json','result.json','durability.json'):
                target.add(folder/name,arcname=f"cases/{folder.name}/{name}")
    runner.hdfs_archive(archive,HDFS+f'/L{layout}/final-report.tar.gz',round_dir/'final-report-receipt.json')
    message=f'【General-Pickup】16任务 L{layout} 共16场已结束；实际 Azure 请求、joint-only、原生初始状态、Skill 版本及最终 HDFS 分片已核验。结果：'+str(output/'coverage.md')
    request=urllib.request.Request(os.environ['EVAL_COMPLETION_WEBHOOK'],data=json.dumps(
        {'msg_type':'text','content':{'text':message}}).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=30) as response: result=json.load(response)
    save(round_dir/'completion-notification.json',{'at':runner.now(),'response':result,'message':message})
    assert result['code']==0


def run(root, output, layout):
    round_dir=output/f'L{layout}'
    assert not (round_dir/'campaign.json').exists(), 'Inspect the existing handle; do not duplicate'
    assert not git(root,'status','--porcelain')
    previous=prior_round(root,output,layout)
    require_worker(root,output,f'L{layout}-launch')
    ready=read(round_dir/'ready.json')
    assert ready['source_commit']==git(root,'rev-parse','HEAD')
    assert base.digest(base.TASK_FILE)==ready['tasks_sha256']
    for name,sha in ready['pinned_files'].items(): assert base.digest(round_dir/name)==sha,name
    raw=subprocess.check_output(['nvidia-smi','--query-gpu=index,memory.total,memory.used','--format=csv,noheader,nounits'],text=True)
    devices={int(v[0]):[int(v[1]),int(v[2])] for line in raw.splitlines() if (v:=line.split(','))}
    cases,projected=allocate_cases(base.task_names(),layout,devices)
    save(round_dir/'admission.json',{'at':runner.now(),'gpu_inventory':devices,'projected_mib':projected,
         'reserved_mib_per_case':10000,'remaining_mib_per_gpu':8000,'cases':cases})
    scratch=SCRATCH/f'L{layout}'; scratch.mkdir(parents=True,exist_ok=True)
    runner.HDFS=HDFS+f'/L{layout}'
    subprocess.run(['hdfs','dfs','-mkdir','-p',runner.HDFS],check=True)
    archive=scratch/'source-inputs.tar.gz'
    with tarfile.open(archive,'w:gz') as target:
        for name in ('skills','configs','source','calibration.json','input-review.md','ready.json','skills-manifest.json','admission.json'):
            target.add(round_dir/name,arcname=name)
        for name in ('plan.json','preparation','preflight'):
            target.add(output/name,arcname=name)
        target.add(base.HARNESS,arcname='frozen-harness')
        target.add(root/'AZURE-FIVE-LAYOUT-PROGRESS.md',arcname='AZURE-FIVE-LAYOUT-PROGRESS.md')
        target.add(root/'SKILL-INPUT-AUDIT.md',arcname='SKILL-INPUT-AUDIT.md')
    runner.hdfs_archive(archive,runner.HDFS+'/source-inputs.tar.gz',round_dir/'source-inputs-receipt.json')
    save(round_dir/'campaign.json',{'at':runner.now(),'pid':os.getpid(),'worker':'0000000','cases':cases,
         'provider':'azure-astra','action_interface':'joint','native_initial_state':True,
         'source_commit':ready['source_commit'],'previous_round':str(previous),'hdfs_root':runner.HDFS})
    args=SimpleNamespace(root=round_dir/'source',scratch=scratch,indices=[layout],timeout=10800,commit=ready['source_commit'])
    results=[]
    with ThreadPoolExecutor(max_workers=16) as pool:
        pending={pool.submit(runner.run_case,args,task,index,gpu,campaign=round_dir,baseline=base.HARNESS,
                   harness_commit=base.HARNESS_COMMIT,provider='azure-astra',skill_revision=f'five-layout-L{layout}'):(task,index)
                 for task,index,gpu in cases}
        while pending:
            for future in list(pending):
                if not future.done(): continue
                task,index=pending.pop(future)
                # Propagate durability/setup failures; never certify an unaudited round.
                results.append(future.result())
                save(round_dir/'results.json',results)
                print(json.dumps(results[-1]),flush=True)
            report(root,output)
            if pending: time.sleep(15)
    runner.BACKUPS.shutdown(wait=True)
    save(round_dir/'completed.json',{'at':runner.now(),'cases':len(results)})
    finalize(root,output,layout)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['inventory','assets','prepare','run','report','audit','finalize'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--layout',type=int,choices=LAYOUTS)
    args=parser.parse_args(); root=args.root.resolve(); output=root/'artifacts/five-layout-suite'
    if args.stage in ('prepare','run','audit','finalize'): assert args.layout is not None
    if args.stage=='inventory': inventory(root,output)
    elif args.stage=='assets': assets(root,output)
    elif args.stage=='prepare': prepare(root,output,args.layout)
    elif args.stage=='run': run(root,output,args.layout)
    elif args.stage=='finalize': finalize(root,output,args.layout)
    elif args.stage=='audit':
        checked=audit_round(root,output,args.layout,final=False)
        print(json.dumps({'cases':len(checked),'started':sum(c['started'] for c in checked),
              'finished':sum(c['finished'] for c in checked),'azure_requests':sum(c['azure_requests'] for c in checked)}))
    else:
        data=report(root,output); print(json.dumps({'finished':data['finished'],'total':data['total']}))


if __name__=='__main__': main()
