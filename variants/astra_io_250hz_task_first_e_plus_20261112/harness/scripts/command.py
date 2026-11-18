"""Submit an atomic command to the worker via shared NAS."""
import argparse
import json
from pathlib import Path
import time

p = argparse.ArgumentParser()
p.add_argument('episode', type=Path)
p.add_argument('command', help='JSON object')
p.add_argument('--timeout', type=float, default=240)
a = p.parse_args()
command = json.loads(a.command)
command.setdefault('id', str(time.time_ns()))
path = a.episode / 'commands' / (command['id'] + '.json')
if path.exists():
    raise FileExistsError(path)
tmp = path.with_suffix('.tmp')
tmp.write_text(json.dumps(command))
tmp.replace(path)
response = a.episode / 'responses' / path.name
start = time.monotonic()
while not response.exists():
    if (a.episode / 'error.txt').exists():
        raise RuntimeError((a.episode / 'error.txt').read_text())
    if time.monotonic() - start > a.timeout:
        raise TimeoutError(command['id'])
    time.sleep(.25)
print(response.read_text())
