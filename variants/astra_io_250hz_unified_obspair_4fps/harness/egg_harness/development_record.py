"""Resolve terminal development evidence without rewriting the original attempt."""
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def terminal_development(run, task):
    run = Path(run).resolve()
    result_path = run / "results.json"
    results = read(result_path)
    if len(results) != 1:
        raise ValueError("Development shard must hold one episode")
    result = results[0]
    index = result["seed_index"]
    episode = run / f"seed-{index}"
    provenance = read(episode / "provenance.json")
    if result["mode"] != "development" or provenance["task"] != task:
        raise ValueError("Development task/mode mismatch")
    evidence = [result_path]
    continuation = None
    if result["status"] != "native_end":
        chain = []
        for path in sorted(run.glob("*.result.json")):
            value = read(path)
            if value.get("kind") == "development_continuation":
                chain.append((path, value))
        candidates = [(path, value) for path, value in chain if value.get("native_terminal")]
        if len(candidates) != 1:
            raise ValueError("Exactly one terminal continuation required after interrupted development")
        path, continuation = candidates[0]
        if Path(continuation["episode"]).resolve() != episode or continuation.get("returncode") != 0:
            raise ValueError("Continuation episode/process mismatch")
        expected_step = result["action_count"]
        for position, (chain_path, value) in enumerate(chain):
            if Path(value["episode"]).resolve() != episode:
                raise ValueError("Continuation chain contains another episode")
            start, end = value.get("start_step"), value.get("end_step")
            if type(start) is not int or type(end) is not int or start != expected_step or end < start:
                raise ValueError("Continuation chain has a gap, overlap, or missing step evidence")
            if type(value.get("returncode")) is not int:
                raise ValueError("Continuation controller exit is not recorded")
            if value.get("native_terminal") and position != len(chain) - 1:
                raise ValueError("Continuation recorded after native terminal")
            expected_step = end
            evidence.append(chain_path)
    native = read(episode / "runner-result.json")
    if native.get("valid_sample") is False or native.get("unstable") is True:
        raise ValueError("Native environment marked development sample unstable")
    if not native["native_terminal"] or read(episode / "exit-status.json")["exit_code"] != 0:
        raise ValueError("Development has no clean terminal simulator result")
    if continuation and continuation["end_step"] != native["steps"]:
        raise ValueError("Continuation does not match final native step")
    evidence.extend([episode / "runner-result.json", episode / "exit-status.json"])
    return {"run": str(run), "layout_index": index, "score": native["score"],
            "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
            "continued": continuation is not None,
            "evidence_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in evidence}}
