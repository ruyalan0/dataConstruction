"""Relocate an explicitly selected closed derived cache, preserving its path."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def sync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def relocate(source,destination,receipt):
    source=source.absolute();destination=destination.absolute();receipt=receipt.absolute()
    if source.is_symlink() or source.resolve()!=source or not source.is_file():
        raise ValueError('Source must be an ordinary file without symlink parents')
    if source.suffix not in ('.zip','.mp4','.webm'):
        raise ValueError('Only explicitly selected derived video/export caches supported')
    if destination.exists() or destination.is_symlink() or receipt.exists():
        raise FileExistsError('Destination and receipt must be new')
    if not destination.parent.is_dir() or not receipt.parent.is_dir():
        raise ValueError('Destination and receipt parents must exist')
    original=source.stat();digest=sha(source)
    if shutil.disk_usage(destination.parent).free<original.st_size+1024**3:
        raise RuntimeError('Insufficient destination space')
    with source.open('rb') as src,destination.open('xb') as dst:
        shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
    shutil.copystat(source,destination);sync(destination.parent)
    if sha(destination)!=digest:raise ValueError('Copied cache checksum mismatch')
    record={'source':str(source),'cache_copy':str(destination),'sha256':digest,'bytes':original.st_size,
            'status':'prepared','durable_backup':False,'scope':'Closed derived cache only; original path retained by symlink; temporary storage is not HDFS backup'}
    def save():
        tmp=receipt.with_suffix(receipt.suffix+'.tmp')
        with tmp.open('x') as stream:
            json.dump(record,stream,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,receipt);sync(receipt.parent)
    save()
    current=source.stat()
    if any(getattr(current,k)!=getattr(original,k) for k in ('st_ino','st_size','st_mtime_ns','st_ctime_ns')) or sha(source)!=digest:
        raise ValueError('Source changed during relocation')
    link=source.with_name(source.name+'.rsi-relocation.tmp')
    link.symlink_to(destination)
    os.replace(link,source);sync(source.parent)
    if sha(source)!=digest:raise ValueError('Relocated path checksum mismatch')
    record['status']='relocated';save()
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('source','destination','receipt'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(relocate(args.source,args.destination,args.receipt)))
