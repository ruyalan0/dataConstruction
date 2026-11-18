"""Check explicit reasoning plus structured actions without function tools."""

import argparse
import json
from pathlib import Path

from dojo_astra.arx_x5_interpreter import Decision
from dojo_astra.azure_session import from_example

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
client = from_example("/workspace/APIs/Azure-gpt-astra.py")
response = client.chat.completions.create(
    model="gpt-6-astra",
    reasoning_effort="high",
    max_tokens=4096,
    response_format={"type": "json_object"},
    messages=[
        {
            "role": "system",
            "content": "Return one JSON action matching this schema: "
            + json.dumps(Decision.model_json_schema()),
        },
        {"role": "user", "content": "Hold both arms and grippers for one step."},
    ],
)
(args.output / "response.json").write_text(response.model_dump_json(indent=2))
if response.choices[0].finish_reason != "stop":
    raise RuntimeError("Incomplete structured response")
action = Decision.model_validate_json(response.choices[0].message.content)
if action.steps != 1 or action.left.operation != "hold" or action.right.operation != "hold":
    raise RuntimeError("Incorrect structured action")
print(json.dumps({"passed": True, "model": response.model, "usage": response.usage.model_dump()}))
