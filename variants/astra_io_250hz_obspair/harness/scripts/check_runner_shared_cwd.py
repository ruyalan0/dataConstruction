"""Exercise runner.main remote audit and simulator launch from an unrelated cwd."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from egg_harness import runner

with tempfile.TemporaryDirectory(prefix='runner-local-checkout-') as directory:
    local = Path(directory); source = local / 'shared-nas-bundle'; source.mkdir()
    (source / 'configs').mkdir()
    (source / 'configs/curriculum.json').write_text(json.dumps({'development_layouts': [21]}))
    (source / 'configs/public-tasks.json').write_text(json.dumps({'classify_objects': {}}))
    config = {'controller': 'skills', 'task': 'classify_objects', 'robot_runtime': '/remote/python', 'robodojo_root': '/shared/robot', 'assets': '/shared/assets', 'object_assets': '/shared/objects'}
    config_path = source / 'configs/test.json'; config_path.write_text(json.dumps(config))
    output = local / 'result'; remote_calls = []
    class Storage:
        def __init__(self, path): pass
        def allocate(self): pass
        def check(self): pass
        def release(self): pass
    def run(args, **kwargs):
        assert kwargs.get('cwd') == source, 'Resource subprocess did not use selected source cwd'
        if args[0] == 'mlx':
            remote_calls.append(('audit', kwargs.get('cwd')))
            assert kwargs.get('cwd') == source, 'mlx resource check inherited local checkout cwd; worker chdir fails before remote command'
            kwargs['stdout'].write(json.dumps({'cpu': 88, 'memory_limit': 460, 'nas': True}) + chr(10)); kwargs['stdout'].flush()
        elif args[0] == 'adcli':
            kwargs['stdout'].write(json.dumps({'data': {'Workers': [{'WorkerSid': 'wkr_test', 'Status': 'Running', 'GpuCount': 4}]}})); kwargs['stdout'].flush()
        else:
            kwargs['stdout'].write(json.dumps({'cpu': 8, 'memory_limit': 32})); kwargs['stdout'].flush()
        return SimpleNamespace(returncode=0)
    def popen(args, **kwargs):
        if args[0] == 'mlx':
            remote_calls.append(('simulator', kwargs.get('cwd')))
            assert '--action-interface joint' in args[-1], 'Default Joint interface missing from worker launch'
            assert kwargs.get('cwd') == source, 'mlx simulator inherited local checkout cwd; worker chdir fails before serve.py'
            raise RuntimeError('Intentional test stop after validating real simulator launch callsite')
        raise AssertionError('Policy should not start in this minimized startup test')
    argv = ['runner', '--mode', 'development', '--bundle', str(source), '--config', str(config_path), '--output', str(output), '--seeds', '21', '--worker', 'test', '--gpu', '1']
    with patch('sys.argv', argv), patch.object(runner, 'verify', return_value={'commit': 'a' * 40}), patch.object(runner.shutil, 'disk_usage', return_value=SimpleNamespace(free=20 * 1024**3)), patch.object(runner.subprocess, 'run', side_effect=run), patch.object(runner.subprocess, 'Popen', side_effect=popen), patch.object(runner, 'EpisodeStorage', Storage):
        runner.main()
    assert remote_calls == [('audit', source), ('simulator', source)], remote_calls
    recorded = json.loads((output / 'results.json').read_text())[0]
    assert 'Intentional test stop' in recorded['error'], recorded
    launch = json.loads((output / 'launch-record.json').read_text())
    assert launch['source'] == str(source) and launch['worker_launch_cwd'] == str(source) and launch['source_commit'] == 'a' * 40
print('Runner real callsites use selected shared source cwd for resource audit and simulator launch; bundle provenance preserved')
