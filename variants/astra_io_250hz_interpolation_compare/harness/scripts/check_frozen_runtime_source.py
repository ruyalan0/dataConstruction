"""Frozen audit rejects substituted or omitted external runtime provenance."""
import copy
import json
import tempfile
from pathlib import Path
from audit_frozen_batch import pinned_runtime_source
with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary);episode=root/'episode';episode.mkdir()
    config={'robodojo_root':str(root/'runtime'),'robodojo_revision':'a'*40,'robodojo_required_submodules':{'XPolicyLab':'b'*40}}
    record={'path':config['robodojo_root'],'status':'verified','clean':True,'commit':'a'*40,
        'required_submodules':{'XPolicyLab':{'path':str(root/'runtime/XPolicyLab'),'commit':'b'*40,'clean':True}}}
    target=episode/'runtime-source-provenance.json'
    target.write_text(json.dumps(record))
    assert pinned_runtime_source(episode,config)['verified']
    variants=[]
    for key,value in [('commit','c'*40),('clean',False),('status','unpinned'),('path',str(root/'different')),('required_submodules',{})]:
        changed=copy.deepcopy(record);changed[key]=value;variants.append(changed)
    changed=copy.deepcopy(record);changed['required_submodules']['XPolicyLab']['commit']='c'*40;variants.append(changed)
    for changed in variants:
        target.write_text(json.dumps(changed))
        try:pinned_runtime_source(episode,config)
        except ValueError:pass
        else:raise AssertionError('Substituted runtime provenance accepted')
    target.unlink()
    try:pinned_runtime_source(episode,config)
    except FileNotFoundError:pass
    else:raise AssertionError('Missing pinned provenance accepted')
    assert not pinned_runtime_source(episode,{})['verified']
print('Frozen source audit rejects mismatches and labels historical unpinned candidates explicitly')
