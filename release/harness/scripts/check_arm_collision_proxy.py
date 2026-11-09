"""Read-only public collision-sphere screening; not PhysX contact evidence."""
import argparse
import json
from pathlib import Path
import itertools
import numpy as np
import yaml
from scipy.spatial.transform import Rotation
from egg_harness.kinematics import Kinematics

p=argparse.ArgumentParser()
p.add_argument('episode',type=Path)
p.add_argument('--spheres',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if a.output.exists():raise FileExistsError(a.output)
m=Kinematics(json.loads((Path(__file__).resolve().parents[1]/'configs/x5-kinematics.json').read_text()))
k=yaml.safe_load(a.spheres.read_text())['kinematics']
links=['base_link']+[f'link{i}' for i in range(1,7)]
rows=[]
for step in (993,999,1002,1005,1008,1011,1027,1031,1034,1040):
    state=json.loads((a.episode/'frames'/f'step-{step:04d}.json').read_text())['state']
    worlds={}
    for arm in ('left','right'):
        q=np.array(state[arm+'_arm_joint_state'])
        T=m.observed_base(q,state[arm+'_ee_pose']);frames=[T.copy()]
        for origin,axis,angle in zip(m.origins,m.axes,q):
            R=np.eye(4);R[:3,:3]=Rotation.from_rotvec(axis*angle).as_matrix();T=T@origin@R;frames.append(T.copy())
        worlds[arm]={}
        for link,T in zip(links,frames):
            worlds[arm][link]=[(T[:3,:3]@np.asarray(s['center'])+T[:3,3],s['radius']) for s in k['collision_spheres'][link]]
    pairs=[]
    for arm in worlds:
        for first,second in itertools.combinations(links,2):
            if second in k['self_collision_ignore'].get(first,[]) or first in k['self_collision_ignore'].get(second,[]):continue
            gap=min(float(np.linalg.norm(x-y)-r-s) for x,r in worlds[arm][first] for y,s in worlds[arm][second])
            buffered=gap-k['self_collision_buffer'][first]-k['self_collision_buffer'][second]
            pairs.append(dict(kind='self',arm=arm,first=first,second=second,raw_gap_m=gap,buffered_gap_m=buffered))
    for first,second in itertools.product(links,repeat=2):
        gap=min(float(np.linalg.norm(x-y)-r-s) for x,r in worlds['left'][first] for y,s in worlds['right'][second])
        pairs.append(dict(kind='inter_arm',first=first,second=second,raw_gap_m=gap))
    selected={arm:sorted([r for r in pairs if r.get('arm')==arm],key=lambda r:r['buffered_gap_m'])[:3] for arm in ('left','right')}
    selected['inter_arm']=sorted([r for r in pairs if r['kind']=='inter_arm'],key=lambda r:r['raw_gap_m'])[:3]
    rows.append(dict(step=step,closest=selected))
report=dict(rows=rows,source=str(a.spheres),scope='Public sphere approximation for base/link1..6 only, excludes gripper/cameras/environment; buffers copied from public cuRobo config. Negative gap is proxy overlap, not confirmed physical collision.')
a.output.write_text(json.dumps(report,indent=2))
for row in rows:print(json.dumps(dict(step=row['step'],closest={key:value[0] for key,value in row['closest'].items()})))
