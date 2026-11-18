"""Summarize recorded API usage, retaining unaccounted and failed requests."""
import json
import hashlib
from pathlib import Path


def model_usage(folder):
    folder = Path(folder)
    requests = {p.name.removesuffix(".request.json"): p for p in folder.glob("*.request.json")}
    cost_paths = {p.name.removesuffix(".cost.json"): p for p in folder.glob("*.cost.json")}
    failure_paths = {p.name.removesuffix(".failure.json"): p for p in folder.glob("*.failure.json")}
    orphaned = (set(cost_paths) | set(failure_paths)) - set(requests)
    if orphaned:
        raise ValueError("Usage evidence without request: " + repr(sorted(orphaned)))
    damaged = []
    def read_records(paths):
        records = {}
        for identifier, path in sorted(paths.items()):
            raw = path.read_bytes()
            try:
                records[identifier] = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                damaged.append({"path": str(path), "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(), "error": str(error)})
        return records
    costs = read_records(cost_paths)
    failures = read_records(failure_paths)
    return {"requests": len(requests), "calls_with_usage": len(costs),
        "tokens": sum(c["usage"]["total_tokens"] for c in costs.values()),
        "api_seconds": sum(c["seconds"] for c in costs.values()),
        "failed_requests": len(failures),
        "damaged_evidence": damaged,
        "accounting_complete": not damaged and set(requests) == set(costs),
        "cost_scope": "Recorded usage only; missing or damaged usage makes totals a lower bound. Failure records do not imply zero billed usage.",
        "requests_without_usage": sorted(set(requests) - set(costs)),
        "unresolved_requests": sorted(set(requests) - set(costs) - set(failures)),
        "unresolved_meaning": "May be in flight or interrupted; inspect the controller process and terminal evidence."}
