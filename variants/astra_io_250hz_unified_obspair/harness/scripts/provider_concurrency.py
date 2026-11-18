"""Cross-process Azure request limit shared by all evaluation campaigns."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time

AZURE_LIMIT = 3
AZURE_LOCKS = Path('/workspace/Astra-Robodojo-RSI-0914/artifacts/runtime/azure-astra-concurrency-3')


@contextmanager
def azure_request_slot(audit=None, directory=AZURE_LOCKS):
    directory.mkdir(parents=True, exist_ok=True)
    streams = [(directory / f'slot-{i}.lock').open('a+') for i in range(AZURE_LIMIT)]
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
                    log.write(json.dumps({'provider': 'azure-astra', 'limit': AZURE_LIMIT,
                        'pid': os.getpid(), 'worker': os.environ.get('ARNOLD_WORKER_ID'),
                        'slot': selected, 'wait_started_ns': waiting, 'acquired_ns': acquired,
                        'released_ns': released}) + '\n')
    finally:
        for stream in streams:
            stream.close()
