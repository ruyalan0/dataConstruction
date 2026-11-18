"""Report runs without promoting interrupted snapshots to evaluation scores."""
import argparse
import json
from pathlib import Path
from audit_curriculum import summarize
from egg_harness.usage import model_usage

def report(run):
    results = json.loads((run / 'results.json').read_text())
    rows = summarize(run)
    for row, original in zip(rows, results):
        row['model_usage'] = model_usage(run / ('policy-' + str(row['layout_index'])) / 'model')
        native = original.get("official_observation", {})
        row['nonterminal_snapshot_score'] = native.get('score') if not native.get("native_terminal") else None
        row["unaccepted_native_terminal_score"] = native.get("score") if native.get("native_terminal") and not row["native_terminal"] else None
    complete = bool(rows) and all(row['native_terminal'] and row['valid_sample'] for row in rows)
    return {'episodes': rows, 'successes': sum(row['success'] for row in rows), 'n': len(rows),
            'all_valid_native_terminal': complete,
            'mean_score': sum(row['score'] for row in rows) / len(rows) if complete else None,
            'total_actions': sum(row['actions'] for row in rows),
            'total_tokens': sum(row['api_tokens'] for row in rows),
            'total_wall_seconds': sum(row['wall_seconds'] for row in rows) if all(row['wall_seconds'] is not None for row in rows) else None,
            'scope': 'Per-run evidence only; frozen independence and generalization require separate audits. Continuations reported separately.'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = report(args.run)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
