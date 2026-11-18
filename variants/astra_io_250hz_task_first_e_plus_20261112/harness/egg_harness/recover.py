"""Independent development continuation; never feeds diagnostics to policy."""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .freeze import digest


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--trace',type=Path,required=True);p.add_argument('--timeout',type=int,default=3600);p.add_argument('--finish',action='store_true');a=p.parse_args()
    held=json.loads((a.run/'held.json').read_text());episode=Path(held['episode'])
    if (episode/'exit-status.json').exists() or (episode/'result.json').exists():raise RuntimeError('Scene already exited; cannot resume')
    def control(op):
        key='developer-'+uuid.uuid4().hex
        path=episode/'commands'/(key+'.json');tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps({'id':key,'op':op}));tmp.replace(path)
        start=time.monotonic()
        while not (episode/'responses'/path.name).exists():
            if time.monotonic()-start>30:raise TimeoutError('Retained service did not respond')
            time.sleep(.2)
        return json.loads((episode/'responses'/path.name).read_text())
    before=control('observe')
    if a.finish:
        control('finish');return
    a.trace=a.trace.resolve();a.trace.parent.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parents[1];started=time.monotonic()
    config=json.loads(Path(held['config']).read_text())
    config['skills_root']=str(root/'skills')
    config['memory']=str(root/'memory/skills.json')
    config['public_task_requirements']=json.loads((root/'configs/public-tasks.json').read_text())[config['task']]
    requests=list(a.run.glob('*/model/*.request.json'))
    if not requests:raise ValueError('Retained development run has no policy request context')
    latest=max(requests,key=lambda path:path.stat().st_mtime_ns)
    from .continuation_context import load_context
    load_context(latest,episode,before['step'])
    config['continuation_request']=str(latest.resolve())
    config_path=a.trace.with_suffix('.config.json')
    if config_path.exists():raise FileExistsError(config_path)
    config_path.write_text(json.dumps(config,indent=2))
    record={'kind':'development_continuation','episode':str(episode),'start_step':before['step'],'human_interventions':0,'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'skill_hashes':digest(root/'skills')}
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    result_path=a.trace.with_suffix('.result.json')
    def persist():
        temporary=result_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(record,indent=2));temporary.replace(result_path)
    record.update(trace=str(a.trace),started_at=datetime.now(timezone.utc).isoformat(),status='starting');persist()
    process=subprocess.Popen([sys.executable,'-B','-m','egg_harness.skill_policy','--config',str(config_path),'--episode',str(episode),'--trace',str(a.trace)],cwd=root,env=env,stdout=a.trace.with_suffix('.log').open('w'),stderr=subprocess.STDOUT)
    record.update(status='running',recovery_pid=os.getpid(),policy_pid=process.pid);persist()
    try:
        process.wait(timeout=a.timeout);record['returncode']=process.returncode
    except subprocess.TimeoutExpired:
        record['timeout']=True;persist()
        process.terminate();process.wait(timeout=15);record['returncode']=process.returncode
    finally:
        record.update(seconds=time.monotonic()-started,finished_at=datetime.now(timezone.utc).isoformat(),status='policy_exited');persist()
        try:
            after=control('observe')
        except BaseException as error:
            record.update(status='observation_failed',finalization_error=repr(error));persist();raise
        record.update(end_step=after['step'],actions=after['step']-before['step'],native_terminal=after['ended']);persist()
        if after['ended']:
            verdict=json.loads((episode/'runner-result.json').read_text());record['success']=bool(verdict['native_terminal'] and verdict['success']);record['status']='native_end';persist();control('finish')
        else:
            record.update(success=False,status='held_for_development_recovery');persist();control('diagnostic')
        persist()
    print(json.dumps(record))

if __name__=='__main__':main()
