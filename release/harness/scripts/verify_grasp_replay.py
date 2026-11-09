"""Offline RGB-only replay: audit a claimed grasp with two-view temporal evidence."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from egg_harness.vision import Vision
p=argparse.ArgumentParser();p.add_argument('--episode',type=Path,required=True);p.add_argument('--steps',type=int,nargs=2,required=True);p.add_argument('--arm',choices=['left','right'],required=True);p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=False);images={};states=[]
for label,step in zip(('before','after'),a.steps):
 obs=json.loads((a.episode/'frames'/f'step-{step:04d}.json').read_text())
 states.append({'label':label,'step':step,'state':obs['state']})
 for camera in ('cam_head','cam_'+a.arm+'_wrist'):images[label+'_'+camera]=obs['cameras'][camera]
question='Compare BEFORE and AFTER head and wrist RGB with actual robot motion. Determine whether an egg moved rigidly with the gripper or stayed in the world/background. Egg appearing between fingers in a moving camera is insufficient. Return JSON {held_after:true|false|null,confidence:0..1,evidence:[string],contradictions:[string],next_disambiguating_observation:string}. Use null when occlusion or image ambiguity prevents confirmation. '+json.dumps(states)
model=Vision(json.loads(a.config.read_text()),a.output/'model');result=model.ask(question,images);(a.output/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
