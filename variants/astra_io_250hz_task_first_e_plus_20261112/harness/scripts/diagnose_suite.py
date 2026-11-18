"""Fill missing joint-only L20/L21 coverage using the Azure gateway exclusively."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
from types import SimpleNamespace
import urllib.request

from prepare_eight_eval import ASSET_REPO, BASE, PROXY, REVISION, Tables, save
from restore_task_assets import restore
import run_eight_eval as runner
from report_eight_eval import end_reason, outcome_text

OLD = BASE/'Astra-Robodojo-RSI-0914-eval-eight'
HARNESS = OLD/'artifacts/harness-joint-margin'
HARNESS_COMMIT = '64998f80b0afe3bc5af9d0e06417445296de5c2b'
TASK_FILE = BASE/'Astra-diagnose/tasks.md'
NEW_TASKS = ['organize_table', 'imitate_sorting_sequence', 'arrange_largest_number',
             'pack_objects_into_box', 'classify_objects', 'fold_clothes', 'put_bottles_into_dustbin']
SKILLS = {'imitate_sorting_sequence': ['imitate-sorting-sequence', 'cross-side-handoff'],
          'arrange_largest_number': ['arrange-largest-number'],
          'classify_objects': ['classify-objects'],
          'put_bottles_into_dustbin': ['put-bottles-into-dustbin', 'cross-side-handoff']}
COMMON = ['visual-contact-control', 'upright-grasp-approach', 'transport-retention']
SCRATCH = Path('/tmp/astra-diagnose-joint-azure-20260916')
HDFS = runner.HDFS+'/azure-missing-joint-suite'


def digest(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def task_names():
    names = [match.group(1) for line in TASK_FILE.read_text().splitlines()
             if (match := re.fullmatch(r'\|\s*([a-z][a-z0-9_]+)\s*\|', line))]
    assert len(names) == len(set(names)) == 16
    return names


def inventory(root, output):
    assert not (output/'coverage-plan.json').exists(), 'Inventory already fixed'
    rows = []
    for task in task_names():
        for layout in (20, 21):
            row = {'task': task, 'layout': layout}
            if task in NEW_TASKS:
                row.update(origin='new', folder=str(output/'cases'/f'{task}-L{layout}-attempt1'))
            elif task == 'classify_objects_by_language':
                folder = BASE/'Astra-Robodojo-RSI-0914-action-ablation/artifacts/action-comparison/campaign-v1'/(
                    'joint-L20' if layout == 20 else 'joint-L21-retry1')
                config_path = folder/f'config-{layout}.json'
                config = json.loads(config_path.read_text())
                provenance = json.loads((folder/f'seed-{layout}/provenance.json').read_text())
                policy = json.loads((folder/f'policy-{layout}/policy-result.json').read_text())
                assert config['task'] == task and config['control_setting'] == 'joint'
                assert provenance['seeds'] == [layout] and provenance['control_setting'] == 'joint'
                assert policy['control_setting'] == 'joint' and policy['model_calls'] > 0
                row.update(origin='historical_action_comparison', folder=str(folder),
                           native_result=str(folder/f'seed-{layout}/runner-result.json'),
                           provider='historical Azure credentials', config_sha256=digest(config_path),
                           mode_evidence=str(folder/f'policy-{layout}/policy-result.json'))
            else:
                folder = OLD/'artifacts/eight-task-eval/cases'/f'{task}-L{layout}-attempt1'
                config_path = folder/'config.json'
                config = json.loads(config_path.read_text())
                launch = json.loads((folder/'launch.json').read_text())
                status = json.loads((folder/'status.json').read_text())
                assert config['task'] == task and config['action_interface'] == 'joint'
                assert launch['layout_index'] == layout and launch['native_initial_state']
                assert status['model_calls'] > 0
                row.update(origin='historical_eight_task', folder=str(folder),
                           provider=config['astra_provider'], config_sha256=digest(config_path),
                           mode_evidence=str(folder/'launch.json'))
            rows.append(row)
    assert sum(row['origin'] == 'new' for row in rows) == 14
    save(output/'coverage-plan.json', {'at': runner.now(), 'tasks_file': str(TASK_FILE),
         'tasks_sha256': digest(TASK_FILE), 'tasks': task_names(), 'layouts': [20, 21],
         'action_interface': 'joint', 'new_provider': 'azure-astra', 'reuse_any_historical_provider': True,
         'harness': str(HARNESS), 'harness_commit': HARNESS_COMMIT, 'rows': rows})
    shutil.copyfile(TASK_FILE, output/'tasks.md')
    save(output/'preflight/local.json', resources(root))
    print(json.dumps({'reused': 18, 'new': 14, 'total': len(rows)}))


def resources(root):
    return json.loads(subprocess.check_output([sys.executable, str(root/'scripts/resources.py'), '--cpu-only'], text=True))


def get(url, route, timeout=45):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(
        {} if route == 'direct' else {'http': route, 'https': route}))
    with opener.open(url, timeout=timeout) as response: return response.read()


def route_probe(url):
    def measure(route):
        started = time.monotonic()
        try: return {'route': route, 'bytes': len(get(url, route, 15)), 'seconds': time.monotonic()-started}
        except Exception as error: return {'route': route, 'error': str(error), 'seconds': time.monotonic()-started}
    with ThreadPoolExecutor(max_workers=2) as pool: records = list(pool.map(measure, ['direct', PROXY]))
    reachable = [row for row in records if 'error' not in row]
    assert reachable, 'Neither configured download route is reachable'
    return min(reachable, key=lambda row: row['seconds'])['route'], records


def prepare(root, output):
    assert os.environ['ARNOLD_WORKER_ID'] == '0000000'
    assert not (output/'preparation/ready.json').exists(), 'Preparation already complete'
    sys.path.insert(0, str(HARNESS))
    from egg_harness.freeze import verify
    assert verify(HARNESS)['commit'] == HARNESS_COMMIT
    assert shutil.disk_usage('/tmp').free > 100*1024**3
    worker = resources(root); local = json.loads((output/'preflight/local.json').read_text())
    assert worker['cpu'] > local['cpu'] and worker['memory_limit'] > local['memory_limit'] and worker['nas']
    save(output/'preflight/preparation-worker.json', worker)
    prep = output/'preparation'; prep.mkdir(parents=True, exist_ok=True)
    route, evidence = route_probe(f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{REVISION}/README.md')
    save(prep/'asset-route.json', evidence)
    doc_route, evidence = route_probe('https://robodojo-benchmark.com/doc/sim-tasks/organize-table/')
    save(prep/'documentation-route.json', evidence)
    def document(task):
        url = 'https://robodojo-benchmark.com/doc/sim-tasks/'+task.replace('_', '-')+'/'
        data = get(url, doc_route); parser = Tables(); parser.feed(data.decode())
        assert any(row and row[0] == 'Instruction' for row in parser.rows), task
        assert any(row and row[0] == '100' for row in parser.rows), task
        (prep/(task+'.html')).write_bytes(data)
        record = {'task':task, 'url':url, 'fetched_at':runner.now(),
                  'sha256':hashlib.sha256(data).hexdigest(), 'rows':parser.rows}
        save(prep/(task+'.json'), record)
        return task, record
    with ThreadPoolExecutor(max_workers=7) as pool: docs = dict(pool.map(document, NEW_TASKS))
    materialized = SCRATCH/'materialized'
    def materialize(name):
        pointer = subprocess.check_output(['git','-C',str(ASSET_REPO),'show',f'{REVISION}:{name}'])
        if pointer.startswith(b'version https://git-lfs'):
            expected = next(line.split('sha256:')[1] for line in pointer.decode().splitlines() if line.startswith('oid '))
            cache = ASSET_REPO/name
            data = cache.read_bytes() if cache.exists() and digest(cache) == expected else get(
                f'https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/resolve/{REVISION}/{name}', route, 120)
            assert hashlib.sha256(data).hexdigest() == expected, name
        else: data = pointer; expected = hashlib.sha256(data).hexdigest()
        target = materialized/name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {'path':name, 'sha256':expected, 'bytes':len(data)}
    names = [f'Assets/Eval_Layout/RoboDojo/arx_x5/0/{task}_{index}.json'
             for task in NEW_TASKS for index in range(22)]
    names += [f'Assets/Traj/RoboDojo/imitate_sorting_sequence/0/{index}.pkl' for index in (20,21)]
    with ThreadPoolExecutor(max_workers=32) as pool: records = list(pool.map(materialize, names))
    save(prep/'layouts-and-support-trajectory.json', {'revision':REVISION, 'files':records})
    layouts = materialized/'Assets/Eval_Layout/RoboDojo/arx_x5/0'
    for task in NEW_TASKS:
        receipt = prep/('assets-'+task)
        if receipt.exists():
            assert (receipt/'manifest.json').exists(), 'Incomplete asset restoration: '+task
            continue
        restore(SimpleNamespace(source_repo=ASSET_REPO, destination=materialized,
            curriculum=HARNESS/'configs/curriculum.json', layouts=layouts, task=task,
            indices=[20,21], proxy=route, workers=32, receipts=receipt,
            reuse_root=[Path('/tmp/astra-eight-tasks-5e17233/materialized'),
                        BASE/'astra-Robodojo/artifacts/task-assets-v1', BASE/'astra-Robodojo/artifacts/runtime']))
    assets = materialized/'Assets'
    for name in ('Robots','Sensor'):
        link = assets/name
        if not link.exists(): link.symlink_to((BASE/'Astra-Robodojo-RSI-0914/artifacts/suite-assets-v4'/name).resolve(), target_is_directory=True)
    shutil.copytree(root/'skills', output/'skills', ignore=shutil.ignore_patterns('__pycache__'))
    skill_hashes = {str(p.relative_to(output/'skills')):digest(p) for p in (output/'skills').rglob('*') if p.is_file()}
    save(output/'skills-manifest.json', {'skill_commit':subprocess.check_output(
         ['git','-C',str(root),'log','-1','--format=%H','--','skills'],text=True).strip(), 'files':skill_hashes})
    for task in NEW_TASKS:
        config = json.loads((HARNESS/'configs/classify-language-joint-development.json').read_text())
        config.update(task=task, astra_provider='azure-astra', action_interface='joint',
            assets=str(assets), object_assets=str(assets/'Object/RoboDojo'),
            memory=str(HARNESS/'memory/skills.json'), skills_root=str(output/'skills'),
            skill_names=COMMON+SKILLS.get(task, []), public_task_requirements=docs[task],
            max_model_calls=500 if task == 'imitate_sorting_sequence' else 400, request_timeout=300)
        for name in config['skill_names']: assert (output/'skills'/name/'SKILL.md').is_file()
        save(output/'configs'/f'{task}.json', config)
    save(prep/'ready.json', {'at':runner.now(), 'tasks':NEW_TASKS, 'layouts':[20,21],
         'provider':'azure-astra','action_interface':'joint','assets':str(assets),
         'harness_commit':HARNESS_COMMIT,'asset_revision':REVISION,
         'config_hashes':{p.name:digest(p) for p in (output/'configs').glob('*.json')}})
    for path in [*(output/'skills').rglob('*'), *(output/'configs').glob('*.json')]:
        if path.is_file(): path.chmod(0o444)
    print('PREPARATION COMPLETE', flush=True)


def report(output):
    plan = json.loads((output/'coverage-plan.json').read_text())
    rows = []
    for row in plan['rows']:
        row = dict(row); folder = Path(row['folder'])
        if row['origin'] == 'historical_action_comparison':
            native = json.loads(Path(row['native_result']).read_text())
            row.update(phase='finished', native=native,
                       end_reason=end_reason(folder,{'native':native}))
        elif (folder/'result.json').exists():
            result = json.loads((folder/'result.json').read_text())
            row.update(phase='finished', result=result, native=result.get('native'), end_reason=end_reason(folder,result))
        elif (folder/'status.json').exists(): row.update(phase='running', status=json.loads((folder/'status.json').read_text()))
        else: row['phase'] = 'queued'
        if row['origin'] == 'historical_eight_task':
            row['skill_followups'] = []
            for revision,attempt in [('v1-joint-margin-pouring',2),('v2-task-strategies',3)]:
                followup=OLD/'artifacts/skill-iterations'/revision/'cases'/f"{row['task']}-L{row['layout']}-attempt{attempt}"
                if not followup.exists() or (followup/'administrative-stop.json').exists(): continue
                item={'revision':revision,'folder':str(followup)}
                if (followup/'result.json').exists():
                    result=json.loads((followup/'result.json').read_text())
                    item.update(phase='finished',native=result.get('native'),end_reason=end_reason(followup,result))
                else: item.update(phase='running',status=json.loads((followup/'status.json').read_text()))
                row['skill_followups'].append(item)
        rows.append(row)
    data = {'at':runner.now(), 'total':len(rows), 'finished':sum(r['phase']=='finished' for r in rows), 'rows':rows}
    save(output/'coverage.json', data)
    lines = ['# Joint-only L20/L21 coverage', '', f"Updated: {data['at']}; ended {data['finished']}/{data['total']}.", '',
             'Historical joint-only runs are reused across API providers; all new runs use azure-astra.',
             'Historical Harness/skill revisions differ. This is a coverage report, not a controlled success-rate comparison.', '',
             '| Task | Layout | Origin | Outcome |', '|---|---|---|---|']
    for row in rows:
        if row['phase']=='finished':
            n=row.get('native'); value = outcome_text(n,row['end_reason'])
        elif row['phase']=='running': value=f"step {row['status']['step']}; calls {row['status']['model_calls']}"
        else: value='queued'
        lines.append(f"| {row['task']} | L{row['layout']} | {row['origin']} | {value} |")
    lines.extend(['', 'Prefix scores are incomplete episode observations; stops and infrastructure errors are not native terminal failures.'])
    followups=[(row, item) for row in rows for item in row.get('skill_followups',[])]
    if followups:
        lines.extend(['','## Existing skill follow-ups (no additional reruns launched)','',
                      '| Task | Layout | Revision | Outcome |','|---|---|---|---|'])
        for row,item in followups:
            n=item.get('native')
            value=outcome_text(n,item.get('end_reason','running'))
            if item['phase']=='running': value=f"step {item['status']['step']}; calls {item['status']['model_calls']}"
            lines.append(f"| {row['task']} | L{row['layout']} | {item['revision']} | {value} |")
    (output/'coverage.md').write_text('\n'.join(lines)+'\n')
    return data


def run(root, output):
    assert os.environ['ARNOLD_WORKER_ID'] == '0000000'
    assert not (output/'campaign.json').exists(), 'Already launched; inspect existing process instead'
    assert not subprocess.check_output(['git','-C',str(root),'status','--porcelain']).strip()
    ready = json.loads((output/'preparation/ready.json').read_text())
    plan = json.loads((output/'coverage-plan.json').read_text())
    assert digest(TASK_FILE) == plan['tasks_sha256'], 'Requested task list changed; reconcile coverage before launch'
    sys.path.insert(0, str(HARNESS))
    from egg_harness.freeze import verify
    assert verify(HARNESS)['commit'] == HARNESS_COMMIT
    skills = json.loads((output/'skills-manifest.json').read_text())
    assert {str(p.relative_to(output/'skills')):digest(p) for p in (output/'skills').rglob('*') if p.is_file()} == skills['files']
    assert {p.name:digest(p) for p in (output/'configs').glob('*.json')} == ready['config_hashes']
    worker=resources(root); local=json.loads((output/'preflight/local.json').read_text())
    assert worker['cpu']>local['cpu'] and worker['memory_limit']>local['memory_limit'] and worker['nas']
    assert worker['memory_used'] < worker['memory_limit']*.7
    save(output/'preflight/launch-worker.json',worker)
    raw=subprocess.check_output(['nvidia-smi','--query-gpu=index,memory.total,memory.used','--format=csv,noheader,nounits'],text=True)
    devices={int(fields[0]):[int(fields[1]),int(fields[2])] for line in raw.splitlines() if (fields:=line.split(','))}
    cases=[]; projected={gpu:value[1] for gpu,value in devices.items()}
    for task in NEW_TASKS:
        for layout in (20,21):
            available=[gpu for gpu,(total,_) in devices.items() if projected[gpu]+10000 <= total-8000]
            assert available, 'Insufficient actual GPU memory for14 additional cases'
            gpu=min(available,key=lambda g:projected[g]); projected[gpu]+=10000
            cases.append((task,layout,gpu))
    save(output/'preflight/admission.json',{'at':runner.now(),'gpu_inventory':devices,'cases':cases,
         'reserved_mib_per_case':10000,'remaining_mib_per_gpu':8000,'projected_mib':projected})
    args=SimpleNamespace(root=root,scratch=SCRATCH,indices=[20,21],timeout=10800,
         commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip())
    runner.HDFS=HDFS
    subprocess.run(['hdfs','dfs','-mkdir','-p',HDFS],check=True)
    archive=SCRATCH/'source-inputs.tar.gz'
    with tarfile.open(archive,'w:gz') as target:
        for name in ['configs','skills','skills-manifest.json','coverage-plan.json','tasks.md','preparation','preflight']:
            target.add(output/name,arcname=name)
        target.add(HARNESS,arcname='frozen-harness')
        target.add(root/'AZURE-TASK-SUITE-PROGRESS.md',arcname='AZURE-TASK-SUITE-PROGRESS.md')
        for name in ['diagnose_suite.py','run_eight_eval.py','prepare_eight_eval.py','eval_retry_policy.py','report_eight_eval.py','resources.py']:
            target.add(root/'scripts'/name,arcname='orchestration/'+name)
    runner.hdfs_archive(archive,HDFS+'/source-inputs.tar.gz',output/'source-inputs-receipt.json')
    save(output/'campaign.json',{'at':runner.now(),'pid':os.getpid(),'worker':'0000000','provider':'azure-astra',
        'action_interface':'joint','cases':cases,'hdfs_root':HDFS,'harness_commit':HARNESS_COMMIT,
        'orchestration_commit':args.commit,'native_initial_state':True})
    results=[]
    with ThreadPoolExecutor(max_workers=len(cases)) as pool:
        pending={pool.submit(runner.run_case,args,task,layout,gpu,campaign=output,baseline=HARNESS,
                            harness_commit=HARNESS_COMMIT,provider='azure-astra',skill_revision='azure-missing-joint-suite'):(task,layout)
                 for task,layout,gpu in cases}
        while pending:
            for future in list(pending):
                if not future.done(): continue
                task,layout=pending.pop(future)
                try: result=future.result()
                except Exception: result={'task':task,'layout_index':layout,'status':'orchestration_or_durability_error','error':traceback.format_exc()}
                results.append(result); save(output/'results.json',results); print(json.dumps(result),flush=True)
            report(output)
            if pending: time.sleep(15)
    runner.BACKUPS.shutdown(wait=True)
    save(output/'completed.json',{'at':runner.now(),'cases':len(results),'results':results})
    report(output)
    finalize(root, output)


def finalize(root, output):
    """Audit actual executions, archive results, then notify; never infer goal completion."""
    assert os.environ['ARNOLD_WORKER_ID'] == '0000000'
    assert not (output/'completion-notification.json').exists(), 'Already finalized and notified'
    assert (output/'completed.json').exists()
    data=report(output)
    assert data['total']==data['finished']==32, 'Coverage is still incomplete'
    plan=json.loads((output/'coverage-plan.json').read_text())
    assert digest(TASK_FILE)==plan['tasks_sha256']
    assert {(r['task'],r['layout']) for r in data['rows']} == {(t,l) for t in task_names() for l in (20,21)}
    manifest=json.loads((output/'skills-manifest.json').read_text())
    def audit(row):
        folder=Path(row['folder'])
        if row['origin']=='historical_action_comparison':
            config_path=folder/f"config-{row['layout']}.json"
            config=json.loads(config_path.read_text())
            assert config['control_setting']=='joint' and digest(config_path)==row['config_sha256']
            assert json.loads((folder/f"seed-{row['layout']}/provenance.json").read_text())['control_setting']=='joint'
            return {'task':row['task'],'layout':row['layout'],'origin':row['origin'],
                    'provider_requirement':'historical provider exempt','native_sha256':digest(Path(row['native_result']))}
        config=json.loads((folder/'config.json').read_text())
        launch=json.loads((folder/'launch.json').read_text())
        assert config['task']==row['task'] and config['action_interface']==launch['action_interface']=='joint'
        assert launch['layout_index']==row['layout'] and launch['native_initial_state']
        result=json.loads((folder/'result.json').read_text())
        assert result['model_calls']>0, 'No model execution: '+folder.name
        if row['origin']!='new':
            assert digest(folder/'config.json')==row['config_sha256']
            return {'task':row['task'],'layout':row['layout'],'origin':row['origin'],
                    'provider':config['astra_provider'],'result_sha256':digest(folder/'result.json')}
        assert config['astra_provider']==launch['provider']=='azure-astra'
        case=Path(json.loads((folder/'status.json').read_text())['worker_case'])
        provenance=json.loads((case/'episode/provenance.json').read_text())
        assert provenance['action_interface']=='joint' and provenance['seeds']==[row['layout']]
        assert provenance['task']==row['task']
        tools=json.loads((case/'policy/model/native-tools.json').read_text())
        assert tools['provider']=='azure-astra' and tools['provider_allocation'] is None
        control=next(tool for tool in tools['tools'] if tool['name']=='control_arms')
        assert set(control['parameters']['$defs'])=={'JointArm'}
        assert control['parameters']['$defs']['JointArm']['properties']['control']['const']=='joint'
        requests=list((case/'policy/model').glob('*.request.json'))
        assert requests
        for path in requests:
            request=json.loads(path.read_text())
            assert request['provider']=='azure-astra' and request['base_url']==tools['base_url']
        actual=json.loads((case/'policy/skills.json').read_text())
        assert actual=={str(output/'skills'/name/'SKILL.md'):manifest['files'][name+'/SKILL.md'] for name in config['skill_names']}
        durability=json.loads((folder/'durability.json').read_text())
        assert durability['final'], 'Final archive incomplete: '+folder.name
        return {'task':row['task'],'layout':row['layout'],'origin':'new','provider':'azure-astra',
                'actual_azure_requests':len(requests),'skills_verified':True,'final_archive_verified':True,
                'result_sha256':digest(folder/'result.json')}
    with ThreadPoolExecutor(max_workers=32) as pool: checked=list(pool.map(audit,data['rows']))
    save(output/'completion-audit.json',{'at':runner.now(),'requirements_verified':True,'cases':checked,
         'scope':'Coverage of attempted cases; this does not claim all tasks succeeded or all episodes reached native termination.'})
    archive=SCRATCH/'final-report.tar.gz'
    with tarfile.open(archive,'w:gz') as target:
        for name in ['coverage-plan.json','coverage.json','coverage.md','completion-audit.json','completed.json',
                     'results.json','skills-manifest.json','campaign.json','source-inputs-receipt.json','tasks.md']:
            target.add(output/name,arcname=name)
        for row in data['rows']:
            folder=Path(row['folder']); prefix='cases/'+row['task']+f"-L{row['layout']}"
            names=['result.json','config.json','launch.json','durability.json'] if row['origin']!='historical_action_comparison' else [
                f"config-{row['layout']}.json",f"seed-{row['layout']}/runner-result.json",f"seed-{row['layout']}/provenance.json",f"policy-{row['layout']}/policy-result.json"]
            for name in names:
                path=folder/name
                if path.exists(): target.add(path,arcname=prefix+'/'+name)
        target.add(root/'AZURE-TASK-SUITE-PROGRESS.md',arcname='AZURE-TASK-SUITE-PROGRESS.md')
        target.add(root/'scripts/diagnose_suite.py',arcname='orchestration/diagnose_suite.py')
    runner.hdfs_archive(archive,HDFS+'/final-report.tar.gz',output/'final-report-receipt.json')
    success=sum(bool(row.get('native',{}).get('native_terminal') and row['native']['success']) for row in data['rows'] if row.get('native'))
    message=(f'【General-Pickup】16任务 L20/L21 仅joint测试覆盖已汇总：复用18场历史joint记录，新增14场均核验实际azure-astra请求。'
             f'共32场已结束，主表原生成功{success}场；其余原生未成功、主动停止和中断分别记录，不能合并解释为成功率。'
             '历史API及Harness版本可能不同。最终报告已校验归档HDFS：'+str(output/'coverage.md'))
    request=urllib.request.Request(os.environ['EVAL_COMPLETION_WEBHOOK'],data=json.dumps(
        {'msg_type':'text','content':{'text':message}}).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=30) as response: result=json.load(response)
    save(output/'completion-notification.json',{'at':runner.now(),'message':message,'response':result})
    assert result.get('code',result.get('StatusCode'))==0,result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['inventory','prepare','run','report','finalize'])
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args(); root=args.root.resolve(); output=root/'artifacts/diagnose-suite'
    if args.stage=='inventory': inventory(root,output)
    elif args.stage=='prepare': prepare(root,output)
    elif args.stage=='run': run(root,output)
    elif args.stage=='finalize': finalize(root,output)
    else: print(json.dumps({k:v for k,v in report(output).items() if k!='rows'}))


if __name__=='__main__': main()
