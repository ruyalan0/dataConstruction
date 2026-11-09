"""Reject incomplete coverage, changed retries and broken archive chains."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory

import audit_five_layout_completion as audit
from prepare_eight_eval import save


def rejects(call):
    try: call()
    except (AssertionError,FileNotFoundError): return
    raise AssertionError('Invalid evidence was accepted')


tasks=audit.base.task_names()
rows=[{'task':task,'layout':layout,'phase':'finished','end_reason':'native_success'}
      for task in tasks for layout in range(20,25)]
data={'total':80,'finished':80,'rows':rows}
assert audit.complete_scope(data,tasks)=={'native_success':80}
unfinished=copy.deepcopy(data); unfinished['rows'][-1]['phase']='running'
rejects(lambda:audit.complete_scope(unfinished,tasks))
duplicate=copy.deepcopy(data); duplicate['rows'][-1]=duplicate['rows'][0]
rejects(lambda:audit.complete_scope(duplicate,tasks))

failure={'provider':'azure-astra','call':1,'attempt':1,'step':12,'request_sha256':'request',
         'scene_sha256':'scene','will_retry':True,'http_status':429,'type':'RateLimitError','delay_seconds':10}
success={'provider':'azure-astra','call':1,'attempt':2,'step':12,'request_sha256':'request','status':'succeeded_after_retry'}
assert audit.retry_records([failure,success])==1
for key,value in [('provider','zzz-astra'),('request_sha256','changed'),('step',13),('attempt',5)]:
    altered={**success,key:value}
    rejects(lambda:audit.retry_records([failure,altered]))
rejects(lambda:audit.retry_records([{**failure,'http_status':400},success]))
changed_scene={**failure,'attempt':2,'scene_sha256':'changed','delay_seconds':30}
rejects(lambda:audit.retry_records([failure,changed_scene]))

with TemporaryDirectory() as directory:
    folder=Path(directory)/'task-L22-attempt1'; hdfs='hdfs://storage.example.invalid/REDACTED'
    save(folder/'durability.json',{'final':True,'shards':2})
    receipt={'uri':hdfs+'/'+folder.name+'-shard-0000.tar.gz','verified_readback':True,'bytes':123,'sha256':'a'*64}
    save(folder/'shard-0000.json',receipt)
    rejects(lambda:audit.case_durability(folder,hdfs))
    save(folder/'shard-0001.json',{**receipt,'uri':hdfs+'/'+folder.name+'-shard-0001.tar.gz'})
    assert len(audit.case_durability(folder,hdfs))==2
    save(folder/'shard-0001.json',{**receipt,'uri':'hdfs://storage.example.invalid/REDACTED'})
    rejects(lambda:audit.case_durability(folder,hdfs))

print('PASS: incomplete/duplicate coverage, changed/provider-switching/invalid retries, missing/misrouted shards rejected')
