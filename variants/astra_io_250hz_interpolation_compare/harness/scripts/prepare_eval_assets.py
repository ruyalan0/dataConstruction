"""Restore unmodified official layouts into an isolated runtime asset view."""
import argparse,hashlib,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True);p.add_argument('--source-repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--count',type=int,default=13);a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=False)
for item in a.runtime.iterdir():
 if item.name!='Eval_Layout':(a.output/item.name).symlink_to(item.resolve(),target_is_directory=item.is_dir())
revision=subprocess.check_output(['git','-C',str(a.source_repo),'rev-parse','HEAD'],text=True).strip()
records=[]
if not 1<=a.count<=65:raise ValueError("count must be1..65")
for index in range(a.count):
 relative=f'Assets/Eval_Layout/RoboDojo/arx_x5/0/fill_egg_holder_{index}.json'
 data=(a.source_repo/relative).read_bytes();blob=subprocess.check_output(['git','-C',str(a.source_repo),'show',f'{revision}:{relative}'])
 if blob.startswith(b'version https://git-lfs'):
  expected=next(line.split('sha256:')[1] for line in blob.decode().splitlines() if line.startswith('oid '))
  assert hashlib.sha256(data).hexdigest()==expected
 else:assert data==blob
 json.loads(data) # Validate format without exposing scene contents to the policy/model.
 target=a.output/relative.removeprefix('Assets/');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
 records.append({'index':index,'sha256':hashlib.sha256(data).hexdigest()})
(a.output/'layout-manifest.json').write_text(json.dumps({'revision':revision,'files':records,'unaltered':True},indent=2))
print('Restored',len(records),'unaltered official layouts; object poses not printed')
