"""Bound active text without promoting archived model hypotheses to current facts."""

import json


def text_bytes(messages):
    def text_only(value):
        if isinstance(value, dict):
            if value.get('type') == 'image_url':
                return {'type': 'image_url', 'content': '[image budget accounted separately]'}
            return {k: text_only(v) for k, v in value.items()}
        if isinstance(value, list):
            return [text_only(v) for v in value]
        return value
    return len(json.dumps(text_only(messages), ensure_ascii=False, separators=(',', ':')).encode())


def compact_history(messages, keep_messages=18, max_bytes=65536, ledger_bytes=8192, protected=()):
    systems, groups, ledger = [], [], []
    for message in messages:
        content = message.get('content')
        if message['role'] == 'system' or (isinstance(content, str) and content.startswith('{"current_working_state":')):
            systems.append(message)
            continue
        if isinstance(content, str) and content.startswith('{"evidence_ledger":'):
            ledger.extend(json.loads(content)['evidence_ledger'])
            continue
        if not groups or (message['role'] == 'user' and isinstance(content, list)):
            groups.append([])
        groups[-1].append(message)

    def evidence(group):
        records = []
        for message in group:
            content = message.get('content')
            if not isinstance(content, str) or not content.startswith('{'):
                continue
            payload = json.loads(content)
            if message['role'] == 'assistant' and payload.get('name') in {'act', 'trajectory'}:
                args = payload['arguments']
                records.append({'model_note_verbatim_unverified': args.get('note', ''),
                                'command': {k: v for k, v in args.items() if k != 'note'}})
            elif 'operation_result' in payload:
                result = payload['operation_result']
                if isinstance(result, dict) and 'pose_reached' in result:
                    records.append({'measured_execution': {k: result[k] for k in
                        ('before', 'after', 'pose_reached', 'interrupted', 'error_mm', 'error_deg') if k in result}})
        return records

    pinned = {i for i, group in enumerate(groups) if any(
        part is protected_part for m in group if isinstance(m.get('content'), list)
        for part in m['content'] for protected_part in protected)}
    keep, count = set(pinned), 0
    for i in reversed(range(len(groups))):
        if count < keep_messages or i >= len(groups) - 2:
            keep.add(i)
            count += len(groups[i])
    for i, group in enumerate(groups):
        if i not in keep:
            ledger.extend(evidence(group))

    def assemble():
        while ledger and text_bytes(ledger) > ledger_bytes:
            ledger.pop(0)
        notes = []
        if ledger:
            notes = [{'role': 'user', 'content': json.dumps({
                'evidence_ledger': ledger,
                'meaning': 'Bounded historical excerpts, not active state. Notes are unverified model hypotheses. '
                           'Use current_working_state for active claims; query history for omitted originals. '
                           'Pose convergence does not establish contact or task completion.'
            }, separators=(',', ':'))}]
        return systems + notes + [message for i in sorted(keep) for message in groups[i]]

    result = assemble()
    while text_bytes(result) > max_bytes:
        removable = sorted(keep - pinned - set(range(max(0, len(groups) - 2), len(groups))))
        if removable:
            i = removable[0]
            keep.remove(i)
            ledger.extend(evidence(groups[i]))
        elif ledger:
            ledger.pop(0)
        else:
            raise ValueError('Active context exceeds budget; required current views and latest operation retained')
        result = assemble()
    return result, max(0, len(messages) - len(result))
