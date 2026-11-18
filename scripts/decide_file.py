"""Isolated API process: consumes only RGB and proprioception from a JSON request."""

import argparse
import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from dojo_astra.agent import Agent
from dojo_astra.azure_session import from_example

parser = argparse.ArgumentParser()
parser.add_argument("--request", type=Path, required=True)
parser.add_argument("--response", type=Path, required=True)
parser.add_argument("--trace", type=Path, required=True)
args = parser.parse_args()
raw = json.loads(args.request.read_text())
for camera in raw["vision"].values():
    camera["color"] = np.asarray(Image.open(io.BytesIO(base64.b64decode(camera["color"]))).convert("RGB"))
agent = Agent(
    from_example("/workspace/APIs/Azure-gpt-astra.py"),
    args.trace,
    protocol="json-high",
    max_calls=4,
)
agent.update_obs(raw)
action = agent.get_action()[0]
args.response.write_text(
    json.dumps(
        {
            "action": {k: v.tolist() for k, v in action.items()},
            "steps": agent.executor.remaining + 1,
            "episode": agent.episode,
        }
    )
)
