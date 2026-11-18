"""Offline posture and native joint tracking from public robot observations."""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from egg_harness.kinematics import Kinematics

p = argparse.ArgumentParser()
p.add_argument('episode', type=Path)
p.add_argument('--start', type=int, required=True)
p.add_argument('--end', type=int, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
if a.output.exists():
    raise FileExistsError(a.output)
m = Kinematics(json.loads((Path(__file__).resolve().parents[1] / 'configs/x5-kinematics.json').read_text()))
actions = {r['step']: r for r in map(json.loads, (a.episode/'actions.jsonl').read_text().splitlines())}
rows = []
for step in range(a.start, a.end + 1):
    state = json.loads((a.episode/'frames'/f'step-{step:04d}.json').read_text())['state']
    for arm in ('left', 'right'):
        q = np.asarray(state[arm+'_arm_joint_state'])
        pose = state[arm+'_ee_pose']
        base = m.observed_base(q, pose)
        transform = base.copy()
        points = []
        for origin, axis, angle in zip(m.origins, m.axes, q):
            rot = np.eye(4)
            rot[:3,:3] = Rotation.from_rotvec(axis*angle).as_matrix()
            transform = transform @ origin @ rot
            points.append(transform[:3,3].copy())
        upper, fore = points[2]-points[1], points[3]-points[2]
        tilt = np.degrees(np.arccos(np.clip(abs(upper[2])/np.linalg.norm(upper), 0, 1)))
        bend = np.degrees(np.arccos(np.clip(np.dot(upper,fore)/np.linalg.norm(upper)/np.linalg.norm(fore), -1, 1)))
        native = actions[step]['action'][arm+'_arm_joint_state']
        delta = np.asarray(native)-q
        jacobian = np.empty((6,6))
        reference = base @ m.forward(q)
        for j in range(6):
            shifted = q.copy(); shifted[j] += 1e-6
            predicted = base @ m.forward(shifted)
            jacobian[:3,j] = (predicted[:3,3]-reference[:3,3])/1e-6
            jacobian[3:,j] = .15*Rotation.from_matrix(reference[:3,:3].T@predicted[:3,:3]).as_rotvec()/1e-6
        singular = np.linalg.svd(jacobian, compute_uv=False)
        probes = []
        for axis in range(3):
            for sign in (-1,1):
                target = np.asarray(pose).copy(); target[axis] += sign*.005
                try:
                    m.solve_local(q,pose,target)
                    probes.append(True)
                except ValueError:
                    probes.append(False)
        rows.append(dict(step=step,arm=arm,command=actions[step]['command'],
            link2_tilt_from_vertical_deg=float(tilt),link2_link3_direction_change_deg=float(bend),
            joint_target_minus_measured_rad=delta.tolist(),joints=q.tolist(),joint_points_world=np.asarray(points).tolist(),
            weighted_jacobian_singular_values=singular.tolist(),five_mm_translation_probes_solved=probes))
report = dict(rows=rows, definitions={'link2':'joint2 origin to joint3 origin from public URDF and measured wrist base',
    'tilt':'0 vertical,90 horizontal, direction sign ignored',
    'tracking':'commanded joint target minus next measured joints; not a collision/contact diagnosis',
    'probes':'offline +/-5mm world x,y,z at fixed wrist orientation and unchanged0.15rad bound; not trajectories'},
    limits='Observed association only; arms, start states and paths differ. No hidden object state or physical replay.')
a.output.write_text(json.dumps(report,indent=2))
for arm in ('left','right'):
    selected=[r for r in rows if r['arm']==arm]
    print(json.dumps(dict(arm=arm,tilt_range=[min(r['link2_tilt_from_vertical_deg'] for r in selected),max(r['link2_tilt_from_vertical_deg'] for r in selected)],
        max_joint_tracking_rad=np.max(np.abs([r['joint_target_minus_measured_rad'] for r in selected]),axis=0).tolist(),
        probes_solved=sum(sum(r['five_mm_translation_probes_solved']) for r in selected),probes_total=6*len(selected))))
