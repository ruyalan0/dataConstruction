"""Select validated NVIDIA libraries matching this worker kernel, then exec."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import tempfile


def prepare(driver_text, library_root, output):
    match = re.search(r"Kernel Module[ ]+([0-9]+[.][0-9]+[.][0-9]+)", driver_text)
    if match is None:
        raise ValueError("Cannot identify NVIDIA kernel driver")
    version = match.group(1)
    aliases = {"libcuda.so.1": "libcuda", "libnvidia-ml.so.1": "libnvidia-ml",
               "libGLX_nvidia.so.0": "libGLX_nvidia", "libEGL_nvidia.so.0": "libEGL_nvidia",
               "libnvidia-ptxjitcompiler.so.1": "libnvidia-ptxjitcompiler"}
    targets = {alias: library_root / (name + ".so." + version) for alias, name in aliases.items()}
    for target in targets.values():
        with target.open("rb") as stream:
            if stream.read(4) != bytes([127, 69, 76, 70]):
                raise ValueError("Driver library is not ELF: " + str(target))
    output.mkdir(exist_ok=False)
    for alias, target in targets.items():
        (output / alias).symlink_to(target)
    return {"kernel_version": version, "directory": str(output),
            "libraries": {alias: str(target) for alias, target in targets.items()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.record.exists():
        raise ValueError("A new record path and executable command are required")
    parent = Path(tempfile.mkdtemp(prefix="rsi-driver-"))
    record = prepare(Path("/proc/driver/nvidia/version").read_text(),
                     Path("/usr/lib/x86_64-linux-gnu"), parent / "lib")
    for target in record["libraries"].values():
        ctypes.CDLL(target)
    env = dict(os.environ)
    env.pop("LD_PRELOAD", None)
    env["LD_LIBRARY_PATH"] = "/usr/local/cuda-12.6/compat:" + record["directory"]
    record["library_path"] = env["LD_LIBRARY_PATH"]
    args.record.write_text(json.dumps(record, indent=2))
    os.execvpe(command[0], command, env)
