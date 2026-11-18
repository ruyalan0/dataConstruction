"""Exercise the runner beyond the old 200-step cutoff with a cheap fake environment."""
import importlib.util
import json
from pathlib import Path

from test_control import raw


def test_native_loop_exceeds_200_and_does_not_send_internal_horizon(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('native_loop', Path('scripts/run_episode_loop.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    captured = []
    class Policy:
        def __init__(self, command, *args, **kwargs):
            captured.append(command)
        def call(self, operation, **payload):
            captured.append(payload)
            return {'episode': 'test', 'calls': 1, 'remaining': 0, 'feedback': {}, 'action': {}}
        def close(self): pass
    class Env:
        def __init__(self):
            self.take_action_cnt = [0]
            self.success = [False]
        step_lim = 213
        def is_episode_end(self): return self.take_action_cnt[0] == self.step_lim
        def take_action(self, action): self.take_action_cnt[0] += 1
        def get_obs(self):
            data=raw();data['step_lim']='HIDDEN';data['reward']='HIDDEN';return data
    monkeypatch.setattr(module, 'ProcessPolicy', Policy)
    result=module.run_episode(Env(),tmp_path,Path('/unused/python'),None,None,'json-high',harness_profile='steps')
    assert result['native_steps']==213
    assert result['official_ended'] is True
    assert result['max_steps'] is None
    assert 'HIDDEN' not in str(captured)
    assert '--config' in captured[0]
    assert json.loads((tmp_path / 'effective-config.json').read_text())['report_steps'] is True
