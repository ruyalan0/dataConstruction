import json,tempfile
from pathlib import Path
from egg_harness.continuation_context import load_context
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory);episode=root/'run/seed-23';episode.mkdir(parents=True);path=root/'run/policy-23/model/0005.request.json';path.parent.mkdir(parents=True)
 context={'step':37,'memory':'Left holds operator; next place on pad','previous_result':{'wrist_error_m':.001},'recent_executed_segments':[{'end_step':37}],'stalled_segments_this_controller':[],'hidden_extra':'must not propagate'}
 path.write_text(json.dumps({'question':json.dumps(context)}))
 restored=load_context(path,episode,37)
 assert restored['memory']==context['memory'] and restored['feedback']==context['previous_result'] and 'hidden_extra' not in restored
 for badpath,step in [(path,38),(path,0),(path,True)]:
  try:load_context(badpath,episode,step)
  except ValueError:pass
  else:raise AssertionError('Mismatched scene accepted')
 try:load_context(path,root/'other/seed-23',37)
 except ValueError:pass
 else:raise AssertionError('Other run accepted')
print('Same-step public memory and feedback restored; mismatched scene and run rejected')
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory);episode=root/'run/seed-20';frames=episode/'frames';frames.mkdir(parents=True);path=root/'run/policy-20/model/0002.request.json';path.parent.mkdir(parents=True)
 cameras={}
 for name in ('cam_head','cam_left_wrist','cam_right_wrist'):
  image=frames/(name+'.jpg');image.write_bytes(b'image');cameras[name]=str(image)
 state={'public':'joint state'};(frames/'step-0002.json').write_text(json.dumps({'step':2,'state':state,'cameras':cameras}))
 context={'step':5,'memory':'holding','recent_executed_segments':[],'stalled_segments_this_controller':[],'previous_observation':{'step':2,'state':state,'active_arm':['left','right']}}
 path.write_text(json.dumps({'question':json.dumps(context)}));restored=load_context(path,episode,5)
 assert restored['previous']['cameras']==cameras and restored['previous']['arm']==['left','right']
 context['previous_observation']['state']={'tampered':True};path.write_text(json.dumps({'question':json.dumps(context)}))
 try:load_context(path,episode,5)
 except ValueError:pass
 else:raise AssertionError('Mismatched prior frame accepted')
print('Prior paired RGB restored from same episode; altered public frame rejected')
