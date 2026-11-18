"""A simulator must keep logging after its launcher closes pipe readers."""
import subprocess
import sys
import tempfile
from pathlib import Path

from egg_harness.runner import simulator_command

with tempfile.TemporaryDirectory() as directory:
    log = Path(directory) / "worker output.log"
    command = [sys.executable, "-c",
               "import time,sys; time.sleep(.1); print('stdout alive',flush=True); print('stderr alive',file=sys.stderr,flush=True)"]
    child = subprocess.Popen(["bash", "-lc", simulator_command(command, log)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child.stdout.close()
    child.stderr.close()
    assert child.wait(timeout=5) == 0, "Simulator failed after launcher disconnected"
    assert set(log.read_text().splitlines()) == {"stdout alive", "stderr alive"}
print("worker logging survives disconnected launcher readers")
