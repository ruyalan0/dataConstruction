"""Ensure interrupted development needs matching terminal continuation evidence."""
import json
import tempfile
from pathlib import Path
from egg_harness.development_record import terminal_development
from report_development_continuations import summarize

with tempfile.TemporaryDirectory() as directory:
    run = Path(directory)
    episode = run / "seed-20"
    episode.mkdir()
    def save(path, value):
        path.write_text(json.dumps(value))
    save(run / "results.json", [{"seed_index": 20, "mode": "development", "status": "held_for_development_recovery", "action_count": 42}])
    original = (run / "results.json").read_bytes()
    save(episode / "provenance.json", {"task": "align_blocks"})
    save(episode / "runner-result.json", {"native_terminal": True, "score": 100, "steps": 99})
    save(episode / "exit-status.json", {"exit_code": 0})
    try:
        terminal_development(run, "align_blocks")
    except ValueError:
        pass
    else:
        raise AssertionError("Interrupted attempt accepted without continuation")
    continuation = {"kind": "development_continuation", "episode": str(episode), "native_terminal": True, "returncode": 0, "start_step": 42, "end_step": 99}
    save(run / "recovery-01.result.json", continuation)
    record = terminal_development(run, "align_blocks")
    assert record["continued"] and record["score"] == 100
    assert (run / "results.json").read_bytes() == original
    trace = run / "recovery-01"
    (trace / "model").mkdir(parents=True)
    save(trace / "model" / "0001.cost.json", {"seconds": 2.5, "usage": {"total_tokens": 123}})
    continuation.update(trace=str(trace), source_commit="new-capability", start_step=42, status="native_end")
    save(run / "recovery-01.result.json", continuation)
    report = summarize(run)
    assert report["original_status"] == "held_for_development_recovery"
    assert report["continuations"][0]["source_commit"] == "new-capability"
    assert report["continuations"][0]["api_tokens"] == 123
    assert report["validated_terminal_development"]["score"] == 100
    assert (run / "results.json").read_bytes() == original
    prior = {"kind": "development_continuation", "episode": str(episode), "start_step": 42, "end_step": 50, "native_terminal": False, "returncode": 1}
    save(run / "recovery-00.result.json", prior)
    continuation["start_step"] = 50
    save(run / "recovery-01.result.json", continuation)
    chain = terminal_development(run, "align_blocks")
    assert str(run / "recovery-00.result.json") in chain["evidence_sha256"], "Failed continuation omitted from pinned evidence"
    prior["end_step"] = 49
    save(run / "recovery-00.result.json", prior)
    try:
        terminal_development(run, "align_blocks")
    except ValueError:
        pass
    else:
        raise AssertionError("Gap in continuation chain accepted")
    prior["end_step"] = 50
    save(run / "recovery-00.result.json", prior)
    continuation["end_step"] = 98
    save(run / "recovery-01.result.json", continuation)
    try:
        terminal_development(run, "align_blocks")
    except ValueError:
        pass
    else:
        raise AssertionError("Mismatched continuation accepted")
print("Terminal continuation validation preserves original attempt and rejects mismatch")
