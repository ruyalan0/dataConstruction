"""Use real allocated blocks and a running child to verify storage-pressure failure."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from egg_harness.storage import EpisodeStorage,StoragePressure,check_configured_storage
from egg_harness.runner import wait_policy
from egg_harness.vision import Vision

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);storage=EpisodeStorage(root,reserve_bytes=1024**2,minimum_free=1024**2)
    storage.allocate()
    assert storage.reserve.stat().st_blocks*512>=1024**2,'Sparse placeholder is not a reserve'
    with patch('egg_harness.storage.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
        try:storage.check()
        except StoragePressure:pass
        else:raise AssertionError('Low disk did not stop execution')
    assert not storage.reserve.exists()
    assert json.loads((root/'storage-pressure.json').read_text())['reason']=='storage_pressure'
    with patch('egg_harness.storage.shutil.disk_usage',return_value=SimpleNamespace(free=20*1024**3)):
        try:storage.check()
        except StoragePressure:pass
        else:raise AssertionError('Released reserve masked prior pressure and restarted writers')
    (root/'final-result.json').write_text('{"success":false}')
    with patch.dict(os.environ,{'RSI_STORAGE_RESERVE_DIR':str(root)}),patch('egg_harness.storage.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
        try:check_configured_storage()
        except StoragePressure:pass
        else:raise AssertionError('Writer ignored configured storage guard')
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
    try:
        assert child.poll() is None
        with patch('egg_harness.storage.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
            try:wait_policy(child,20,storage)
            except StoragePressure:pass
            else:raise AssertionError('Runner waited for completion despite low storage')
    finally:
        child.terminate();child.wait(timeout=5)
    storage.release()
    vision=Vision.__new__(Vision)
    vision.calls=0
    with patch('egg_harness.vision.check_configured_storage',side_effect=StoragePressure('full')):
        try:vision.ask('must not bill',{})
        except StoragePressure:pass
        else:raise AssertionError('Model request proceeded under storage pressure')
    assert vision.calls==0
print('Real reserve allocation verified; low space releases blocks and records failure before writer proceeds')
