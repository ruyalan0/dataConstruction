"""Development-only RGB-localized closure test; never a task-success evaluation."""
import argparse,json,time,traceback
from pathlib import Path
from .policy import Policy
p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--episode',required=True);p.add_argument('--trace',required=True);a=p.parse_args()
policy=Policy(json.loads(Path(a.config).read_text()),a.episode,a.trace);start=time.monotonic()
try:
 scene=policy.perceive();policy.close(scene['slots'])
 result={'status':'probe_completed','not_task_success':True}
except BaseException:
 result={'status':'probe_failed','traceback':traceback.format_exc(),'not_task_success':True}
 raise
finally:
 result.update(seconds=time.monotonic()-start,steps=policy.robot.obs['step'],model_calls=policy.vision.calls)
 (Path(a.trace)/'policy-result.json').write_text(json.dumps(result,indent=2))
 policy.robot.command('finish')
