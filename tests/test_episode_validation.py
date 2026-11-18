import json

import pytest

from dojo_astra.episode_validation import validate_episode


def evidence(tmp_path):
    trace = tmp_path / "agent" / "episode"
    trace.mkdir(parents=True)
    (trace / "rgb.png").write_bytes(b"image-evidence")
    events = [
        {"kind": "observation", "data": {"sequence": 0, "images": {"head": "rgb.png"}}},
        {"kind": "response", "data": {}},
        {"kind": "native_action", "data": {}},
        {"kind": "observation", "data": {"sequence": 1, "images": {"head": "rgb.png"}}},
    ]
    (trace / "events.jsonl").write_text(chr(10).join(json.dumps(e) for e in events))
    (tmp_path / "exit-status.json").write_text(json.dumps({"exit_code": 0}))
    result = {
        "status": "completed",
        "episode": "episode",
        "native_steps": 1,
        "max_steps": 1,
        "calls": 1,
        "max_calls": 2,
        "official_ended": False,
        "stop_reason": "development_budget",
        "task_success": None,
    }
    (tmp_path / "episode-result.json").write_text(json.dumps(result))
    return result, trace


def test_budget_stop_is_not_success(tmp_path):
    result, _ = evidence(tmp_path)
    assert validate_episode(tmp_path)["task_success"] is None
    result["task_success"] = True
    (tmp_path / "episode-result.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="truncated"):
        validate_episode(tmp_path)


def test_unlimited_calls_still_requires_environment_budget_or_terminal(tmp_path):
    result, _ = evidence(tmp_path)
    result["max_calls"] = None
    (tmp_path / "episode-result.json").write_text(json.dumps(result))
    assert validate_episode(tmp_path)["max_calls"] is None
    result["max_steps"] = 200
    (tmp_path / "episode-result.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="truncated"):
        validate_episode(tmp_path)


def test_missing_observation_rejects_otherwise_successful_exit(tmp_path):
    _, trace = evidence(tmp_path)
    lines = (trace / "events.jsonl").read_text().splitlines()
    (trace / "events.jsonl").write_text(chr(10).join(lines[:-1]))
    with pytest.raises(ValueError, match="observation"):
        validate_episode(tmp_path)


def test_missing_image_and_simulator_error_are_not_ignored(tmp_path):
    _, trace = evidence(tmp_path)
    (trace / "rgb.png").unlink()
    with pytest.raises(ValueError, match="RGB"):
        validate_episode(tmp_path)
    (tmp_path / "error.txt").write_text("simulator failed")
    with pytest.raises(RuntimeError, match="simulator failed"):
        validate_episode(tmp_path)


def test_native_termination_without_any_harness_cap(tmp_path):
    result, _ = evidence(tmp_path)
    result.update(max_steps=None, max_calls=None, official_ended=True,
                  stop_reason='official_end', task_success=False)
    (tmp_path / 'episode-result.json').write_text(json.dumps(result))
    assert validate_episode(tmp_path)['max_steps'] is None
    result.update(official_ended=False, stop_reason='development_budget', task_success=None)
    (tmp_path / 'episode-result.json').write_text(json.dumps(result))
    with pytest.raises(ValueError, match='truncated'):
        validate_episode(tmp_path)
