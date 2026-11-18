"""Verify process-wide Azure admission, contention and release after failure."""
import multiprocessing as mp
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from provider_concurrency import azure_request_slot


def child(directory, entered, release, events, index):
    with azure_request_slot(directory=Path(directory)):
        events.put(('enter', index, time.monotonic_ns()))
        entered.set()
        assert release.wait(10)
        events.put(('exit', index, time.monotonic_ns()))


if __name__ == '__main__':
    ctx = mp.get_context('spawn')
    with TemporaryDirectory() as temporary:
        events = ctx.Queue(); release = [ctx.Event() for _ in range(4)]; entered = [ctx.Event() for _ in range(4)]
        workers = [ctx.Process(target=child, args=(temporary, entered[i], release[i], events, i)) for i in range(4)]
        try:
            for i in range(3): workers[i].start()
            assert all(entered[i].wait(5) for i in range(3)), 'Three requests must be admitted'
            workers[3].start()
            assert not entered[3].wait(.5), 'Fourth request entered before a slot was free'
            release[0].set()
            assert entered[3].wait(5), 'Waiting request did not acquire released slot'
            for event in release: event.set()
            for worker in workers:
                worker.join(5); assert worker.exitcode == 0
            rows = sorted([events.get(timeout=2) for _ in range(8)], key=lambda r: r[2])
            live = maximum = 0
            for kind, _, _ in rows:
                live += 1 if kind == 'enter' else -1
                maximum = max(maximum, live)
            assert maximum == 3 and live == 0
            try:
                with azure_request_slot(directory=Path(temporary)): raise ValueError('fixture')
            except ValueError: pass
            with azure_request_slot(directory=Path(temporary)): pass
        finally:
            for event in release: event.set()
            for worker in workers:
                if worker.pid is not None and worker.is_alive(): worker.terminate(); worker.join()
    print('PASS: three concurrent processes admitted, fourth blocked, released slots reusable after exceptions')
