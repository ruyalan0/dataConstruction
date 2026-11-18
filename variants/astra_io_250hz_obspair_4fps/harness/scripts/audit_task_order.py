"""Recompute registered acceptance and check observed task start ordering."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from audit_frozen_batch import audit


def read(path):
    return json.loads(path.read_text())


def user_acceptances(registry, curriculum):
    names={row['task'] for row in curriculum}
    result={}
    for entry in registry.get('user_acceptances',[]):
        task=entry['task']
        if task not in names or task in result:raise ValueError('Unknown or duplicate user acceptance')
        if entry.get('authority')!='explicit_user_instruction' or not entry.get('instruction') or entry.get('automatic_benchmark_pass') is not False:
            raise ValueError('User acceptance must preserve explicit authority and benchmark distinction')
        accepted_at=datetime.fromisoformat(entry['accepted_at'])
        if accepted_at.tzinfo is None:raise ValueError('Acceptance timestamp must include timezone')
        result[task]={'completed_at_unix':accepted_at.timestamp(),'acceptance_basis':'explicit_user_instruction','record':entry}
    return result


def two_scene_acceptances(registry, curriculum):
    from egg_harness.development_record import terminal_development
    targets={row['task']:row['target_mean_score'] for row in curriculum}
    accepted={}
    for entry in registry.get('two_scene_acceptances',[]):
        task=entry['task']
        if task not in targets or task in accepted:raise ValueError('Unknown or duplicate two-scene acceptance')
        if len(entry['runs'])!=2:raise ValueError('Exactly two scene records required')
        records=[terminal_development(Path(run),task) for run in entry['runs']]
        if len({row['layout_index'] for row in records})!=2:raise ValueError('Distinct scene layouts required')
        target=targets[task]
        if any(not (row['score']>target or target==100 and row['score']==100) for row in records):
            raise ValueError('Each scene must exceed benchmark (or match the100-point ceiling)')
        at=datetime.fromisoformat(entry['accepted_at'])
        if at.tzinfo is None:raise ValueError('Acceptance timestamp needs timezone')
        accepted[task]={'completed_at_unix':at.timestamp(),'acceptance_basis':'user_authorized_two_distinct_scenes','target':target,'evidence':records,'scope':'Development evidence; not a three-scene frozen benchmark claim'}
    return accepted


def ordering_rows(curriculum, accepted, starts, parallel_groups, max_unfinished_tasks=None):
    names = [item['task'] for item in curriculum]
    if max_unfinished_tasks is not None and (type(max_unfinished_tasks) is not int or max_unfinished_tasks<1):
        raise ValueError('Rolling parallelism requires a positive integer capacity')
    groups = {}
    for group in parallel_groups:
        if not isinstance(group, list) or len(group) < 2 or len(set(group)) != len(group):
            raise ValueError('Parallel groups require at least two distinct tasks')
        for task in group:
            if task not in names or task in groups:
                raise ValueError('Unknown or overlapping parallel task')
            groups[task] = set(group)
    rows = []
    preceding = []
    for task in names:
        dependencies = [previous for previous in preceding if previous not in groups.get(task, set())]
        violations = []
        if task in starts:
            violations = [previous for previous in dependencies if previous not in accepted or accepted[previous]['completed_at_unix'] > starts[task]]
            if max_unfinished_tasks is not None:
                prior=preceding
                unstarted=[name for name in prior if name not in starts or starts[name]>starts[task]]
                unfinished=[name for name in prior if name not in accepted or accepted[name]['completed_at_unix']>starts[task]]
                violations=unstarted if len(unfinished)<max_unfinished_tasks else list(dict.fromkeys(unstarted+unfinished))
        rows.append({'task': task, 'started_at_unix': starts.get(task), 'accepted': task in accepted,
                     'required_preceding_tasks': dependencies, 'preceding_task_violations': violations})
        preceding.append(task)
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    config = read(Path("configs/curriculum.json"))
    curriculum = config["tasks"]
    registry = read(Path("configs/evaluation-registry.json"))
    registered = registry["batches"]
    accepted = {}
    for batch in registered:
        report = audit(Path(batch["plan"]), [Path(r) for r in batch["runs"]])
        if report["task"] != batch["task"]:
            raise ValueError("Registry task mismatch")
        if report["meets_target"]:
            completed = max((Path(r) / "results.json").stat().st_mtime for r in batch["runs"])
            accepted[batch["task"]] = {"completed_at_unix": completed, "report": report}
    benchmark_accepted = dict(accepted)
    user_accepted = user_acceptances(registry, curriculum)
    for task, record in user_accepted.items():
        if task not in accepted: accepted[task] = record
    two_scene = two_scene_acceptances(registry, curriculum)
    for task, record in two_scene.items():
        if task not in accepted: accepted[task] = record
    starts = {}
    for launch in a.artifacts.glob("*/launch-record.json"):
        record = read(launch)
        started = datetime.fromisoformat(record["started_at"]).timestamp()
        starts[record["task"]] = min(starts.get(record["task"], float("inf")), started)
    for provenance in a.artifacts.glob("*/seed-*/provenance.json"):
        task = read(provenance)["task"]
        starts[task] = min(starts.get(task, float("inf")), provenance.stat().st_mtime)
    parallel_groups = config.get('authorized_parallel_groups', [])
    capacity=config.get('rolling_parallelism',{}).get('max_unfinished_tasks')
    rows = ordering_rows(curriculum, accepted, starts, parallel_groups,capacity)
    report = {"tasks": rows, "accepted_batches": accepted, 'authorized_parallel_groups': parallel_groups, "ordering_verified_for_observed_starts": not any(r["preceding_task_violations"] for r in rows), "all_tasks_accepted": len(accepted) == len(curriculum), "limitations": "Uses local filesystem timestamps, explicit parallel authorization and declared batch registry. Starts use launch records or simulator provenance; attempts lacking both remain outside ordering attribution. Parallel authorization never establishes task acceptance."}
    report["accepted_batches"] = benchmark_accepted
    report["user_accepted_tasks"] = user_accepted
    report["two_scene_accepted_tasks"] = two_scene
    report["accepted_task_count"] = len(accepted)
    a.output.write_text(json.dumps(report, indent=2))
    print(json.dumps({"accepted": len(accepted), "ordering_verified": report["ordering_verified_for_observed_starts"], "all_tasks_accepted": report["all_tasks_accepted"]}))


if __name__ == "__main__":
    main()
