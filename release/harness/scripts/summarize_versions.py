"""Summarize immutable releases using completed native episode evidence."""
import hashlib
import json
from pathlib import Path
from audit_curriculum import summarize
from egg_harness.freeze import verify

rows = []
for release in sorted(Path('artifacts/releases').iterdir()):
    if not release.is_dir():
        continue
    manifest = verify(release)
    source_hash = hashlib.sha256(json.dumps(manifest['files'], sort_keys=True).encode()).hexdigest()
    episodes = []
    for result in sorted(Path('artifacts').glob('*/results.json')):
        episodes.extend(row for row in summarize(result.parent) if row['source_hash'] == source_hash)
    modes = {}
    for mode in ('development', 'frozen'):
        selected = [r for r in episodes if r['mode'] == mode]
        terminal = [r for r in selected if r['native_terminal'] and r['valid_sample']]
        modes[mode] = {
            'attempts': len(selected), 'terminal_episodes': len(terminal),
            'successes': sum(r['success'] for r in selected),
            'success_rate_all_attempts': sum(r['success'] for r in selected)/len(selected) if selected else None,
            'mean_score': sum(r['score'] for r in terminal)/len(selected) if selected and len(terminal)==len(selected) else None,
            'actions': sum(r['actions'] for r in selected),
            'api_calls': sum(r['api_calls_completed'] for r in selected),
            'api_tokens': sum(r['api_tokens'] for r in selected),
            'evidence': [r['run'] for r in selected]}
    rows.append({'version': release.name, 'commit': manifest['commit'], 'source_hash': source_hash, 'measurements': modes})
Path('artifacts/version-summary.json').write_text(json.dumps(rows, indent=2))
print(json.dumps(rows, indent=2))
