"""Corruption must prevent eviction; durable exact RGB may be removed."""
import json
from pathlib import Path
import tarfile
import tempfile
from archive_rgb_cache import digest, evict

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    run = root / 'run'
    (run / 'seed-20').mkdir(parents=True)
    (run / 'seed-20/exit-status.json').write_text('{"exit_code":0}')
    (run / 'results.json').write_text('[{"seed_index":20}]')
    rgb = run / 'seed-20/frame.jpg'
    rgb.write_bytes(b'original rgb')
    archive = root / 'run.tar.gz'
    with tarfile.open(archive, 'w:gz') as out:
        out.add(run, arcname='run')
    backup = root / 'backup.json'
    backup.write_text(json.dumps({'verified_readback': True, 'uri': 'hdfs://storage.example.invalid/REDACTED',
                                 'sha256': digest(archive), 'bytes': archive.stat().st_size}))
    rgb.write_bytes(b'changed rgb!')
    try:
        evict(run, archive, backup, root / 'receipt.json')
    except ValueError:
        pass
    else:
        raise AssertionError('Corrupt RGB removed')
    assert rgb.exists() and not (root / 'receipt.json').exists()
    rgb.write_bytes(b'original rgb')
    result = evict(run, archive, backup, root / 'receipt.json')
    assert result['removed'] == 1 and not rgb.exists()
    assert (run / 'results.json').exists() and archive.exists()
    assert json.loads((root / 'receipt.json').read_text())['files'][0]['sha256']
print('Corrupt RGB retained; exact durable RGB cache eviction preserves logs and restore inventory')
