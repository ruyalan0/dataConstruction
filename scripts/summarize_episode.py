"""Summarize measured evidence and request overhead without treating notes as success."""

import argparse
import json
from collections import Counter
from pathlib import Path


def longest_streak(values):
    longest = current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def summarize(output):
    traces = list((output / 'agent').glob('*/events.jsonl'))
    if len(traces) != 1:
        raise ValueError('Expected one policy episode')
    events = [json.loads(line) for line in traces[0].read_text().splitlines()]
    def data(kind):
        return [e['data'] for e in events if e['kind'] == kind]
    responses, decisions, feedback = data('response'), data('decision'), data('feedback')
    native = data('native_feedback')
    operations = [e for e in data('operation') if e['state'] == 'prepared']
    usage = [r['usage'] for r in responses if r.get('usage') is not None]
    manifest = traces[0].parent / 'requests.jsonl'
    requests = [json.loads(line) for line in manifest.read_text().splitlines()] if manifest.exists() else []
    repaired = pending_errors = 0
    for event in events:
        if event['kind'] == 'tool_error':
            pending_errors += 1
        elif (event['kind'] == 'operation' and event['data']['state'] == 'completed'
              and event['data'].get('outcome') != 'validation_error'):
            repaired += pending_errors
            pending_errors = 0
    holds = [all(d[a]['operation'] in {'hold', 'hold_current', 'hold_latched', 'latch'}
                 and d[a]['gripper'] == 'hold' for a in ('left', 'right'))
             if 'waypoints' not in d else False for d in decisions]
    low = [max(p['consecutive_low_progress_boundaries'] for p in f['progress'].values()) > 0 for f in native]
    config_path = traces[0].parent / 'harness-config.json'
    config = json.loads(config_path.read_text()) if config_path.exists() else None
    threshold = config['config']['stall_boundaries'] if config else None
    sustained = [max(p['consecutive_low_progress_boundaries'] for p in f['progress'].values()) >= threshold
                 for f in native] if threshold is not None else []
    names = [op['name'] for op in operations]
    summary = {
        'responses': len(responses), 'logical_requests': len(data('request_started')),
        'api_retries': len(data('api_retry')),
        'api_attempts': len(data('request_started')) + len(data('api_retry')),
        'decisions': len(decisions), 'native_actions': len(data('native_action')),
        'observations': len(data('observation')), 'operation_counts': dict(Counter(names)),
        'validation_errors': len(data('tool_error')), 'validation_errors_repaired': repaired,
        'validation_errors_unresolved': pending_errors,
        'pose_reached_segments': sum(f['pose_reached'] for f in feedback),
        'interrupted_segments': sum(f['interrupted'] for f in feedback),
        'low_progress_interruptions': sum(f.get('interruption_reason') == 'sustained_low_progress' for f in feedback),
        'native_feedback_boundaries': len(native),
        'low_progress_boundary_ratio': sum(low) / len(low) if low else None,
        'sustained_low_progress_boundary_ratio': sum(sustained) / len(sustained) if sustained else None,
        'stall_boundaries': threshold,
        'both_arm_hold_decisions': sum(holds), 'longest_hold_decision_streak': longest_streak(holds),
        'longest_recall_operation_streak': longest_streak([name == 'recall' for name in names]),
        'active_text_bytes_per_request': [r['active_text_bytes'] for r in requests],
        'max_active_text_bytes': max((r['active_text_bytes'] for r in requests), default=None),
        'distinct_images_per_request': [r['unique_images'] for r in requests],
        'evidence_metrics_available': bool(requests),
        'prompt_tokens': sum(u['prompt_tokens'] for u in usage),
        'completion_tokens': sum(u['completion_tokens'] for u in usage),
        'reasoning_tokens': sum((u.get('completion_tokens_details') or {}).get('reasoning_tokens', 0) or 0 for u in usage),
        'responses_with_token_usage': len(usage),
        'request_seconds': [e['seconds'] for e in data('request_finished')],
        'visual_context': data('visual_context'),
        'prompt_tokens_per_call': [u['prompt_tokens'] for u in usage],
        'response_models': dict(Counter(r['model'] for r in responses if r.get('model'))),
        'decisions_detail': decisions, 'feedback': feedback,
        'metrics_scope': 'Allowed robot observations only; hold/recall counts do not imply waste or task failure. '
                         'Missing legacy manifests/native feedback are reported as unavailable, not zero progress.',
    }
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    summary = summarize(parser.parse_args().output)
    print(json.dumps({k: v for k, v in summary.items() if not isinstance(v, list)}))
