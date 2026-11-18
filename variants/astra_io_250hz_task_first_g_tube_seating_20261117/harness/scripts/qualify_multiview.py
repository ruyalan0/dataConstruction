"""Verify persisted rendered marker measurements, then emit a fixed camera rig."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import label

from egg_harness.geometry import unproject
from egg_harness.kinematics import pose_matrix
from egg_harness.multiview import project, triangulate


def detect_markers(folder):
    rows=json.loads((folder/'rendered-markers.json').read_text())
    for index,row in enumerate(rows):
        for name in row['observations']:
            rgb=np.asarray(Image.open(folder/f'marker-{index:02d}-{name}.png')).astype(float)
            previous=f'marker-{index-1:02d}-{name}.png' if index else f'pose-2-{name}.png'
            before=np.asarray(Image.open(folder/previous)).astype(float)
            mask=(rgb[:,:,0]>140)&(rgb[:,:,2]>140)&(rgb[:,:,1]<.6*np.minimum(rgb[:,:,0],rgb[:,:,2]))&(np.max(rgb-before,axis=2)>60)
            regions,count=label(mask)
            components=[np.argwhere(regions==i) for i in range(1,count+1)]
            components=[c for c in components if len(c)>=4]
            if len(components)==1:
                row['observations'][name]={'pixel':(components[0].mean(axis=0)[::-1]+.5).tolist(),'area':len(components[0])}
            else:row['observations'][name]={'rejected_components':[len(c) for c in components]}
    (folder/'markers-difference-detection.json').write_text(json.dumps(rows,indent=2))
    return rows


def qualify(folder):
    captures=json.loads((folder/'camera-observations.json').read_text())
    rows=detect_markers(folder)
    rig={'schema_version':1,'status':'render_qualified','cameras':{},
         'convention':'OpenGL: camera looks along -Z, image v points down; public EE pose uses world wxyz.',
         'scope':'Fixed ARX X5 hardware calibration; no object positions or per-episode poses.',
         'qualification':'12 isolated rendered marker positions, three wrist postures; pixel detection, not Astra correspondence or grasp accuracy.'}
    metrics={}
    for name,first in captures[0]['cameras'].items():
        camera={'intrinsic':first['metadata_intrinsic'],'resolution':[640,480]}
        transform='world_from_camera' if name=='cam_head' else 'ee_from_camera'
        camera[transform]=first[transform]
        differences=[float(np.max(np.abs(np.array(c['cameras'][name][transform])-first[transform]))) for c in captures]
        if max(differences)>1e-5:raise ValueError('Camera mounting is not invariant across postures')
        errors=[];sdk_errors=[]
        for row in rows:
            found=row['observations'][name]
            if 'pixel' not in found:continue
            if name=='cam_head':t=np.array(camera[transform])
            else:
                arm='left' if name=='cam_left_wrist' else 'right'
                t=pose_matrix(row['state'][arm+'_ee_pose'])@np.array(camera[transform])
            errors.append(float(np.linalg.norm(project(row['world_point'],{**camera,'world_from_camera':t})-found['pixel'])))
            sdk_errors.append(float(np.linalg.norm(project(row['world_point'],row['cameras'][name])-found['pixel'])))
        if len(errors)<6 or max(errors)>1.:raise ValueError('Insufficient/incorrect rendered intrinsic or extrinsic qualification')
        rig['cameras'][name]=camera
        metrics[name]={'markers':len(errors),'max_reprojection_px':max(errors),'median_reprojection_px':float(np.median(errors)),
                       'sdk_intrinsic_max_error_px':max(sdk_errors),'mount_matrix_max_change':max(differences)}
    results=[]
    for index,row in enumerate(rows):
        names=[n for n,v in row['observations'].items() if 'pixel' in v]
        cameras=[]
        for name in names:
            c=rig['cameras'][name]
            if name=='cam_head':cameras.append(c)
            else:
                arm='left' if name=='cam_left_wrist' else 'right'
                cameras.append({**c,'world_from_camera':(pose_matrix(row['state'][arm+'_ee_pose'])@np.array(c['ee_from_camera'])).tolist()})
        record={'marker':index,'views':names}
        try:
            fit=triangulate([row['observations'][n]['pixel'] for n in names],cameras)
        except ValueError as error:
            record.update(status='rejected',reason=str(error))
        else:
            record.update(status='accepted',error_m=float(np.linalg.norm(np.array(fit['position'])-row['world_point'])),result=fit)
            if 'cam_head' in names:
                pixel=row['observations']['cam_head']['pixel'];c=rig['cameras']['cam_head']
                record['plane_z_077_error_m']=float(np.linalg.norm(unproject(pixel,.77,c)-row['world_point']))
                record['plane_correct_z_error_m']=float(np.linalg.norm(unproject(pixel,row['world_point'][2],c)-row['world_point']))
        results.append(record)
    errors=[r['error_m'] for r in results if r['status']=='accepted']
    if len(errors)<8 or max(errors)>.005:raise ValueError('Rendered triangulation qualification failed')
    report={'scope':'Rendered synthetic marker geometry, not task-level or Astra-selected correspondence accuracy',
            'camera_metrics':metrics,'markers':results,'accepted':len(errors),'attempted':len(rows),
            'error_m':{key:float(np.percentile(errors,value)) for key,value in [('median',50),('p90',90),('p95',95),('max',100)]}}
    rig['qualification_evidence_sha256']=hashlib.sha256((folder/'markers-difference-detection.json').read_bytes()).hexdigest()
    return rig,report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--capture',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    rig,report=qualify(a.capture)
    with a.output.open('x') as stream:json.dump(rig,stream,indent=2)
    (a.capture/'qualification-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:report[k] for k in ['accepted','attempted','error_m']}))
