"""Independent episode runner. Native verdict stays outside policy observations."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from .freeze import digest, verify
from .action_control import action_interface
from .storage import EpisodeStorage,StoragePressure


def wait_policy(policy,timeout,storage):
    started=time.monotonic()
    while policy.poll() is None:
        storage.check()
        remaining=timeout-(time.monotonic()-started)
        if remaining<=0:raise subprocess.TimeoutExpired(policy.args,timeout)
        try:policy.wait(timeout=min(1,remaining))
        except subprocess.TimeoutExpired:continue


def simulator_command(command, log):
    import shlex
    # Keep simulator output independent of the SSH launcher lifetime during recovery.
    return "unset LD_PRELOAD; exec " + shlex.join(command) + " > " + shlex.quote(str(log)) + " 2>&1"


def resource_audit_command(source, output):
    import shlex
    command = ['python', str(source/'scripts/worker_driver.py'), '--record',
               str(output/'resource-driver.json'), '--', 'python',
               str(source/'scripts/resources.py')]
    return 'unset LD_PRELOAD; exec ' + shlex.join(command)


def runtime_source_arguments(config):
    import re
    revision = config.get('robodojo_revision')
    submodules = config.get('robodojo_required_submodules', {})
    if not isinstance(submodules, dict):
        raise ValueError('Required runtime submodules must be a mapping')
    if revision is None:
        if submodules:
            raise ValueError('Required submodules need a pinned runtime revision')
        return []
    if not isinstance(revision, str) or re.fullmatch(r'[0-9a-f]{40}', revision) is None:
        raise ValueError('Runtime revision must be a full lowercase commit SHA')
    arguments = ['--robodojo-revision', revision]
    for path, sha in sorted(submodules.items()):
        if not isinstance(path, str) or not isinstance(sha, str):
            raise ValueError('Runtime submodule paths and revisions must be strings')
        arguments.extend(['--required-submodule', path + '=' + sha])
    return arguments


def completed_bilateral_probe(config, mode, trace):
    if config.get('probe') != 'bilateral':
        return False
    if mode != 'development':
        raise ValueError('Bilateral probe is development-only')
    record = json.loads((trace / 'policy-result.json').read_text())
    if record['status'] != 'probe_completed' or record['not_task_success'] is not True:
        raise ValueError('Missing completed development probe evidence')
    return True


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=['development','frozen'],required=True)
    p.add_argument('--bundle',type=Path)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seeds',type=int,nargs='+',required=True)
    p.add_argument('--worker',required=True);p.add_argument('--gpu',type=int,required=True)
    p.add_argument('--hold-on-failure',action='store_true')
    p.add_argument('--action-interface',choices=['joint','tcp','both'],help='Override development interface; default is joint when config omits it')
    p.add_argument('--development-case-reason',help='Retest one exposed case in development and record why; never an unseen evaluation')
    p.add_argument('--episode-timeout',type=int,default=3600)
    p.add_argument('--replay-episode',type=Path,help='Development-only recorded native-action prefix source')
    p.add_argument('--replay-step',type=int)
    p.add_argument('--serial-development-storage',action='store_true',help='One development episode with 3GiB admission, allocated cleanup reserve and latched pressure stop')
    p.add_argument('--parallel-development-storage',action='store_true',help='One development episode with 5GiB admission for an explicitly coordinated parallel development campaign')
    a=p.parse_args();root=Path(__file__).resolve().parents[1];config=json.loads(a.config.read_text())
    if a.action_interface is not None:
        if a.mode=='frozen' and a.action_interface!=action_interface(config):
            raise ValueError('Freeze the selected action interface in the bundle before evaluation')
        config['action_interface']=a.action_interface
    config['action_interface']=action_interface(config)
    if config['action_interface']!='tcp' and (config.get('controller')!='skills' or config.get('probe') is not None):
        raise ValueError('Joint/Both interfaces require the skills controller; legacy probes require explicit TCP')
    replaying=a.replay_episode is not None or a.replay_step is not None
    if replaying and (a.mode!='development' or len(a.seeds)!=1 or a.replay_episode is None or a.replay_step is None or a.replay_step<1 or not a.development_case_reason):
        raise ValueError('Replay requires one development case, source episode, positive step and explicit reason')
    runtime_arguments = runtime_source_arguments(config)
    joint_control=config.get('joint_control',False) if config['action_interface']=='both' else False
    if type(joint_control) is not bool or (joint_control and (a.mode!='development' or config.get('public_posture_feedback') is not True)):
        raise ValueError('Joint control requires development qualification and public posture feedback')
    direct_posture=config.get('direct_posture_action',False) if config['action_interface']=='both' else False
    recovery=config.get('posture_recovery_program',False) if config['action_interface']=='both' else False
    if type(recovery) is not bool or (recovery and (not direct_posture or a.mode!='development')):
        raise ValueError('Recovery program requires direct posture and development qualification')
    if type(direct_posture) is not bool or (direct_posture and config.get('public_posture_feedback') is not True):
        raise ValueError('Direct posture requires boolean enablement and public posture feedback')
    if a.serial_development_storage and (a.mode!='development' or len(a.seeds)!=1 or a.hold_on_failure):
        raise ValueError('Serial storage admission requires one development episode without hold')
    if a.parallel_development_storage and (a.serial_development_storage or a.mode!='development' or len(a.seeds)!=1 or a.hold_on_failure):
        raise ValueError('Parallel storage admission requires one development episode without hold or serial override')
    if config.get('probe') == 'bilateral' and a.mode != 'development':
        raise ValueError('Bilateral probe is development-only')
    if type(config.get('contract_local_ik', False)) is not bool:
        raise ValueError('contract_local_ik must be boolean')
    if config.get('contract_local_ik', False) and config.get('motion_op') != 'move_local':
        raise ValueError('Cartesian contraction requires local motion')
    if a.mode=='frozen' and a.bundle is None:raise ValueError('Frozen mode requires --bundle')
    source=a.bundle.resolve() if a.bundle else root
    if a.bundle:verify(source)
    curriculum=json.loads((source/'configs/curriculum.json').read_text())
    from .layout_split import heldout_layouts
    allowed=curriculum['development_layouts'] if a.mode=='development' else heldout_layouts(curriculum,config['task'])
    case_reason=a.development_case_reason
    if case_reason is not None and (a.mode!='development' or len(a.seeds)!=1 or not case_reason.strip() or a.seeds[0]<0):
        raise ValueError('Case retest requires one development layout and a nonempty reason')
    if len(a.seeds)!=len(set(a.seeds)) or (case_reason is None and not set(a.seeds)<=set(allowed)):raise ValueError('Layout split violation')
    if a.hold_on_failure and (a.mode!='development' or len(a.seeds)!=1):raise ValueError('Hold requires one development episode')
    # A selected bundle can also pin development runs while skills evolve elsewhere.
    if a.mode=='frozen':
        manifest=verify(source)
        config_path=a.config.resolve()
        if not config_path.is_relative_to(source/'configs'):raise ValueError('Frozen config must be selected from bundle/configs')
        config_key=str(config_path.relative_to(source))
        if config_key not in manifest['files']:raise ValueError('Frozen config is not in manifest')
    a.output=a.output.resolve()
    storage_parent=a.output.parent
    while not storage_parent.exists():
        storage_parent=storage_parent.parent
    storage_free=shutil.disk_usage(storage_parent).free
    storage_required=(5 if a.parallel_development_storage else 3 if a.serial_development_storage else 8)*1024**3
    if storage_free < storage_required:
        raise RuntimeError(f'Insufficient output storage: {storage_free} free bytes; require {storage_required}. No episode launched.')
    a.output.mkdir(parents=True,exist_ok=False)
    launch_record={"task":config["task"],"mode":a.mode,"layout_indices":a.seeds,
        "worker":a.worker,"gpu":a.gpu,"runner_pid":os.getpid(),
        "started_at":datetime.now(timezone.utc).isoformat(),"source":str(source),"worker_launch_cwd":str(source),
        "config":config,"storage_admission":{"free_bytes":storage_free,"required_bytes":storage_required,"path":str(storage_parent),"scope":"Launch-time check only; does not reserve space against concurrent writers"},"source_commit":verify(source)["commit"] if a.bundle else subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()}
    if case_reason is not None:
        launch_record['development_case']={'reason':case_reason,'unseen_evaluation':False}
    (a.output/"launch-record.json").write_text(json.dumps(launch_record,indent=2))
    subprocess.run([sys.executable,str(source/'scripts/resources.py'),'--cpu-only'],cwd=source,stdout=(a.output/'local-resources.json').open('w'),check=True)
    subprocess.run(['adcli','--json','power-pool','worker','list'],cwd=source,stdout=(a.output/'workers.json').open('w'),check=True)
    workers=json.loads((a.output/'workers.json').read_text())['data']['Workers']
    worker=next(w for w in workers if w['WorkerSid']=='wkr_'+a.worker)
    if worker['Status']!='Running' or not 0<=a.gpu<worker['GpuCount']:raise ValueError('Worker/GPU unavailable')
    # CPU and GPU quotas are recorded from the actual worker before simulation.
    audit=resource_audit_command(source, a.output)
    # mlx mirrors its local process cwd on the worker before executing the command.
    # A clean /tmp checkout is local-only; the selected source bundle is shared.
    subprocess.run(['mlx','worker','login',a.worker,'--','bash','-lc',audit],cwd=source,stdout=(a.output/'resources.txt').open('w'),check=True)
    local=json.loads((a.output/'local-resources.json').read_text())
    remote=next(json.loads(line) for line in (a.output/'resources.txt').read_text().splitlines() if line.startswith('{'))
    if remote['cpu']<=local['cpu'] or remote['memory_limit']<=local['memory_limit'] or not remote['nas']:raise RuntimeError('Worker does not satisfy resource placement')
    results=[]
    for seed in a.seeds:
        episode=a.output/f'seed-{seed}';trace=a.output/f'policy-{seed}'
        started=time.monotonic();sim=None;policy=None;result={'seed_index':seed,'task':config['task'],'mode':a.mode,'success':False,'human_interventions':0,'started_at':datetime.now(timezone.utc).isoformat()}
        storage=EpisodeStorage(a.output)
        try:
            storage.allocate()
            env=dict(os.environ);env['PYTHONDONTWRITEBYTECODE']='1';env['RSI_STORAGE_RESERVE_DIR']=str(a.output)
            command=['env',f'CUDA_VISIBLE_DEVICES={a.gpu}','OMNI_KIT_ACCEPT_EULA=YES','PYTHONNOUSERSITE=1','OMP_NUM_THREADS=1','OPENBLAS_NUM_THREADS=1',config['robot_runtime'],str(source/'scripts/serve.py'),'--output',str(episode),'--robodojo-root',config['robodojo_root'],'--assets',config['assets'],'--object-assets',config['object_assets'],'--rtx-driver-version-workaround','--seed-index',str(seed),'--run-mode',a.mode]
            command.extend(['--task',config['task'],'--action-interface',config['action_interface']])
            if replaying:
                command.extend(['--replay-episode',str(a.replay_episode.resolve()),'--replay-step',str(a.replay_step)])
                result['development_replay']={'episode':str(a.replay_episode.resolve()),'step':a.replay_step,'scope':'Recorded action reconstruction, not exact physics snapshot restoration'}
            command.extend(runtime_arguments)
            command.insert(1,'RSI_STORAGE_RESERVE_DIR='+str(a.output))
            # Resolve driver libraries on the worker; allocated workers can run different kernels.
            command = ["python", str(source/"scripts/worker_driver.py"), "--record",
                       str(a.output/f"driver-{seed}.json"), "--", *command]
            remote=simulator_command(command, a.output/f"worker-sim-{seed}.log")
            sim=subprocess.Popen(['mlx','worker','login',a.worker,'--','bash','-lc',remote],cwd=source,stdout=(a.output/f'sim-{seed}.log').open('w'),stderr=subprocess.STDOUT)
            while not (episode/'ready.json').exists():
                storage.check()
                if sim.poll() is not None: raise RuntimeError('Simulator exited during startup')
                if (episode/'error.txt').exists():raise RuntimeError('Simulator startup error')
                if time.monotonic()-started>(1800 if replaying else 300):raise TimeoutError('Simulator startup/replay')
                time.sleep(1)
            episode_config=dict(config)
            episode_config['public_task_requirements']=json.loads((source/'configs/public-tasks.json').read_text())[config['task']]
            episode_config['memory']=str(source/'memory/skills.json')
            episode_config['skills_root']=str(source/'skills')
            cfg=a.output/f'config-{seed}.json';cfg.write_text(json.dumps(episode_config))
            files=digest(source) if a.bundle else {str(f.relative_to(source)):__import__('hashlib').sha256(f.read_bytes()).hexdigest() for folder in ['egg_harness','memory','scripts','skills','configs'] for f in (source/folder).rglob('*') if f.is_file() and '__pycache__' not in f.parts}
            (a.output/f'source-{seed}.json').write_text(json.dumps({'source':str(source),'files':files},indent=2))
            module='egg_harness.closure_probe' if a.mode=='development' and config.get('probe')=='closure' else ('egg_harness.skill_policy' if config.get('controller')=='skills' else 'egg_harness.policy')
            if config.get('probe') == 'bilateral':
                if a.mode != 'development':
                    raise ValueError('Bilateral probe is development-only')
                module = 'egg_harness.bilateral_probe'
            policy=subprocess.Popen([sys.executable,'-B','-m',module,'--config',str(cfg),'--episode',str(episode),'--trace',str(trace)],cwd=source,env=env,stdout=(a.output/f'policy-{seed}.log').open('w'),stderr=subprocess.STDOUT)
            wait_policy(policy,a.episode_timeout,storage)
            if policy.returncode:raise RuntimeError(f'Policy exit {policy.returncode}')
            if completed_bilateral_probe(config, a.mode, trace):
                result.update(status='development_probe_completed', not_task_success=True)
                continue
            verdict=episode/'runner-result.json'
            if not verdict.exists():raise RuntimeError('No native terminal verdict')
            native=json.loads(verdict.read_text())
            result.update(native);result['success']=bool(native['native_terminal'] and native['success']);result['status']='native_end'
        except BaseException:
            result.update(status='error_or_timeout',error=traceback.format_exc())
        finally:
            storage.release()
            if policy and policy.poll() is None:policy.terminate();policy.wait(timeout=15)
            held = a.hold_on_failure and not (a.output/'storage-pressure.json').exists() and result.get('status')=='error_or_timeout' and (episode/'ready.json').exists() and sim is not None and sim.poll() is None
            if held:
                (episode/'developer-snapshot.request').touch()
                result['status']='held_for_development_recovery'
                (a.output/'held.json').write_text(json.dumps({'episode':str(episode),'config':str(cfg),'sim_launcher_pid':sim.pid,'reason':result['error']}))
            if not held and (episode/'commands').exists() and not (episode/'result.json').exists():
                path=episode/'commands'/'zz-runner-finish.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps({'id':'zz-runner-finish','op':'finish'}));tmp.replace(path)
            if sim and not held:
                try:sim.wait(timeout=45)
                except subprocess.TimeoutExpired:sim.terminate();result['cleanup_timeout']=True
            if (episode/'runner-result.json').exists():
                result['official_observation']=json.loads((episode/'runner-result.json').read_text())
            result['wall_seconds']=time.monotonic()-started
            result['finished_at']=datetime.now(timezone.utc).isoformat()
            if (a.output/'storage-pressure.json').exists():result['storage_pressure']=json.loads((a.output/'storage-pressure.json').read_text())
            if (trace/'policy-result.json').exists():result['policy']=json.loads((trace/'policy-result.json').read_text())
            result['action_count']=sum(1 for _ in (episode/'actions.jsonl').open()) if (episode/'actions.jsonl').exists() else 0
            results.append(result);(a.output/'results.json').write_text(json.dumps(results,indent=2))
            if a.bundle:verify(source)
        if (a.output/'storage-pressure.json').exists():
            break
    print(json.dumps({'successes':sum(x['success'] for x in results),'episodes':len(results),'output':str(a.output)}))

if __name__=='__main__':main()
