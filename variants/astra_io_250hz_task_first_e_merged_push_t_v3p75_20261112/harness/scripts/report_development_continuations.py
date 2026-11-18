"""Report each live-development continuation separately from its original attempt."""
import argparse
import json
from pathlib import Path
from egg_harness.development_record import terminal_development


def summarize(run):
    run = Path(run).resolve()
    originals = json.loads((run / "results.json").read_text())
    if len(originals) != 1 or originals[0]["mode"] != "development":
        raise ValueError("Expected one development episode")
    original = originals[0]
    episode = run / ("seed-" + str(original["seed_index"]))
    task = json.loads((episode / "provenance.json").read_text())["task"]
    continuations = []
    for path in sorted(run.glob("*.result.json")):
        record = json.loads(path.read_text())
        if record.get("kind") != "development_continuation":
            continue
        if Path(record["episode"]).resolve() != episode:
            raise ValueError("Continuation belongs to another episode")
        trace = Path(record["trace"])
        if not trace.is_relative_to(run):
            raise ValueError("Continuation trace is outside run")
        costs = [json.loads(p.read_text()) for p in (trace / "model").glob("*.cost.json")]
        continuations.append({"record": str(path), "source_commit": record["source_commit"],
            "status": record.get("status"), "start_step": record["start_step"],
            "end_step": record.get("end_step"), "native_terminal": record.get("native_terminal", False),
            "api_calls": len(costs), "api_tokens": sum(c["usage"]["total_tokens"] for c in costs),
            "api_seconds": sum(c["seconds"] for c in costs), "returncode": record.get("returncode")})
    terminal = None
    rejection = None
    try:
        terminal = terminal_development(run, task)
    except (ValueError, FileNotFoundError) as error:
        rejection = str(error)
    return {"run": str(run), "task": task, "original_status": original["status"],
        "original_result_unchanged": str(run / "results.json"), "continuations": continuations,
        "validated_terminal_development": terminal, "terminal_rejection": rejection,
        "version_attribution": "Continuation uses its own source commit; never credit its result to the original frozen release."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = [summarize(run) for run in args.runs]
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
