"""Move duplicate archive caches only after exact raw-tree and copied-byte checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
from datetime import datetime, timezone


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def relocate(pending, destination, receipt):
    if receipt.exists():raise FileExistsError(receipt)
    metadata=json.loads(pending.read_text())
    source=Path(metadata['archive']).resolve()
    raw=source.with_name(source.name.removesuffix('.tar.gz'))
    if not source.name.endswith('.tar.gz') or not raw.is_dir():
        raise ValueError('Expected a duplicate archive with retained raw directory')
    if source.stat().st_size!=metadata['bytes'] or digest(source)!=metadata['sha256']:
        raise ValueError('Source differs from pending-backup manifest')
    inventory=set()
    with tarfile.open(source,'r|gz') as archive:
        for member in archive:
            path=Path(member.name)
            if path.is_absolute() or '..' in path.parts or path.parts[0]!=raw.name:
                raise ValueError('Unsafe archive member')
            local=raw.parent/path
            if member.isdir():
                if not local.is_dir():raise ValueError('Missing raw directory')
            elif member.isfile():
                if local.is_symlink() or not local.is_file() or local.stat().st_size!=member.size:
                    raise ValueError('Raw file mismatch: '+str(local))
                stream=archive.extractfile(member)
                if hashlib.file_digest(stream,'sha256').hexdigest()!=digest(local):
                    raise ValueError('Raw bytes differ: '+str(local))
                inventory.add(local.relative_to(raw))
            else:
                raise ValueError('Archive contains unsupported link or special member')
    actual={p.relative_to(raw) for p in raw.rglob('*') if p.is_file()}
    if actual!=inventory:raise ValueError('Raw tree has extra/missing files')
    destination.mkdir(parents=True,exist_ok=True)
    target=destination/source.name
    if target.exists():raise FileExistsError(target)
    if shutil.disk_usage(destination).free<source.stat().st_size+1024**3:
        raise RuntimeError('Insufficient cache destination space')
    with source.open('rb') as src,target.open('xb') as dst:
        shutil.copyfileobj(src,dst)
        dst.flush();os.fsync(dst.fileno())
    if digest(target)!=metadata['sha256']:raise ValueError('Copied archive checksum mismatch')
    record={'source':str(source),'cache_copy':str(target),'raw_retained':str(raw),
            'bytes':metadata['bytes'],'sha256':metadata['sha256'],'raw_files_verified':len(inventory),
            'at':datetime.now(timezone.utc).isoformat(),'hdfs_verified':False,
            'scope':'Temporary duplicate cache relocation; raw tree stays on shared storage; HDFS backup remains pending',
            'source_cache_removed':False}
    receipt.write_text(json.dumps(record,indent=2))
    source.unlink()
    record['source_cache_removed']=True
    receipt.write_text(json.dumps(record,indent=2))
    return record


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--pending',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(relocate(a.pending,a.destination,a.receipt)))
