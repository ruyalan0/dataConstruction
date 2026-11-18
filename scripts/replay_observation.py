"""Ask Astra for one action from an archived real observation, without executing it."""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from dojo_astra.agent import Agent
from dojo_astra.azure_session import from_example

parser = argparse.ArgumentParser()
parser.add_argument("--trace", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--protocol", choices=["tools", "json-high"], required=True)
args = parser.parse_args()
events = [json.loads(line) for line in (args.trace / "events.jsonl").read_text().splitlines()]
obs = next(event["data"] for event in events if event["kind"] == "observation")
state = {}
for arm, pose in obs["poses"].items():
    state[f"{arm}_ee_pose"] = pose
    state[f"{arm}_ee_joint_state"] = [obs["gripper_command_echo"][arm]]
vision = {
    name: {"color": np.asarray(Image.open(args.trace / file).convert("RGB"))}
    for name, file in obs["images"].items()
}
agent = Agent(
    from_example("/workspace/APIs/Azure-gpt-astra.py"),
    args.output,
    protocol=args.protocol,
    max_calls=4,
)
agent.update_obs(
    {"data_format_version": "v1.0", "instruction": obs["instruction"], "state": state, "vision": vision}
)
action = agent.get_action()[0]
print(json.dumps({"action": {k: v.tolist() for k, v in action.items()}, "executed": False}))
