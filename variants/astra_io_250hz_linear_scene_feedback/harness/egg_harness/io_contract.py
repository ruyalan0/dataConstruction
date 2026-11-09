"""Shared four-decimal model I/O and legacy history utilities."""
import base64
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
import math
from pathlib import Path

CONTROL_HZ = 250
DECIMAL_PLACES = 4
QUANTUM = Decimal('0.0001')
IMAGE_ALIASES = {
    'previous_head': 'cam_head',
    'previous_left_wrist': 'cam_left_wrist',
    'previous_right_wrist': 'cam_right_wrist',
}


def precision_metadata():
    return dict(numeric_input_decimal_places=DECIMAL_PLACES,
                numeric_output_decimal_places=DECIMAL_PLACES,
                numeric_rounding='ROUND_HALF_UP', numeric_float_format='fixed_4_decimal_places')


def _decimal4(value):
    if not math.isfinite(value):
        raise ValueError('Nonfinite model I/O number')
    decimal = Decimal(str(value))
    with localcontext() as context:
        context.prec = max(28, decimal.adjusted() + 1 + DECIMAL_PLACES)
        rounded = decimal.quantize(QUANTUM, rounding=ROUND_HALF_UP)
    return abs(rounded) if rounded == 0 else rounded


def round_numbers(value):
    """Copy structured data, rounding floats but preserving strings/integers."""
    if isinstance(value, float):
        return float(_decimal4(value))
    if isinstance(value, dict):
        return {key: round_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [round_numbers(item) for item in value]
    return value


def round_output(value):
    """Compatibility name: input and effective output use identical rounding."""
    return round_numbers(value)


def dumps_four_decimals(value):
    """Compact JSON with numeric float literals such as 0.4000, never strings."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(_decimal4(value), '.4f')
    if isinstance(value, (list, tuple)):
        return '[' + ','.join(dumps_four_decimals(item) for item in value) + ']'
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError('Model JSON object keys must be strings')
        return '{' + ','.join(json.dumps(key, ensure_ascii=False) + ':' + dumps_four_decimals(item)
                              for key, item in value.items()) + '}'
    raise TypeError('Unsupported model JSON value: ' + type(value).__name__)


def execution_phase(decision):
    """Keep the IPC envelope compatible; the goal executor has no phase bounds."""
    return 'transit'


def image_hashes(message):
    result, name = {}, None
    for item in message.get('content', []):
        if item['type'] == 'input_text':
            name = item['text']
        elif item['type'] == 'input_image':
            data = item['image_url'].split(',', 1)[1]
            result[name] = hashlib.sha256(base64.b64decode(data, validate=True)).hexdigest()
    return result


def deduplicate_observation(context, images, previous):
    """Keep the preceding tool cycle intact; remove only proven current duplicates.

    A missing or changed pair is preserved. This also makes a first request after
    reconnection self-contained when no complete previous cycle is available.
    """
    context, images = deepcopy(context), dict(images)
    report = dict(removed=[], retained_nonidentical=[], policy='preserve_preceding_complete_tool_cycle')
    if not previous:
        return context, images, report
    if previous[0].get('role') != 'user':
        raise ValueError('History must begin with the preceding observation')
    prior = json.loads(previous[0]['content'][0]['text'])
    calls = [item for item in previous if item.get('type') == 'function_call']
    results = [item for item in previous if item.get('type') == 'function_call_output']
    if len(calls) != 1 or len(results) != 1 or calls[0]['call_id'] != results[0]['call_id']:
        raise ValueError('Expected the preceding matched function call and result')
    arguments, result = json.loads(calls[0]['arguments']), json.loads(results[0]['output'])
    references = {}

    def remove(container, key, source, source_key, label, reference):
        if key not in container:
            return
        if source_key in source and container[key] == source[source_key]:
            del container[key]
            references[label] = reference
            report['removed'].append(label)
        else:
            report['retained_nonidentical'].append(label)

    old_state = context.get('previous_observation')
    if isinstance(old_state, dict):
        remove(old_state, 'state', prior, 'state', 'previous_observation.state',
               'preceding_user_message.state')
    remove(context, 'previous_arm_posture', prior, 'arm_posture', 'previous_arm_posture',
           'preceding_user_message.arm_posture')
    remove(context, 'memory', arguments, 'memory', 'memory', 'preceding_function_call.arguments.memory')
    remove(context, 'previous_result', result, 'feedback', 'previous_result',
           'preceding_function_call_output.feedback')
    hashes = image_hashes(previous[0])
    for alias, original in IMAGE_ALIASES.items():
        if alias not in images:
            continue
        digest = hashlib.sha256(Path(images[alias]).read_bytes()).hexdigest()
        if hashes.get(original) == digest:
            del images[alias]
            references[alias] = 'preceding_user_message.' + original
            report['removed'].append(alias)
        else:
            report['retained_nonidentical'].append(alias)
    if references:
        context['history_references'] = references
    return context, images, report


def readable_input(items):
    """Audit copy: replace inline image bytes with SHA-256, and encrypted reasoning with length."""
    result = deepcopy(items)
    for item in result:
        if item.get('type') == 'reasoning' and item.get('encrypted_content'):
            item['encrypted_content'] = '<encrypted content: %d characters>' % len(item['encrypted_content'])
        for part in item.get('content', []):
            if part.get('type') == 'input_image':
                data = base64.b64decode(part.pop('image_url').split(',', 1)[1], validate=True)
                part['image_sha256'] = hashlib.sha256(data).hexdigest()
    return result
