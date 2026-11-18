"""Exposed cases may run as development, never as frozen or unlabeled exceptions."""
import sys,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from egg_harness.runner import main
with tempfile.TemporaryDirectory() as directory:
 output=Path(directory)/'never-created'
 base=['runner','--mode','development','--config','configs/solve-equation-posture-development.json','--output',str(output),'--seeds','23','--worker','0000000','--gpu','0']
 for reason,expected in [(None,ValueError),('',ValueError),('Cross-side handoff failure regression',RuntimeError)]:
  args=base+(['--development-case-reason',reason] if reason is not None else [])
  with patch.object(sys,'argv',args),patch('egg_harness.runner.shutil.disk_usage',return_value=SimpleNamespace(free=0)),patch('egg_harness.runner.subprocess.run') as run:
   try:main()
   except expected as error:
    if expected is RuntimeError:assert 'storage' in str(error).lower()
   else:raise AssertionError('Expected admission boundary')
   run.assert_not_called();assert not output.exists()
 args=base.copy();args[2]='frozen';args+=['--development-case-reason','exposed case']
 with patch.object(sys,'argv',args):
  try:main()
  except ValueError:pass
  else:raise AssertionError('Frozen exception accepted')
print('Seen-case development reaches storage gate; unlabeled, blank and frozen exceptions rejected')
