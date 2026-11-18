"""Offline regression for the real two-card restorer; no historical episodes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
from unittest.mock import patch

from frozen_runtime import load_runtime


def main():
    source = Path(__file__).resolve().parents[1] / 'release/worker'
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        helpers = root / 'helpers'
        helpers.mkdir()
        expected = {}
        for name in ('worker_driver.py', 'restore_worker_runtime.py'):
            shutil.copyfile(source / name, helpers / name)
            expected[name] = hashlib.sha256((helpers / name).read_bytes()).hexdigest()
        legacy = root / 'legacy'
        legacy.mkdir()
        (legacy / 'restore_worker_runtime.py').write_text("raise RuntimeError('legacy 4/8-card helper selected')\n")
        (legacy / 'worker_driver.py').write_text("raise RuntimeError('legacy driver selected')\n")
        old_driver = types.ModuleType('worker_driver')
        old_driver.prepare = None
        with patch.object(sys, 'path', [str(legacy), *sys.path]), patch.dict(sys.modules, {'worker_driver': old_driver}):
            module = load_runtime(helpers, expected)
            assert Path(module.__file__).parent == helpers
            assert sys.modules['worker_driver'] is old_driver
            worker = {'Status': 'Running', 'GpuCount': 2, 'WorkerType': 'NVIDIA-L20', 'WorkerSid': 'wkr_fixture'}
            for name, data in [('runtime.json', {}), ('worker.json', worker), ('local.json', {})]:
                (root / name).write_text(json.dumps(data))
            with patch.dict(os.environ, {'ARNOLD_WORKER_ID': 'wrong-worker'}):
                try:
                    module.restore(root / 'runtime.json', root / 'worker.json', root / 'local.json', root / 'output')
                except ValueError as error:
                    assert str(error) == 'Run restoration on the selected worker, not the local Pod'
                else:
                    raise AssertionError('Worker guard was not reached')
            (helpers / 'worker_driver.py').write_text('raise RuntimeError("tampered")\n')
            try:
                load_runtime(helpers, expected)
            except ValueError as error:
                assert 'checksum mismatch' in str(error)
            else:
                raise AssertionError('Changed helper was accepted')
    print('PASS: frozen imports ignore legacy paths/cache, accept two-card shape, and reject tampering')


if __name__ == '__main__':
    main()
