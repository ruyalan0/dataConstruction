"""Reconstruct policy evidence after deterministic development action replay."""

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from .arx_x5_interpreter import rotation
from .observation_adapter import Observation


def read_trace(source):
    paths = list((Path(source) / "agent").glob("*/events.jsonl"))
    if len(paths) != 1:
        raise ValueError("Recovery requires one source episode")
    return paths[0], [json.loads(line) for line in paths[0].read_text().splitlines()]


def restore_agent(agent, source):
    from .agent import TOOLS
    if agent.protocol != "json-high":
        raise ValueError("Recovery currently requires explicit JSON protocol")
    path, events = read_trace(source)
    observations = [e["data"] for e in events if e["kind"] == "observation"]
    actions = [e for e in events if e["kind"] == "native_action"]
    if len(observations) != len(actions) + 1:
        raise ValueError("Cannot recover an unacknowledged native action")
    last = observations[-1]
    feedback = [e["data"] for e in events if e["kind"] == "feedback"]
    if not feedback or feedback[-1]["after"] != last["sequence"]:
        raise ValueError("Recovery requires a completed action segment")
    for obs in observations:
        agent.trace.frames[obs["sequence"]] = {}
        agent.trace.frame_states[obs["sequence"]] = {
            "poses": obs["poses"], "gripper_command_echo": obs["gripper_command_echo"],
            "observation_sequence": obs["sequence"],
        }
        for camera, filename in obs["images"].items():
            target = agent.trace.root / filename
            shutil.copy2(path.parent / filename, target)
            agent.trace.frames[obs["sequence"]][camera] = target
    shutil.copy2(path, agent.trace.root / "events.jsonl")
    agent.evidence.restore(events)
    agent.initial_poses = observations[0]["poses"]
    agent.executor.latched = {arm: np.array(pose) for arm, pose in agent.initial_poses.items()}
    for event in events:
        if event['kind'] == 'observation':
            observed_poses = event['data']['poses']
        elif event['kind'] == 'decision' and 'left' in event['data']:
            for arm in ('left', 'right'):
                if event['data'][arm]['operation'] == 'latch':
                    agent.executor.latched[arm] = np.array(observed_poses[arm])
    agent.tools[1]['function']['parameters']['properties']['camera']['enum'] = list(last['images'])
    agent.freeze_config()
    contract_path = path.parent / 'harness-config.json'
    if contract_path.exists():
        saved_config = json.loads(contract_path.read_text())
        actual_config = json.loads((agent.trace.root / 'harness-config.json').read_text())
        for record in (saved_config, actual_config):
            record['config'].pop('trace_root', None)
        if saved_config != actual_config:
            raise ValueError('Harness configuration or source changed; start a fresh episode')
    agent.episode_instruction = last["instruction"]
    # Recovery preserves the source prompt contract, including pre-contract traces.
    # A new public-document version requires a new episode, not a silent prompt change.
    from .public_instructions import public_instruction
    contracts = [e["data"] for e in events if e["kind"] == "public_instruction_contract"]
    agent.instruction_contract = None
    if contracts:
        if len(contracts) != 1 or contracts[0]["instruction"] != last["instruction"]:
            raise ValueError("Invalid source public instruction contract")
        saved = contracts[0]
        if saved["contract"] is not None:
            contract = public_instruction(last["instruction"])
            if (contract is None or saved["contract"] != contract.as_message()
                    or saved["source_sha256"] != contract.source_sha256):
                raise ValueError("Public instruction contract changed; start a new episode")
            agent.instruction_contract = contract
    agent.sequence = last["sequence"] + 1
    agent.executed_steps = len(actions)
    agent.executor.obs = Observation(
        last["sequence"],
        last["instruction"],
        {a: np.array(p) for a, p in last["poses"].items()},
        last["gripper_command_echo"],
        {c: np.asarray(Image.open(agent.trace.frames[last["sequence"]][c])) for c in last["images"]},
    )
    agent.executor.feedback = feedback[-1]
    streak = 0
    for item in feedback:
        streak = 0 if item.get("pose_reached", False) else streak + 1
    agent.executor.consecutive_not_reached = streak
    snapshot_path = path.parent / "session.json"
    exact = snapshot_path.exists()
    if exact:
        snapshot = json.loads(snapshot_path.read_text())
        responses = sum(e["kind"] == "response" for e in events)
        if snapshot["pending"] is not None or snapshot["calls"] != responses:
            raise ValueError("Session snapshot is not a completed operation boundary")
        if snapshot.get('format') == 'dojo-session-v2':
            from .trace_store import TraceStore

            agent.session.messages = TraceStore.load_messages(path.parent, snapshot['messages'])
        elif 'format' not in snapshot:
            agent.session.messages = snapshot['messages']
        else:
            raise ValueError('Unknown session archive format')
        agent.session.calls = snapshot["calls"]
    else:
        # Explicit legacy format: old traces lack request snapshots.
        messages = agent.session.messages
        messages.append({"role": "system", "content":
            'Return only JSON {"name":"act" or "recall","arguments":{...}}. Match schemas: '
            + json.dumps(TOOLS)})
        for event in events:
            kind, data = event["kind"], event["data"]
            if kind == "observation":
                current = {k: data[k] for k in
                    ("sequence", "instruction", "poses", "gripper_command_echo", "images")}
            elif kind == "response":
                messages.append({"role": "user", "content": [{"type": "text", "text": json.dumps(current)}]})
                messages.append(data["choices"][0]["message"])
                agent.session.calls += 1
            elif kind == "feedback":
                allowed = {k: data[k] for k in ("steps", "pose_reached", "interrupted", "remaining_steps",
                    "error_mm", "error_deg", "displacement_mm", "before", "after", "semantic_status") if k in data}
                messages.append({"role": "user", "content": json.dumps({"operation_result": allowed})})
        messages.append({"role": "user", "content": [{"type": "text", "text":
            "Development recovery: historical conversation reconstructed, not exact requests. "
            "Use fresh RGB to reassess earlier hypotheses."}]})
    agent.trace.event("recovery", {"source": str(path.parent), "replayed_steps": len(actions),
                                   "exact_session": exact})
    return len(actions)


