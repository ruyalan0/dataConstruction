"""Export audited 25 Hz decisions, including their complete preceding tool cycle."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image

DOC = 'https://swift.readthedocs.io/en/latest/Customization/Custom-dataset.html'
CAMERAS = ('cam_head', 'cam_left_wrist', 'cam_right_wrist')
FIELDS = {'assessment', 'plan', 'memory', 'left', 'right', 'steps', 'settle'}
NATIVE_PREFIX = '''Use exactly one of the supplied native function tools for your next operation.
The action:{...} notation in the skills describes the existing operation semantics;
express that operation through the matching function's parameters, not a JSON text answer.
Include your brief assessment, plan and updated episode memory in the call.'''
TEXT_PREFIX = '''Return the JSON argument object for one control_arms operation as assistant text.
The action:{...} notation in the skills describes the existing operation semantics;
express that operation using the control_arms parameters as a JSON text answer.
Include your brief assessment, plan and updated episode memory in the JSON object.'''
SERIALIZATION = '''

Training serialization:
Output only the next control_arms argument JSON object, with no tool-call wrapper,
Markdown or extra prose. Preserve null holds, continuous gripper commands, steps,
settle, assessment, plan and memory. Do not emit phase. Timing remains 25 Hz.
The preceding complete tool cycle is serialized in the user message as named
blocks: [preceding_user_message], [preceding_function_call] and
[preceding_function_call_output]. The current observation is in
[current_user_message]. Resolve the original history_references against these
named blocks. Each observation block contains its original context followed by
its camera names and images in recorded order. The first decision has no prior
cycle. The preceding function call's arguments are a JSON-encoded string.
The function schema below defines the output argument object:
'''


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def call_from(path):
    response = read(path)
    require(response['status'] == 'completed', f'Incomplete response: {path}')
    require(all(item['type'] in ('reasoning', 'function_call') for item in response['output']),
            f'Unmapped response content: {path}')
    calls = [item for item in response['output'] if item['type'] == 'function_call']
    require(len(calls) == 1 and calls[0]['name'] == 'control_arms'
            and calls[0]['status'] == 'completed', f'Expected one control_arms call: {path}')
    return calls[0]


def readable_observation(request):
    parts = [{'type': 'input_text', 'text': request['question']}]
    for item in request['images']:
        parts.extend([{'type': 'input_text', 'text': item['name']},
                      {'type': 'input_image', 'detail': 'high', 'image_sha256': item['sha256']}])
    return {'role': 'user', 'content': parts}


def precise(value):
    if isinstance(value, Decimal):
        return value.is_finite() and value.as_tuple().exponent >= -4
    if isinstance(value, dict):
        return all(precise(item) for item in value.values())
    if isinstance(value, list):
        return all(precise(item) for item in value)
    return True


def validate_arguments(text):
    arguments = json.loads(text, parse_float=Decimal)
    require(set(arguments) == FIELDS, 'Unexpected action fields; no phase or arms conversion is allowed')
    require(precise(arguments), 'Action precision exceeds four decimal places')
    require(type(arguments['steps']) is int and 1 <= arguments['steps'] <= 20, 'Invalid requested steps')
    require(type(arguments['settle']) is int and 0 <= arguments['settle'] <= 2, 'Invalid settle')
    for name in ('assessment', 'plan', 'memory'):
        require(isinstance(arguments[name], str) and arguments[name].strip(), f'Empty {name}')
    for name in ('left', 'right'):
        arm = arguments[name]
        if arm is None:
            continue
        require(set(arm) == {'control', 'joints', 'gripper'} and arm['control'] == 'joint', 'Invalid arm')
        joints = arm['joints']
        require(joints is None or (isinstance(joints, list) and len(joints) == 6
                and all(type(v) in (int, Decimal) for v in joints)), 'Invalid joint targets')
        gripper = arm['gripper']
        require(gripper is None or (type(gripper) in (int, Decimal) and 0 <= gripper <= 1), 'Invalid gripper')


def load_source(batch):
    manifest = read(batch / 'batch.json')
    require(manifest['status'] == 'completed' and manifest['variant'] == 'astra_io_25hz'
            and manifest['enabled_tools'] == ['control_arms'] and manifest['action_hz'] == 25,
            'Expected a completed, audited 25 Hz control-only batch')
    files = {batch / 'batch.json', batch / 'summary.json'}
    records = []
    for episode in manifest['episodes']:
        run, layout = Path(episode['output']), episode['layout']
        native = read(run / 'results.json')[0]
        audit = read(run / 'io-audit.json')
        timing = read(run / f'seed-{layout}/control-timing.json')
        require(episode['audit_passed'] and audit['passed'] and native['status'] == 'native_end'
                and native['official_observation']['valid_sample']
                and not native['official_observation']['unstable'], f'Invalid episode: L{layout}')
        require(timing['action_hz'] == 25 and timing['physics_hz'] == 250
                and timing['physics_steps_per_action'] == 10, 'Actual timing mismatch')
        model = run / f'policy-{layout}/model'
        tool = read(model / 'native-tools.json')['tools']
        require(len(tool) == 1 and tool[0]['name'] == 'control_arms', 'Unexpected tools')
        files.update([run / 'results.json', run / 'io-audit.json', run / 'launch-record.json',
                      run / 'layout-provenance.json', run / f'seed-{layout}/control-timing.json',
                      model / 'native-tools.json'])
        actions_path, commands_path = run / f'seed-{layout}/actions.jsonl', run / f'policy-{layout}/commands.jsonl'
        actions, commands = jsonl(actions_path), jsonl(commands_path)
        files.update([actions_path, commands_path])
        require(len(actions) == native['action_count']
                and [a['step'] for a in actions] == list(range(1, len(actions) + 1)), 'Action sequence mismatch')
        paths = sorted(model.glob('*.request.json'))
        require(len(paths) == native['policy']['model_calls'] == len(commands) - 1, 'Decision count mismatch')
        previous, end = None, 0
        for position, path in enumerate(paths, 1):
            stem = path.name.removesuffix('.request.json')
            related = {name: model / f'{stem}.{name}.json' for name in
                       ('request', 'response', 'response.raw', 'tool-result', 'api-input.readable')}
            request, call = read(path), call_from(related['response'])
            raw_call, result = call_from(related['response.raw']), read(related['tool-result'])
            files.update(related.values())
            validate_arguments(call['arguments'])
            require(json.loads(raw_call['arguments']) == json.loads(call['arguments']),
                    'This export requires raw and effective action values to match')
            context, actual = json.loads(request['question']), json.loads(result['output'])
            require(request['step'] == context['step'] == end and request['action_hz'] == 25
                    and context['control_timing'] == timing, 'Request/episode timing mismatch')
            obs = run / f'seed-{layout}/frames' / ('initial.json' if end == 0 else f'step-{end:04d}.json')
            files.add(obs)
            require(read(obs)['state'] == context['state'] and read(obs)['instruction'] == context['instruction'],
                    'Input observation mismatch')
            require(result['type'] == 'function_call_output' and result['call_id'] == call['call_id'],
                    'Feedback/call mismatch')
            command = commands[position]
            executed = actual['feedback']['executed_steps']
            require(command['step'] == actual['step'] == end + executed
                    and 0 <= executed <= json.loads(call['arguments'])['steps'] + json.loads(call['arguments'])['settle'],
                    'Decision execution boundaries mismatch')
            require(all(a['command'] == command['command']['id'] for a in actions[end:actual['step']]),
                    'Actions do not belong to the matched decision')
            expected_input = [] if previous is None else [readable_observation(previous['request']),
                                                          previous['call'], previous['result']]
            expected_input.append(readable_observation(request))
            actual_input = [item for item in read(related['api-input.readable'])['input']
                            if item.get('type') != 'reasoning']
            require(actual_input == expected_input, f'Complete actual history mismatch: L{layout}/{stem}')
            require(request['replayed_call_id'] == (previous['call']['call_id'] if previous else None),
                    'Previous cycle identity mismatch')
            require([item['name'] for item in request['images']] == list(CAMERAS), 'Unexpected camera order')
            for image in request['images']:
                source = Path(image['path'])
                require(digest(source) == image['sha256'], f'Source image changed: {source}')
                files.add(source)
            record = dict(layout=layout, stem=stem, request=request, call=call, raw_call=raw_call,
                          result=result, previous=previous, run=run, paths=related,
                          start=end, end=actual['step'], episode_success=native['success'],
                          episode_score=native['official_observation']['score'], tool=tool[0])
            records.append(record)
            previous, end = record, actual['step']
        require(end == native['action_count'], 'Unmatched final execution boundary')
    return manifest, records, {str(path): digest(path) for path in sorted(files)}


def training_system(record):
    source = record['request']['system']
    require(source.count(NATIVE_PREFIX) == 1, 'Unknown source instructions; review serialization')
    return source.replace(NATIVE_PREFIX, TEXT_PREFIX, 1) + SERIALIZATION + compact(record['tool'])


def image_path(output, record, image):
    return output / 'images/general_pickup' / f"L{record['layout']}" / f"{record['stem']}-{image['name']}.jpg"


def observation_block(label, output, record):
    lines = [f'[{label}]', record['request']['question']]
    images = []
    for item in record['request']['images']:
        lines.append(item['name'] + ': <image>')
        images.append(str(image_path(output, record, item)))
    lines.append(f'[/{label}]')
    return '\n'.join(lines), images


def sample_for(output, record):
    sections, images = [], []
    previous = record['previous']
    if previous is None:
        sections.append('Previous tool cycle: unavailable (first decision of this episode).')
    else:
        text, paths = observation_block('preceding_user_message', output, previous)
        sections.extend([text, '[preceding_function_call]\n' + compact(previous['call'])
                         + '\n[/preceding_function_call]',
                         '[preceding_function_call_output]\n' + compact(previous['result'])
                         + '\n[/preceding_function_call_output]'])
        images.extend(paths)
    text, paths = observation_block('current_user_message', output, record)
    sections.append(text)
    images.extend(paths)
    return {'messages': [{'role': 'system', 'content': training_system(record)},
                         {'role': 'user', 'content': '\n\n'.join(sections)},
                         {'role': 'assistant', 'content': record['call']['arguments']}], 'images': images}


def index_for(line, record):
    arguments, actual = json.loads(record['call']['arguments']), json.loads(record['result']['output'])
    return dict(line=line, layout=record['layout'], decision=int(record['stem']),
                observation_step=record['start'], previous_observation_step=record['previous']['start']
                if record['previous'] else None, end_step=record['end'],
                requested_steps=arguments['steps'], requested_settle=arguments['settle'],
                executed_steps=actual['feedback']['executed_steps'], execution_status=actual['feedback']['status'],
                episode_success=record['episode_success'], episode_score=record['episode_score'],
                source_run=str(record['run']), sources={name: str(path) for name, path in record['paths'].items()})


def export(batch, output):
    source, records, hashes = load_source(batch)
    output.mkdir(parents=True, exist_ok=False)
    samples, indices, raw = [], [], []
    for line, record in enumerate(records, 1):
        for image in record['request']['images']:
            target = image_path(output, record, image)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(image['path'], target)
        samples.append(sample_for(output, record))
        indices.append(index_for(line, record))
        raw.append(dict(line=line, raw_function_call=record['raw_call'],
                        effective_function_call=record['call'], tool_result=record['result']))
    for name, rows in [('train', samples), ('all', samples), ('index', indices), ('raw-outputs', raw)]:
        (output / f'{name}.jsonl').write_text(''.join(compact(row) + '\n' for row in rows))
    save(output / 'example-first.json', samples[0])
    followup = next(sample for sample, record in zip(samples, records) if record['previous'])
    save(output / 'example-with-history.json', followup)
    (output / 'source-system.txt').write_text(records[0]['request']['system'])
    (output / 'training-system.txt').write_text(training_system(records[0]))
    counts = Counter(record['layout'] for record in records)
    manifest = dict(schema_version=1, format='ms-swift messages + images JSONL', source_batch=str(batch),
                    created_at=datetime.now(timezone.utc).isoformat(), output=str(output), documentation=DOC,
                    samples=len(records), per_layout=dict(counts), image_files=sum(len(r['request']['images']) for r in records),
                    image_references=sum(len(s['images']) for s in samples), action_hz=25, physics_hz=250,
                    labels='exact effective function_call.arguments strings; raw values identical',
                    history='complete preceding observation, function call and result serialized into named user blocks',
                    system_adaptation='native function invocation changed to JSON text; original skills retained; schema appended',
                    encrypted_reasoning_exported=False, source_sha256=hashes,
                    validation_split='none; all three complete episodes exported to train.jsonl')
    save(output / 'manifest.json', manifest)
    (output / 'README.md').write_text(
        '# general_pickup 25 Hz：Swift 训练数据\n\n'
        f"本批次 L20/L21/L22 共 {len(records)} 条，每次模型决策一条；分别为 {dict(counts)}。\n\n"
        '- train.jsonl / all.jsonl：相同的完整数据；每行只有 messages、images。\n'
        '- example-first.json / example-with-history.json：首轮和带历史样本的可读示例。\n'
        '- images/：复制原图，JSONL 使用绝对路径；迁移时需同步修改路径。\n'
        '- index.jsonl：来源、决策边界和实际执行情况；评分只在此审计文件中。\n'
        '- raw-outputs.jsonl：原始及实际执行的函数调用、对应工具反馈。\n'
        '- manifest.json / validation.json：导出策略、来源哈希及核验结果。\n\n'
        '每条样本均为 system/user/assistant 三条消息。user 历史区完整保留上一轮观察、'
        '三路图片、control_arms 调用及实际反馈，随后放当前原始 context 和三路图片；'
        '首轮 3 张图，后续 6 张图，顺序与占位符一一对应。history_references 保留并指向命名历史区。'
        '所有原始 context 字符串和输入测量精度均保留。加密推理不进入训练内容。\n\n'
        'assistant.content 逐字符保留实际执行响应中的 arguments；包括 assessment、plan、memory、'
        'left、right、steps、settle，没有 phase。null 保持语义和连续夹爪值不变；'
        '不改为 arms 包装、不插值、不把请求步数改成实际完成步数。'
        '两段被保护截断的 L20 请求也保留，实际执行量见索引。\n\n'
        '动作频率 25 Hz，每步 40 ms；物理频率 250 Hz，每动作 10 个物理步。'
        '示例中的 250 Hz 控制说明不适用于这批标签。原始 system 的原生工具调用指令改成输出参数 JSON，'
        '其余技能和控制约束保留，附上当前 control_arms schema；两个 system 文本均随数据提供。\n\n'
        '未随机拆分相邻决策；训练时可使用 `--dataset /absolute/path/train.jsonl --split_dataset_ratio 0`。'
        '本次验证 JSONL、原图、历史回放、原始标签和时间边界；未启动模型训练或模型专用模板编码。\n\n'
        f'[Swift 自定义数据文档]({DOC})\n\n'
        '复核：`python scripts/export_io_variant_ms_swift.py --validate-only --output <数据目录>`。\n')
    return manifest


def validate(output):
    manifest = read(output / 'manifest.json')
    _, records, hashes = load_source(Path(manifest['source_batch']))
    require(hashes == manifest['source_sha256'], 'Source files changed since export')
    samples, all_samples = jsonl(output / 'train.jsonl'), jsonl(output / 'all.jsonl')
    indices, raw = jsonl(output / 'index.jsonl'), jsonl(output / 'raw-outputs.jsonl')
    require(len(samples) == len(all_samples) == len(indices) == len(raw) == len(records) == manifest['samples'],
            'Sample count mismatch')
    require(samples == all_samples, 'train/all mismatch')
    files, references = set(), 0
    label_hashes, status_counts = [], Counter()
    for line, (sample, record, index, raw_row) in enumerate(zip(samples, records, indices, raw), 1):
        require(sample == sample_for(output, record), f'Source input/label/history/image order mismatch: line {line}')
        require(index == index_for(line, record), f'Index mismatch: line {line}')
        require(raw_row == dict(line=line, raw_function_call=record['raw_call'],
                                effective_function_call=record['call'], tool_result=record['result']), 'Raw audit mismatch')
        require(set(sample) == {'messages', 'images'} and [m['role'] for m in sample['messages']]
                == ['system', 'user', 'assistant'], 'Invalid Swift message layout')
        require(sum(m['content'].count('<image>') for m in sample['messages']) == len(sample['images']),
                'Image placeholder mismatch')
        require(len(sample['images']) == (6 if record['previous'] else 3), 'Missing current or historical images')
        validate_arguments(sample['messages'][-1]['content'])
        image_sources = ([] if record['previous'] is None else record['previous']['request']['images']) + record['request']['images']
        for path, image in zip(sample['images'], image_sources):
            target = Path(path)
            require(target.is_absolute() and digest(target) == image['sha256'], 'Exported image content/path mismatch')
            if path not in files:
                with Image.open(path) as picture:
                    require(picture.format == 'JPEG' and list(picture.size) == image['size'], 'Invalid image format/size')
                    picture.load()
                files.add(path)
            references += 1
        label_hashes.append(hashlib.sha256(sample['messages'][-1]['content'].encode()).hexdigest())
        status_counts[index['execution_status']] += 1
    require(len(files) == manifest['image_files'] and references == manifest['image_references'], 'Image count mismatch')
    require(read(output / 'example-first.json') == samples[0], 'First example mismatch')
    require(read(output / 'example-with-history.json') == next(s for s, r in zip(samples, records) if r['previous']),
            'History example mismatch')
    require((output / 'source-system.txt').read_text() == records[0]['request']['system']
            and (output / 'training-system.txt').read_text() == training_system(records[0]), 'System text mismatch')
    report = dict(passed=True, validated_at=datetime.now(timezone.utc).isoformat(), samples=len(samples),
                  per_layout=manifest['per_layout'], image_files=len(files), image_references=references,
                  complete_history_samples=sum(r['previous'] is not None for r in records),
                  execution_status_counts=dict(status_counts), action_hz=25, physics_hz=250,
                  exact_source_labels=True, original_context_strings=True, original_input_precision=True,
                  actual_api_history_checked=True, all_images_decoded=True, source_hashes_unchanged=True,
                  current_execution_result_not_in_current_input=True, current_or_future_reward_not_added=True,
                  ms_swift_model_template_tested=False, label_sha256=label_hashes,
                  dataset_sha256={name: digest(output / name) for name in
                                  ('train.jsonl', 'all.jsonl', 'index.jsonl', 'raw-outputs.jsonl')})
    save(output / 'validation.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if not args.validate_only:
        require(args.batch is not None, '--batch is required for export')
        export(args.batch.resolve(), output)
    report = validate(output)
    print(json.dumps({k: v for k, v in report.items() if k not in ('label_sha256', 'dataset_sha256')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
