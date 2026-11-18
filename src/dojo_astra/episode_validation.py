"""Check durable rollout evidence without inferring task success."""

import json
from pathlib import Path


def validate_episode(output):
    output = Path(output)
    if (output / "error.txt").exists():
        raise RuntimeError((output / "error.txt").read_text())
    status = json.loads((output / "exit-status.json").read_text())
    result = json.loads((output / "episode-result.json").read_text())
    if status["exit_code"] != 0 or result["status"] != "completed":
        raise ValueError("Rollout did not complete cleanly")
    traces = list((output / "agent").glob("*/events.jsonl"))
    if len(traces) != 1 or traces[0].parent.name != result["episode"]:
        raise ValueError("Expected exactly one matching policy episode")
    events = [json.loads(line) for line in traces[0].read_text().splitlines()]
    observations = [e["data"] for e in events if e["kind"] == "observation"]
    actions = [e for e in events if e["kind"] == "native_action"]
    responses = [e for e in events if e["kind"] == "response"]
    steps = result["native_steps"]
    if steps < 0 or (result["max_steps"] is not None and steps > result["max_steps"]) or len(actions) != steps:
        raise ValueError("Native action evidence does not match reported steps")
    expected_observations = steps + 1 + int(bool(result.get("recovery_source")))
    if [o["sequence"] for o in observations] != list(range(expected_observations)):
        raise ValueError("Missing or unordered native observation")
    if (
        len(responses) != result["calls"]
        or result["calls"] < 0
        or (result["max_calls"] is not None and result["calls"] > result["max_calls"])
    ):
        raise ValueError("Model call evidence does not match reported calls")
    for obs in observations:
        for filename in obs["images"].values():
            path = traces[0].parent / filename
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("Missing archived RGB evidence")
    if result["official_ended"]:
        if result["stop_reason"] != "official_end" or type(result["task_success"]) is not bool:
            raise ValueError("Invalid official terminal result")
    elif (
        result["stop_reason"] != "development_budget"
        or result["task_success"] is not None
        or (
            (result["max_steps"] is None or steps < result["max_steps"])
            and (result["max_calls"] is None or result["calls"] < result["max_calls"])
        )
    ):
        raise ValueError("A truncated rollout must exhaust a budget and have unknown success")
    return result
