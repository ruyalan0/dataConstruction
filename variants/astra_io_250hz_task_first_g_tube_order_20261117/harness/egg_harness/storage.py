"""Allocated cleanup reserve and fail-fast storage checks for episode writers."""
import json
import os
import shutil
import time
import uuid
from pathlib import Path


class StoragePressure(RuntimeError):
    pass


class EpisodeStorage:
    def __init__(self,folder,reserve_bytes=256*1024**2,minimum_free=1024**3):
        self.folder=Path(folder)
        self.reserve=self.folder/'cleanup-reserve.bin'
        self.minimum_free=minimum_free
        self.reserve_bytes=reserve_bytes

    def allocate(self):
        if shutil.disk_usage(self.folder).free < self.minimum_free+self.reserve_bytes:
            raise StoragePressure('Insufficient storage for allocated cleanup reserve')
        with self.reserve.open('xb') as stream:
            os.posix_fallocate(stream.fileno(),0,self.reserve_bytes)
            stream.flush();os.fsync(stream.fileno())

    def release(self):
        self.reserve.unlink(missing_ok=True)

    def check(self):
        if (self.folder/'storage-pressure.json').exists():
            raise StoragePressure('Recorded output storage pressure; this attempt cannot resume')
        free=shutil.disk_usage(self.folder).free
        if free < self.minimum_free:
            self.release()
            record={'free_bytes':free,'minimum_free_bytes':self.minimum_free,
                    'released_reserve_bytes':self.reserve_bytes,'at_unix':time.time(),
                    'reason':'storage_pressure','scope':'Fail before another model request or native action; not native task completion'}
            path=self.folder/'storage-pressure.json'
            temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
            temporary.write_text(json.dumps(record,indent=2));temporary.replace(path)
            raise StoragePressure('Output storage below running minimum; cleanup reserve released')


def check_configured_storage():
    folder=os.environ.get('RSI_STORAGE_RESERVE_DIR')
    if folder is not None:EpisodeStorage(folder).check()
