"""Low storage rejects the runner before creating output or launching work."""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from egg_harness.runner import main

with tempfile.TemporaryDirectory() as directory:
    output = Path(directory) / 'must-not-launch'
    args = ['runner', '--mode', 'development', '--config',
            'configs/bilateral-probe.json', '--output', str(output),
            '--seeds', '22', '--worker', '0000000', '--gpu', '0']
    with patch.object(sys, 'argv', args), \
         patch('egg_harness.runner.shutil.disk_usage', return_value=SimpleNamespace(free=3*1024**3)), \
         patch('egg_harness.runner.subprocess.run') as run, \
         patch('egg_harness.runner.subprocess.Popen') as popen:
        try:
            main()
        except RuntimeError as error:
            assert 'storage' in str(error).lower()
        else:
            raise AssertionError('Low-storage runner admitted')
        assert not output.exists()
        run.assert_not_called()
        popen.assert_not_called()
    for mode,seeds,hold in [('frozen',['22'],False),('development',['20','21'],False),('development',['22'],True)]:
        trial=['runner','--mode',mode,'--config','configs/bilateral-probe.json','--output',str(output),'--seeds',*seeds,'--worker','0000000','--gpu','0','--serial-development-storage']
        if hold:trial.append('--hold-on-failure')
        with patch.object(sys,'argv',trial):
            try:main()
            except ValueError as error:assert 'Serial storage' in str(error)
            else:raise AssertionError('Serial exception leaked to unsupported mode')
    args[2]='frozen'
    for mode,seeds,hold,serial in [('frozen',['22'],False,False),('development',['20','21'],False,False),('development',['22'],True,False),('development',['22'],False,True)]:
        trial=['runner','--mode',mode,'--config','configs/bilateral-probe.json','--output',str(output),'--seeds',*seeds,'--worker','0000000','--gpu','0','--parallel-development-storage']
        if hold:trial.append('--hold-on-failure')
        if serial:trial.append('--serial-development-storage')
        with patch.object(sys,'argv',trial):
            try:main()
            except ValueError as error:assert 'Parallel storage' in str(error)
            else:raise AssertionError('Parallel exception leaked to unsupported mode')
    parallel=['runner','--mode','development','--config','configs/bilateral-probe.json','--output',str(output),'--seeds','22','--worker','0000000','--gpu','0','--parallel-development-storage']
    with patch.object(sys,'argv',parallel),patch('egg_harness.runner.shutil.disk_usage',return_value=SimpleNamespace(free=5*1024**3-1)),patch('egg_harness.runner.subprocess.run') as run:
        try:main()
        except RuntimeError as error:assert 'storage' in str(error).lower()
        else:raise AssertionError('Parallel admission below5GiB')
        run.assert_not_called()
        assert not output.exists()
print('Low storage rejects before output creation, resource probes or simulator launch')
