"""Paid native tool roundtrip with Astra-selected pixels from real rendered RGB."""
import argparse
import json
from pathlib import Path

import numpy as np

from egg_harness.multiview import MultiViewGeometry
from egg_harness.native_responses import NativeToolVision
from egg_harness.usage import model_usage


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--provider',choices=['azure-astra','zzz-astra','zzy-astra'],required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    config=json.loads(Path('configs/classify-objects-multijoint-development.json').read_text())
    config.update(astra_provider=args.provider,reasoning_effort='xhigh',max_model_calls=2)
    geometry=MultiViewGeometry()
    row=json.loads((args.capture/'rendered-markers.json').read_text())[0]
    names=['cam_head','cam_left_wrist']
    images={name:str(args.capture/f'marker-00-{name}.png') for name in names}
    obs={'step':0,'state':row['state'],'cameras':images,'camera_calibration_id':geometry.calibration_id}
    model=NativeToolVision(config,args.output/'model')
    try:
        question={'step':0,'state':obs['state'],'instruction':'Camera protocol test. Each supplied current RGB image contains one small bright magenta marker. Locate its visible center in BOTH images in raw 640x480 pixel coordinates and call triangulate_point for that common marker with step=0. Infer pixels from the images; no motion. Use cam_head and cam_left_wrist.'}
        decision=model.ask(json.dumps(question),images,system='Use triangulate_point for the indicated common marker. Other tools and robot motion are unnecessary.')
        if set(decision['action'])!={'triangulate'}:raise ValueError('Expected native triangulate_point')
        result=geometry.locate(decision['action']['triangulate'],obs)
        (args.output/'triangulation.json').write_text(json.dumps(result,indent=2))
        model.observe({'triangulation':result},0,False)
        decision=model.ask(json.dumps({'step':0,'instruction':'Finish by calling stop_episode with reason MULTIVIEW_OK and memory containing ONLY a JSON array of the three returned position coordinates rounded to six decimals. Read the preceding triangulate_point output.'}),{},system='Read the actual tool result and stop the protocol test.')
        if decision['action']!={'stop':True,'reason':'MULTIVIEW_OK'}:raise ValueError('Expected protocol completion')
        if json.loads(decision['memory'])!=[round(x,6) for x in result['position']]:raise ValueError('Tool result was not consumed')
        model.observe({'stopped':'synthetic_protocol_test','native_steps_executed':0},0,True)
        record={'status':'passed','provider':args.provider,'tool':'triangulate_point','model_calls':model.calls,
                'pixel_source':'Astra-selected from rendered RGB; coordinates and ground truth were not supplied',
                'position_error_m':float(np.linalg.norm(np.array(result['position'])-row['world_point'])),
                'usage':model_usage(args.output/'model'),'native_steps':0}
        (args.output/'result.json').write_text(json.dumps(record,indent=2));print(json.dumps(record))
    except BaseException as error:
        model.fail_pending(error,0)
        (args.output/'result.json').write_text(json.dumps({'status':'failed','error':str(error),'usage':model_usage(args.output/'model')},indent=2))
        raise
    finally:model.client.close()


if __name__=='__main__':main()
