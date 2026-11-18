"""CPU checks for scope, sequential launch gates, public inputs and GPU admission."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import five_layout_suite as suite
from prepare_eight_eval import save


def rejects(call):
    try: call()
    except (AssertionError,FileNotFoundError): return
    raise AssertionError('Invalid campaign state was accepted')


tasks=suite.base.task_names()
cases,projected=suite.allocate_cases(tasks,22,{i:[46068,5] for i in range(8)})
assert len(cases)==16 and len(set(t for t,_,_ in cases))==16
assert all(v==20005 for v in projected.values())
rejects(lambda:suite.allocate_cases(tasks,22,{0:[46068,35000]}))
cases,projected=suite.allocate_cases(tasks,23,{0:[46068,35000],**{i:[46068,0] for i in range(1,8)}})
assert all(g!=0 for _,_,g in cases) and all(v<=38068 for v in projected.values())

public=suite.public_requirements({'task':'example','url':'public-source','rows':[
    ['Instruction','Do the whole task'],['Comment','Use the specified orientation'],
    ['10','One object earns partial reward'],['100','All objects placed and robot home']]})
assert public['rows']==[['Instruction','Do the whole task'],['Comment','Use the specified orientation'],
                         ['Completion','All objects placed and robot home']]
rejects(lambda:suite.public_requirements({'rows':[['100','Missing instruction']]}))

with TemporaryDirectory() as temporary:
    root=Path(temporary); output=root/'artifacts/five-layout-suite'
    previous=root/'artifacts/diagnose-suite'
    rejects(lambda:suite.prior_round(root,output,22))
    records=[{'task':t,'layout':l,'phase':'finished','end_reason':'native_success',
              'native':{'score':100,'native_terminal':True,'success':True}} for t in tasks for l in (20,21)]
    save(previous/'completion-audit.json',{'requirements_verified':True,'cases':records})
    rejects(lambda:suite.prior_round(root,output,22))
    save(previous/'final-report-receipt.json',{'verified_readback':False})
    rejects(lambda:suite.prior_round(root,output,22))
    save(previous/'final-report-receipt.json',{'verified_readback':True})
    assert suite.prior_round(root,output,22)==previous
    invalid=[dict(r) for r in records]; invalid[-1]['layout']=0
    save(previous/'completion-audit.json',{'requirements_verified':True,'cases':invalid})
    rejects(lambda:suite.prior_round(root,output,22))
    save(previous/'coverage.json',{'rows':records})
    data=suite.report(root,output)
    assert data['total']==80 and data['finished']==32
    folder=output/f'L22/cases/{tasks[0]}-L22-attempt1'
    save(folder/'status.json',{'step':13})
    data=suite.report(root,output)
    assert data['finished']==32 and sum(r['phase']=='running' for r in data['rows'])==1
    save(folder/'result.json',{'status':'execution_error','model_calls':2})
    data=suite.report(root,output)
    assert data['finished']==33
    item=next(r for r in data['rows'] if r['task']==tasks[0] and r['layout']==22)
    assert item['end_reason']=='execution_error' and item['native'] is None
    invalid_native={'score':None,'raw_native_score':0.0,'native_terminal':True,
                    'success':False,'valid_sample':False,'unstable':True}
    save(folder/'result.json',{'status':'policy_completed','model_calls':2,'native':invalid_native})
    data=suite.report(root,output)
    item=next(r for r in data['rows'] if r['task']==tasks[0] and r['layout']==22)
    assert data['finished']==33 and item['end_reason']=='invalid_native_sample'
    assert item['native']==invalid_native, 'Invalid raw evidence was changed'
    assert 'unscored; invalid_native_sample' in (output/'coverage.md').read_text()
    rejects(lambda:suite.outcome_text({**invalid_native,'valid_sample':True,'unstable':False},'native_unsuccessful_end'))
    assert suite.outcome_text({'score':50,'native_terminal':False},'astra_stopped')=='50 prefix; astra_stopped'
    assert suite.outcome_text({'score':100,'native_terminal':True},'native_success')=='100; native_success'
    rejects(lambda:suite.prior_round(root,output,23))

print('PASS: 80-case scope, ordered gates, unscored invalid samples, distinct errors, public inputs and GPU admission')
