"""Minimal reproduction of the Warp CUDA device failure, without Isaac startup."""

import argparse
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--warp-root", type=Path)
parser.add_argument("--torch-first", action="store_true")
args = parser.parse_args()
if args.warp_root:
    sys.path.insert(0, str(args.warp_root))
import torch
import warp as wp

if args.torch_first:
    torch.cuda.init()
wp.init()
print(
    json.dumps(
        {
            "loaded_cuda": [
                line.strip()
                for line in Path("/proc/self/maps").read_text().splitlines()
                if "libcuda." in line
            ]
        }
    ),
    flush=True,
)
tensor = torch.zeros(1, device="cuda")
array = wp.from_torch(tensor)
result = {
    "warp": wp.__version__,
    "source": wp.__file__,
    "cuda_devices": [str(d) for d in wp.get_cuda_devices()],
    "array_device": str(array.device),
}
print(json.dumps(result), flush=True)
if not result["cuda_devices"] or not array.device.is_cuda:
    raise RuntimeError("Warp cannot represent the CUDA tensor")
