"""Load verified runtime helpers independently of historical import search paths."""
import hashlib
import importlib.util
import sys
from pathlib import Path


def load_runtime(directory, expected):
    directory = Path(directory).resolve()

    def load(name):
        path = directory / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected[name]:
            raise ValueError(f'Runtime helper checksum mismatch: {name}')
        spec = importlib.util.spec_from_file_location('_physevo_' + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    driver = load('worker_driver.py')
    # The frozen restorer itself imports worker_driver at module load time.
    # Bind that dependency before executing it; restore the caller's cache after.
    previous = sys.modules.get('worker_driver')
    sys.modules['worker_driver'] = driver
    try:
        restorer = load('restore_worker_runtime.py')
    finally:
        if previous is None:
            del sys.modules['worker_driver']
        else:
            sys.modules['worker_driver'] = previous
    if restorer.prepare is not driver.prepare:
        raise RuntimeError('The verified driver was not loaded')
    return restorer
