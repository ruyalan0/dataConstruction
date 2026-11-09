"""Read live worker handles and actual API/tool records without executing actions."""
import argparse
import json
import os
from pathlib import Path
from prepare_eight_eval import save
from run_eight_eval import now
from diagnose_suite import SCRATCH


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args(); root=args.root.resolve(); output=root/'artifacts/diagnose-suite'
    assert os.environ['ARNOLD_WORKER_ID']=='0000000'
    campaign=json.loads((output/'campaign.json').read_text()); pid=campaign['pid']
    if not (output/'completed.json').exists():
        state=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]
        assert state!='Z', 'Supervisor ended before completing its cases'
        assert str(root/'scripts/diagnose_suite.py').encode() in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    manifest=json.loads((output/'skills-manifest.json').read_text())
    checked=[]
    for task,layout,gpu in campaign['cases']:
        folder=output/'cases'/f'{task}-L{layout}-attempt1'
        record={'task':task,'layout':layout,'gpu':gpu,'phase':'starting','azure_requests_verified':0}
        case=SCRATCH/'runs'/folder.name
        config=json.loads((case/'config.json').read_text())
        assert config['astra_provider']=='azure-astra' and config['action_interface']=='joint'
        status=folder/'status.json'
        if status.exists():
            status=json.loads(status.read_text()); case=Path(status['worker_case'])
            record.update(phase='running',step=status['step'])
            provenance=json.loads((case/'episode/provenance.json').read_text())
            assert provenance['seeds']==[layout] and provenance['action_interface']=='joint'
            tools_path=case/'policy/model/native-tools.json'
            if tools_path.exists():
                tools=json.loads(tools_path.read_text())
                assert tools['provider']=='azure-astra' and tools['provider_allocation'] is None
                control=next(item for item in tools['tools'] if item['name']=='control_arms')
                assert set(control['parameters']['$defs'])=={'JointArm'}
                assert control['parameters']['$defs']['JointArm']['properties']['control']['const']=='joint'
                actual=json.loads((case/'policy/skills.json').read_text())
                assert actual=={str(output/'skills'/name/'SKILL.md'):manifest['files'][name+'/SKILL.md'] for name in config['skill_names']}
                # Recent files can still be written. Audit only fully settled request records.
                import time
                for path in (case/'policy/model').glob('*.request.json'):
                    if time.time()-path.stat().st_mtime<2: continue
                    request=json.loads(path.read_text())
                    assert request['provider']=='azure-astra' and request['base_url']==tools['base_url']
                    record['azure_requests_verified']+=1
                record['skills_and_joint_schema_verified']=True
        if (folder/'result.json').exists():
            record.update(phase='finished',result=json.loads((folder/'result.json').read_text()))
        checked.append(record)
    save(output/'live-audit.json',{'at':now(),'supervisor_pid':pid,'cases':checked})
    print(json.dumps({'at':now(),'supervisor_pid':pid,'cases':len(checked),
        'started_policies':sum(bool(c.get('skills_and_joint_schema_verified')) for c in checked),
        'verified_azure_requests':sum(c['azure_requests_verified'] for c in checked),
        'finished':sum(c['phase']=='finished' for c in checked)}))


if __name__=='__main__': main()
