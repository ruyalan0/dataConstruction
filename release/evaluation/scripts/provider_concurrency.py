"""Cross-process provider request limits shared by all evaluation campaigns."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time

AZURE_LIMIT = 4
# Keep the original namespace: old frozen adapters still contend for slots 0..2.
AZURE_LOCKS = Path(os.environ['RSI_REQUEST_LOCK_DIR']) / 'azure-astra-concurrency-3'
ZZY_LIMIT = 5
ZZY_LOCKS = AZURE_LOCKS.parent / 'zzy-astra-concurrency-2'
PROVIDER_LIMITS = {'azure-astra': AZURE_LIMIT, 'zzy-astra': ZZY_LIMIT}


@contextmanager
def _request_slot(provider, limit, audit, directory):
    directory.mkdir(parents=True, exist_ok=True)
    streams = [(directory / f'slot-{i}.lock').open('a+') for i in range(limit)]
    selected = None
    waiting = time.monotonic_ns()
    try:
        while selected is None:
            for i, stream in enumerate(streams):
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                selected = i
                break
            if selected is None:
                time.sleep(.1)
        acquired = time.monotonic_ns()
        try:
            yield selected
        finally:
            released = time.monotonic_ns()
            if audit is not None:
                with Path(audit).open('a') as log:
                    log.write(json.dumps({'provider': provider, 'limit': limit,
                        'pid': os.getpid(), 'worker': os.environ.get('ARNOLD_WORKER_ID'),
                        'slot': selected, 'wait_started_ns': waiting, 'acquired_ns': acquired,
                        'released_ns': released}) + '\n')
    finally:
        for stream in streams:
            stream.close()


def azure_request_slot(audit=None, directory=AZURE_LOCKS):
    return _request_slot('azure-astra', AZURE_LIMIT, audit, directory)


def zzy_request_slot(audit=None, directory=ZZY_LOCKS):
    return _request_slot('zzy-astra', ZZY_LIMIT, audit, directory)
