"""Validate a predeclared batch against recorded capabilities and policy inputs."""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from egg_harness.freeze import verify


def read(path):
    return json.loads(path.read_text())


def pinned_config(bundle, manifest, config):
    public = read(bundle / "configs/public-tasks.json")[config["task"]]
    candidates = []
    for name in manifest["files"]:
        if not name.startswith("configs/") or not name.endswith(".json"):
            continue
        source = read(bundle / name)
        if not isinstance(source, dict) or source.get("task") != config["task"]:
            continue
        expected = dict(source)
        expected.update(memory=str(bundle / "memory/skills.json"),
                        skills_root=str(bundle / "skills"), public_task_requirements=public)
        if expected == config:
            candidates.append(name)
    if not candidates:
        raise ValueError("Runtime configuration does not match any pinned task configuration")
    return candidates


def pinned_runtime_source(episode, config):
    expected=config.get('robodojo_revision')
    if expected is None:
        return {'verified':False,'reason':'Candidate did not pin external runtime source'}
    record=read(episode/'runtime-source-provenance.json')
    if record.get('status')!='verified' or record.get('commit')!=expected or record.get('clean') is not True:
        raise ValueError('External runtime source does not match pinned clean revision')
    if Path(record['path']).resolve()!=Path(config['robodojo_root']).resolve():
        raise ValueError('External runtime checkout path differs from configuration')
    required=config.get('robodojo_required_submodules',{})
    actual=record.get('required_submodules',{})
    if set(actual)!=set(required):raise ValueError('Required runtime submodule inventory differs')
    for name,revision in required.items():
        row=actual[name]
        if row.get('commit')!=revision or row.get('clean') is not True:
            raise ValueError('Required runtime submodule revision or clean status differs')
        if Path(row['path']).resolve()!=Path(config['robodojo_root']).resolve()/name:
            raise ValueError('Required runtime submodule checkout path differs')
    return {'verified':True,'commit':expected,'required_submodules':required,
            'scope':'Startup source provenance; installed runtime packages and post-start external edits not certified'}


def audit(plan_path, runs):
    plan = read(plan_path)
    bundle = Path(plan["bundle"])
    manifest = verify(bundle)
    assert manifest["files"] == plan["files"]
    assert manifest["commit"] == plan["commit"]
    declared = datetime.fromisoformat(plan["created_at"]).timestamp()
    development_indices = set()
    for development in plan["development"]:
        path = Path(development["run"]) / "results.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == development["result_sha256"]
        assert path.stat().st_mtime <= declared, "Development completed after declaration"
        for evidence, expected in development.get("evidence_sha256", {}).items():
            evidence_path = Path(evidence)
            assert hashlib.sha256(evidence_path.read_bytes()).hexdigest() == expected
            assert evidence_path.stat().st_mtime <= declared
        development_indices.add(development["layout_index"])
    curriculum = read(bundle / "configs/curriculum.json")
    from egg_harness.layout_split import heldout_layouts
    assert plan["heldout_layouts"] == heldout_layouts(curriculum, plan["task"])
    assert development_indices == set(curriculum["development_layouts"])
    assert not development_indices.intersection(plan["heldout_layouts"])
    rows = []
    batch_config = None
    for run in runs:
        results = read(run / "results.json")
        assert len(results) == 1, "Each independent shard must contain one episode"
        result = results[0]
        index = result["seed_index"]
        episode = (run / f"seed-{index}").resolve()
        trace = run / f"policy-{index}"
        provenance = read(episode / "provenance.json")
        assert provenance["task"] == plan["task"] and provenance["mode"] == "frozen"
        assert all(provenance[key] is False for key in ("privileged_diagnostics", "depth_calibration", "object_teleportation"))
        assert result["mode"] == "frozen" and result["human_interventions"] == 0
        assert read(run / f"source-{index}.json")["files"] == manifest["files"]
        config = read(run / f"config-{index}.json")
        pinned_config(bundle, manifest, config)
        runtime_source=pinned_runtime_source(episode,config)
        if batch_config is None:
            batch_config = config
        elif config != batch_config:
            raise ValueError("Frozen episodes used different runtime configurations")
        assert Path(config["memory"]).resolve() == bundle / "memory/skills.json"
        assert Path(config["skills_root"]).resolve() == bundle / "skills"
        expected_skills = {str(bundle / "skills" / name / "SKILL.md"): manifest["files"][f"skills/{name}/SKILL.md"] for name in config["skill_names"]}
        assert read(trace / "skills.json") == expected_skills
        requests = sorted((trace / "model").glob("*.request.json"))
        assert requests and requests[0].stat().st_mtime >= declared
        first = json.loads(read(requests[0])["question"])
        assert first["step"] == 0 and first["previous_result"] is None
        assert first["recent_executed_segments"] == [] and first["stalled_segments_this_controller"] == []
        assert first["memory"] == "Fresh controller. Infer current scene; it may be a preserved failure scene."
        for request in requests:
            data = read(request)
            for image in data["images"]:
                assert Path(image["path"]).resolve().is_relative_to(episode), "Cross-episode image input"
        native = read(episode / "runner-result.json")
        assert native.get("valid_sample", True) and not native.get("unstable", False), "Unstable native sample cannot establish acceptance"
        assert result["status"] == "native_end" and native["native_terminal"]
        assert result["score"] == native["score"] and result["success"] == native["success"]
        assert read(episode / "exit-status.json")["exit_code"] == 0
        rows.append({"run": str(run), "layout": index, "score": native["score"], "success": native["success"], "calls": len(requests), 'runtime_source':runtime_source})
    assert len(rows) == 3 and {r["layout"] for r in rows} == set(plan["heldout_layouts"])
    target = next(t["target_mean_score"] for t in curriculum["tasks"] if t["task"] == plan["task"])
    mean = sum(r["score"] for r in rows) / 3
    return {"task": plan["task"], "commit": manifest["commit"], "episodes": rows, "mean_score": mean, "target": target, "meets_target": mean >= target, "recorded_integrity_and_independence": True, "limits": "File hashes, initial input reset, image provenance and local timestamp ordering verified. This is not an OS security sandbox or a cryptographic proof of wall-clock ordering. Cross-task ordering is audited separately."}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--runs", type=Path, nargs=3, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    report = audit(a.plan, a.runs)
    a.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
