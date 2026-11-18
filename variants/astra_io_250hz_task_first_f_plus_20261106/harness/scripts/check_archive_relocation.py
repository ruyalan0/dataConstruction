"""Never remove evidence when raw bytes or archive provenance differ."""
import json
from pathlib import Path
import tarfile
import tempfile
from relocate_archive_cache import relocate,digest

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);raw=root/'run';raw.mkdir()
    (raw/'evidence.json').write_text('{"failure":true}')
    archive=root/'run.tar.gz'
    with tarfile.open(archive,'w:gz') as output:output.add(raw,arcname=raw.name)
    pending=root/'pending.json'
    pending.write_text(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'sha256':digest(archive)}))
    (raw/'evidence.json').write_text('{"failure":null}')
    try:relocate(pending,root/'cache',root/'receipt.json')
    except ValueError:pass
    else:raise AssertionError('Mismatched evidence permitted removal')
    assert archive.exists() and not (root/'receipt.json').exists()
    (raw/'evidence.json').write_text('{"failure":true}')
    record=relocate(pending,root/'cache',root/'receipt.json')
    assert not archive.exists() and (raw/'evidence.json').exists()
    assert digest(Path(record['cache_copy']))==record['sha256']
    assert record['source_cache_removed'] and record['hdfs_verified'] is False
print('Mismatched raw evidence blocks removal; exact verified copy preserves original evidence and pending backup status')
