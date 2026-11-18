"""Audit the user's reference JSONL without rewriting it or requiring its RGB files."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def audit():
    path = ROOT / '4fps_val.jsonl'
    counts = Counter(); roles = Counter(); tasks = Counter(); history_counts = Counter(); issues = []
    images = 0; first_images = []; offsets = []; examples = {}
    for number, line in enumerate(path.open(), 1):
        row = json.loads(line)
        if [message['role'] for message in row['messages']] != ['system', 'user', 'assistant']:
            issues.append(dict(line=number, issue='roles'))
        user = row['messages'][1]['content']
        observations = []
        parts = re.split(r'Observation \d+: ', user)[1:]
        for part in parts:
            metadata, _, rest = part.partition('\n')
            value = json.loads(metadata)
            observations.append(value)
            roles[','.join(value['roles'])] += 1
            has_state = '\nState: ' in '\n' + rest
            if has_state != any(role in value['roles'] for role in ('current', 'previous_decision')):
                issues.append(dict(line=number, issue='endpoint state placement'))
        times = [value['relative_time_ms'] for value in observations]
        if times != sorted(times) or not times or times[-1] != 0 or min(times) < -5000:
            issues.append(dict(line=number, issue='chronology/window'))
        if user.count('<image>') != len(row['images']) or len(row['images']) != 3 * len(observations):
            issues.append(dict(line=number, issue='image count/order'))
        offsets.extend(times)
        counts[len(row['images'])] += 1
        history_counts[sum('history' in value['roles'] for value in observations)] += 1
        tasks[user.splitlines()[0]] += 1
        images += len(row['images'])
        if not first_images: first_images = row['images'][:3]
        key = 'first' if number == 1 else 'full-window' if len(row['images']) == 66 else None
        if key and key not in examples:
            examples[key] = number
            (HERE / 'examples' / f'reference-{key}.json').write_text(json.dumps(dict(
                scope='Exact reference row; remote image paths are not copied or verified',
                source=str(path), source_line=number, sample=row), ensure_ascii=False, indent=2) + '\n')
    result = dict(passed=not issues, source=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  rows=sum(counts.values()), image_references=images, tasks=dict(tasks),
                  image_count_distribution=dict(sorted(counts.items())), observation_roles=dict(roles),
                  history_observation_count_distribution=dict(sorted(history_counts.items())),
                  relative_time_ms_range=[min(offsets), max(offsets)],
                  maximum_observations=max(counts) // 3, issues=issues,
                  first_image_files_present=[Path(p).is_file() for p in first_images],
                  scope='Text structure only; image content and physical correctness not verified')
    (HERE / 'audit/4fps-reference.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


if __name__ == '__main__':
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
