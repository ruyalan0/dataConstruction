"""Check incremental archives and failed-upload state without invoking HDFS."""
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import time
import run_eight_eval as runner

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    case, destination = root/'case', root/'receipt'
    case.mkdir(); destination.mkdir()
    (case/'latest.json').write_text('{"step":0}')
    (case/'cleanup-reserve.bin').write_bytes(b'reserve')
    received = []
    def upload(archive, uri, receipt):
        with tarfile.open(archive) as package:
            received.append({item.name: package.extractfile(item).read() for item in package.getmembers()})
        runner.save(receipt, {'verified_readback': True, 'uri': uri})
    runner.hdfs_archive = upload
    durable = runner.DurableCase(case, destination)
    durable.snapshot(final=True)
    assert received == [{'case/latest.json': b'{"step":0}'}]
    durable.snapshot(final=True)
    assert len(received) == 1
    (case/'latest.json').write_text('{"step":10}')
    durable.snapshot(final=True)
    assert received[-1]['case/latest.json'] == b'{"step":10}'
    assert json.loads((destination/'durability.json').read_text())['shards'] == 2
    known = dict(durable.known)
    (case/'latest.json').write_text('{"step":20}')
    def fail(*args): raise RuntimeError('upload failed')
    runner.hdfs_archive = fail
    try: durable.snapshot(final=True)
    except RuntimeError: pass
    else: raise AssertionError('Failed durability must propagate')
    assert durable.known == known and durable.shard == 2
    shared = root/'shared-summary.json'
    def publish(writer):
        for sequence in range(8):
            runner.save(shared, {'writer': writer, 'sequence': sequence, 'payload': [writer]*100})
            observed = json.loads(shared.read_text())
            assert observed['payload'] == [observed['writer']]*100
    with ThreadPoolExecutor(max_workers=32) as pool: list(pool.map(publish, range(32)))
    assert not list(root.glob('shared-summary.json.*.tmp'))
print('PASS: incremental restore order, unchanged-file exclusion, reserve exclusion, failed-upload preservation')
print('PASS: 32 concurrent report writers publish complete JSON without temporary-file collisions')