def replay_environment(env, source, output):
    _, events = read_trace(source)
    actions = [e["data"] for e in events if e["kind"] == "native_action"]
    observations = [e["data"] for e in events if e["kind"] == "observation"]
    if len(observations) != len(actions) + 1:
        raise ValueError("Unacknowledged action prevents safe replay")
    max_position, max_angle = 0.0, 0.0
    for index, action in enumerate(actions):
        if env.is_episode_end():
            raise RuntimeError("Replay ended before saved boundary")
        before = env.take_action_cnt[0]
        env.take_action(action)
        if env.take_action_cnt[0] != before + 1:
            raise RuntimeError("Replay action was not executed")
        raw = env.get_obs()
        for arm, expected in observations[index + 1]["poses"].items():
            actual = np.array(raw["state"][f"{arm}_ee_pose"])
            distance = float(np.linalg.norm(actual[:3] - np.array(expected[:3])) * 1000)
            angle = float(np.rad2deg((rotation(actual).inv() * rotation(np.array(expected))).magnitude()))
            max_position, max_angle = max(max_position, distance), max(max_angle, angle)
            if distance > 2 or angle > 2:
                raise RuntimeError(
                    f"Replay diverged at step {index + 1}: {distance:.3f} mm / {angle:.3f} deg"
                )
    report = {
        "source": str(source),
        "replayed_steps": len(actions),
        "max_position_error_mm": max_position,
        "max_angle_error_deg": max_angle,
        "object_state_verified": False,
        "development_only": True,
    }
    (Path(output) / "replay.json").write_text(json.dumps(report, indent=2))
    return len(actions)
