"""Bounded live probe: RGB, act tool, tool result continuation, episode reset."""

import argparse
import json
from pathlib import Path

import numpy as np

from dojo_astra.agent import Agent
from dojo_astra.azure_session import from_example

parser = argparse.ArgumentParser()
parser.add_argument("--credentials-example", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--protocol", choices=["tools", "json-high"], default="tools")
args = parser.parse_args()
client = from_example(args.credentials_example)
agent = Agent(client, args.output, max_calls=4, protocol=args.protocol)
raw = {
    "data_format_version": "v1.0",
    "instruction": "API integration test only: hold both arms and grippers for one step. Do not move.",
    "state": {
        "left_ee_pose": [0.1, 0, 0.9, 1, 0, 0, 0],
        "right_ee_pose": [-0.1, 0, 0.9, 1, 0, 0, 0],
        "left_ee_joint_state": [1.0],
        "right_ee_joint_state": [1.0],
    },
    "vision": {"test_camera": {"color": np.full((64, 64, 3), 127, dtype=np.uint8)}},
}
for _ in range(2):
    agent.update_obs(raw)
    action = agent.get_action()[0]
    for arm in ("left", "right"):
        np.testing.assert_allclose(action[f"{arm}_ee_pose"], raw["state"][f"{arm}_ee_pose"])
    if agent.executor.remaining != 0:
        raise ValueError("Expected one native step in probe")
agent.update_obs(raw)
result = {
    "model": agent.model,
    "protocol": "chat.completions",
    "reasoning_effort": "none" if args.protocol == "tools" else "high",
    "action_protocol": args.protocol,
    "calls": agent.session.calls,
    "episode": agent.episode,
    "rgb_tool_continuation_passed": True,
    "simulation_executed": False,
}
agent.reset()
assert len(agent.session.messages) == 1 and agent.executor.obs is None
result["reset_passed"] = True
args.output.mkdir(parents=True, exist_ok=True)
(args.output / "probe.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result))
