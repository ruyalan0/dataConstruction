"""Exercise the actual serve finalizer with a broken logging stream."""
import ast
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

source = ast.parse(Path("scripts/serve.py").read_text())
finalizer = next(node for node in source.body if isinstance(node, ast.Try)).finalbody
code = compile(ast.fix_missing_locations(ast.Module(body=finalizer, type_ignores=[])),
               "serve-finalizer", "exec")

class BrokenStream:
    def flush(self):
        raise BrokenPipeError("disconnected launcher")

class Timer:
    started = False
    def __init__(self, *args, **kwargs):
        pass
    def start(self):
        Timer.started = True

with tempfile.TemporaryDirectory() as directory:
    output = Path(directory)
    namespace = dict(args=SimpleNamespace(output=output), json=json, exit_code=1,
                     threading=SimpleNamespace(Timer=Timer), os=SimpleNamespace(_exit=None),
                     sys=SimpleNamespace(stdout=BrokenStream(), stderr=BrokenStream()), app=None)
    try:
        exec(code, namespace)
    except BrokenPipeError:
        pass
    else:
        raise AssertionError("Expected disconnected-stream failure")
    assert json.loads((output / "exit-status.json").read_text()) == {"exit_code": 1}
    assert Timer.started, "Shutdown watchdog must start before fragile logging"
print("Failure status and shutdown watchdog survive broken logging")
