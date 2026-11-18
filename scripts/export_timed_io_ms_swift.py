"""Export completed timed 250 Hz episodes using the preceding Swift serialization."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import shutil

from PIL import Image

import export_io_variant_ms_swift as prior

SERIALIZATION = prior.SERIALIZATION.replace(
    'Timing remains 25 Hz.',
    'steps and settle count 250 Hz control ticks (4 ms each). The observation and\n'
    'native task clock remain 25 Hz (40 ms per task period, ten control ticks).\n'
    'Keep the original requested steps even when tracking or task termination\n'
    'stopped execution early; actual execution is recorded in the tool feedback.')


def validate_arguments(text):
    value = json.loads(text, parse_float=Decimal)
    prior.require(type(value['steps']) is int and 1 <= value['steps'] <= 2500,
                  'Invalid timed control duration')
    # Reuse the preceding format checks without applying its 20-tick duration cap.
    bounded = dict(value, steps=1)
    prior.validate_arguments(json.dumps(bounded, default=lambda v: float(v)))
    prior.require(prior.precise(value), 'Action precision exceeds four decimal places')


def normalized_numbers(value):
    if isinstance(value, Decimal):
        return value.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    if isinstance(value, list):
        return [normalized_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: normalized_numbers(item) for key, item in value.items()}
    return value


def load_source(run):
    results = prior.read(run / 'results.json')
    prior.require(len(results) == 1, 'Expected one completed episode')
    result = results[0]
    layout = result['seed_index']
    native = result['official_observation']
    episode, model = run / f'seed-{layout}', run / f'policy-{layout}/model'
    timing = prior.read(episode / 'control-timing.json')
    launch = prior.read(run / 'launch-record.json')
    gpu = prior.read(episode / 'gpu-selection.json')
    policy = prior.read(run / f'policy-{layout}/policy-result.json')
    prior.require(result['status'] == policy['status'] == 'native_end'
                  and native['native_terminal'] and native['valid_sample']
                  and not native['unstable'], 'Expected a valid native terminal result')
    prior.require(result['policy_returncode'] == result['simulator_returncode'] == 0
                  and result['release_unchanged'], 'Runtime failed or source changed')
    prior.require(launch['io_variant'] == 'astra_io_250hz_timed'
                  and timing['protocol'] == launch['timing_protocol'] == 'timed_joint_v1'
                  and timing['action_hz'] == launch['action_hz'] == 250
                  and timing['observation_hz'] == launch['observation_hz'] == 25
                  and timing['physics_hz'] == 250 and timing['physics_steps_per_action'] == 1
                  and timing['control_ticks_per_task_step'] == 10
                  and timing['action_interval_seconds'] == 0.004
                  and timing['task_step_seconds'] == 0.04
                  and timing['max_action_steps'] == 2500, 'Timed control protocol mismatch')
    prior.require(native['step_unit'] == '250 Hz control ticks'
                  and native['steps'] == result['action_count'] == policy['steps']
                  and native['task_steps'] == native['steps'] // 10
                  and math.isclose(native['elapsed_simulation_seconds'], native['steps'] / 250),
                  'Native timing or action count mismatch')
    prior.require(gpu['ISAACSIM_GPU'] == str(result['gpu'])
                  and gpu['env_gpu'] == gpu['robodojo_device_id'] == result['gpu']
                  and gpu['isaac_launcher_device'] == f"cuda:{result['gpu']}"
                  and not gpu['cuda_visible_devices_present'], 'Native GPU selection mismatch')
    tools = prior.read(model / 'native-tools.json')
    prior.require(tools['enabled_tools'] == ['control_arms']
                  and [item['name'] for item in tools['tools']] == ['control_arms'], 'Unexpected tools')
    tool = tools['tools'][0]
    prior.require(tool['parameters']['properties']['steps']['maximum'] == 2500
                  and 'phase' not in tool['parameters']['properties'], 'Wrong action schema')
    actions_path, commands_path = episode / 'actions.jsonl', run / f'policy-{layout}/commands.jsonl'
    actions, commands = prior.jsonl(actions_path), prior.jsonl(commands_path)
    paths = sorted(model.glob('*.request.json'))
    prior.require(len(paths) == policy['model_calls'] == result['policy']['model_calls']
                  == len(commands) - 1 and bool(paths), 'Decision count mismatch')
    prior.require(len(actions) == native['steps']
                  and [item['step'] for item in actions] == list(range(1, len(actions) + 1)),
                  'Action sequence mismatch')
    prior.require(commands[0]['command']['op'] == 'observe' and commands[0]['step'] == 0,
                  'Missing initial observation')
    files = {run / 'results.json', run / 'launch-record.json', run / 'layout-provenance.json',
             episode / 'control-timing.json', episode / 'gpu-selection.json',
             episode / 'runner-result.json', run / f'policy-{layout}/policy-result.json',
             run / f'source-{layout}.json', model / 'native-tools.json', actions_path, commands_path}
    prior.require(prior.read(episode / 'runner-result.json') == native, 'Native result mismatch')
    previous, end, records = None, 0, []
    for position, path in enumerate(paths, 1):
        stem = path.name.removesuffix('.request.json')
        related = {name: model / f'{stem}.{name}.json' for name in
                   ('request', 'response', 'response.raw', 'tool-result',
                    'api-input.readable', 'output-normalization')}
        request, call = prior.read(path), prior.call_from(related['response'])
        raw_call, result_call = prior.call_from(related['response.raw']), prior.read(related['tool-result'])
        files.update(related.values())
        validate_arguments(call['arguments'])
        raw_values = json.loads(raw_call['arguments'], parse_float=Decimal)
        values = json.loads(call['arguments'], parse_float=Decimal)
        prior.require(normalized_numbers(raw_values) == values, 'Unexpected raw/effective label changes')
        normalization = prior.read(related['output-normalization'])
        prior.require(normalization['arguments_changed'] == (raw_values != values), 'Normalization audit mismatch')
        context, actual = json.loads(request['question']), json.loads(result_call['output'])
        prior.require(request['step'] == context['step'] == end and request['action_hz'] == 250
                      and request['tools'] == ['control_arms'] and context['control_timing'] == timing,
                      'Request/episode timing mismatch')
        budget = context['budget']
        prior.require(budget['total_control_ticks'] == budget['total_native_steps'] * 10
                      and budget['executed_control_ticks'] == end
                      and budget['executed_native_steps'] == end // 10
                      and budget['remaining_control_ticks'] == budget['total_control_ticks'] - end
                      and budget['task_horizon_seconds'] == native['task_horizon_seconds']
                      and math.isclose(budget['elapsed_simulation_seconds'], end / 250), 'Budget mismatch')
        images = request['images']
        prior.require([item['name'] for item in images] == list(prior.CAMERAS), 'Unexpected camera order')
        head = Path(images[0]['path'])
        frame = head.with_name(head.name.removesuffix('-cam_head.jpg') + '.json')
        frame_data = prior.read(frame)
        files.add(frame)
        prior.require(frame_data['step'] == end and frame_data['state'] == context['state']
                      and frame_data['instruction'] == context['instruction'], 'Input observation mismatch')
        prior.require(result_call['type'] == 'function_call_output'
                      and result_call['call_id'] == call['call_id'], 'Feedback/call mismatch')
        args = json.loads(call['arguments'])
        command = commands[position]
        executed = actual['feedback']['executed_steps']
        expected_action = dict(arms={arm: {key: value for key, value in args[arm].items() if value is not None}
                                     for arm in ('left', 'right') if args[arm] is not None},
                               steps=args['steps'], settle=args['settle'])
        prior.require(command['command']['op'] == 'control_action'
                      and command['command']['action'] == expected_action, 'Executed goal differs from label')
        prior.require(command['step'] == actual['step'] == end + executed
                      and 0 <= executed <= args['steps'] + args['settle'], 'Execution boundary mismatch')
        prior.require(all(item['command'] == command['command']['id'] for item in actions[end:actual['step']]),
                      'Actions do not belong to this decision')
        expected_input = [] if previous is None else [prior.readable_observation(previous['request']),
                                                      previous['call'], previous['result']]
        expected_input.append(prior.readable_observation(request))
        actual_input = [item for item in prior.read(related['api-input.readable'])['input']
                        if item.get('type') != 'reasoning']
        prior.require(actual_input == expected_input, f'Complete actual history mismatch: {stem}')
        prior.require(request['replayed_call_id'] == (previous['call']['call_id'] if previous else None),
                      'Previous cycle identity mismatch')
        for image in images:
            source = Path(image['path'])
            prior.require(prior.digest(source) == image['sha256'], f'Source image changed: {source}')
            files.add(source)
        record = dict(layout=layout, stem=stem, request=request, call=call, raw_call=raw_call,
                      result=result_call, previous=previous, run=run, paths=related,
                      start=end, end=actual['step'], episode_success=results[0]['success'],
                      episode_score=native['score'], tool=tool)
        records.append(record)
        previous, end = record, actual['step']
    prior.require(end == native['steps'], 'Unmatched final execution boundary')
    prior.require(json.loads(records[-1]['result']['output'])['ended'], 'Final feedback is not terminal')
    return results[0], records, {str(path): prior.digest(path) for path in sorted(files)}


def training_system(record):
    source = record['request']['system']
    prior.require(source.count(prior.NATIVE_PREFIX) == 1, 'Unknown source instructions')
    return source.replace(prior.NATIVE_PREFIX, prior.TEXT_PREFIX, 1) + SERIALIZATION + prior.compact(record['tool'])


def sample_for(output, record):
    sections, images = [], []
    previous = record['previous']
    if previous is None:
        sections.append('Previous tool cycle: unavailable (first decision of this episode).')
    else:
        text, paths = prior.observation_block('preceding_user_message', output, previous)
        sections.extend([text, '[preceding_function_call]\n' + prior.compact(previous['call'])
                         + '\n[/preceding_function_call]', '[preceding_function_call_output]\n'
                         + prior.compact(previous['result']) + '\n[/preceding_function_call_output]'])
        images.extend(paths)
    text, paths = prior.observation_block('current_user_message', output, record)
    sections.append(text)
    images.extend(paths)
    return dict(messages=[dict(role='system', content=training_system(record)),
                          dict(role='user', content='\n\n'.join(sections)),
                          dict(role='assistant', content=record['call']['arguments'])], images=images)


def load_sources(runs):
    prior.require(bool(runs) and len(set(runs)) == len(runs), 'Use unique source runs')
    results, records, hashes, layouts = [], [], {}, set()
    for run in runs:
        result, current, current_hashes = load_source(run)
        prior.require(result['task'] == 'general_pickup' and result['seed_index'] not in layouts,
                      'Use one general_pickup episode per layout')
        layouts.add(result['seed_index'])
        results.append(result)
        records.extend(current)
        hashes.update(current_hashes)
    return results, records, hashes


def export(run, output):
    return export_runs([run], output)


def export_runs(runs, output):
    results, records, hashes = load_sources(runs)
    output.mkdir(parents=True, exist_ok=False)
    samples, indices, raw = [], [], []
    for line, record in enumerate(records, 1):
        for image in record['request']['images']:
            target = prior.image_path(output, record, image)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(image['path'], target)
        samples.append(sample_for(output, record))
        indices.append(prior.index_for(line, record))
        raw.append(dict(line=line, raw_function_call=record['raw_call'],
                        effective_function_call=record['call'], tool_result=record['result']))
    for name, rows in [('train', samples), ('all', samples), ('index', indices), ('raw-outputs', raw)]:
        (output / f'{name}.jsonl').write_text(''.join(prior.compact(row) + '\n' for row in rows))
    prior.save(output / 'example-first.json', samples[0])
    followup = next((sample for sample, record in zip(samples, records) if record['previous']), None)
    if followup is not None:
        prior.save(output / 'example-with-history.json', followup)
    (output / 'source-system.txt').write_text(records[0]['request']['system'])
    (output / 'training-system.txt').write_text(training_system(records[0]))
    manifest = dict(schema_version=1, format='ms-swift messages + images JSONL', source_runs=list(map(str, runs)),
                    created_at=datetime.now(timezone.utc).isoformat(), output=str(output), documentation=prior.DOC,
                    samples=len(records), per_layout=dict(Counter(record['layout'] for record in records)),
                    image_files=sum(len(r['request']['images']) for r in records),
                    image_references=sum(len(s['images']) for s in samples),
                    action_hz=250, observation_hz=25, physics_hz=250, timing_protocol='timed_joint_v1',
                    step_unit='250 Hz control ticks', task_step_unit='25 Hz task periods',
                    labels='exact effective function_call.arguments strings; requested durations preserved',
                    history='complete preceding observation, function call and result in named user blocks',
                    system_adaptation='native function invocation changed to JSON text; skills retained; schema appended',
                    encrypted_reasoning_exported=False, source_sha256=hashes,
                    exporter_sha256=prior.digest(Path(__file__)),
                    serialization_helpers_sha256=prior.digest(Path(prior.__file__)),
                    native_results={str(result['seed_index']): result['official_observation'] for result in results},
                    validation_split='none; complete episodes in train.jsonl')
    if len(runs) == 1:
        manifest.update(source_run=str(runs[0]), native_result=results[0]['official_observation'])
    prior.save(output / 'manifest.json', manifest)
    summary = '；'.join(f"L{result['seed_index']}：{result['official_observation']['score']:g} 分，"
                       f"{sum(r['layout'] == result['seed_index'] for r in records)} 条" for result in results)
    (output / 'README.md').write_text(
        '# general_pickup 250 Hz 定时控制：Swift 训练数据\n\n'
        f'{summary}。共 {len(records)} 条模型决策。'
        '每行同时包含完整输入与对应输出，沿用之前的 messages + images JSONL 格式。\n\n'
        '- train.jsonl / all.jsonl：相同的完整数据；训练时选择其中一个文件。\n'
        '- messages[0] 为 system，messages[1] 为 user 输入，messages[2].content 为实际执行的参数 JSON 标签。\n'
        '- images/：复制原始三路 JPEG；首轮 3 张图，后续上一轮与本轮各 3 张，共 6 张。\n'
        '- example-first.json / example-with-history.json：可读输入输出样例。\n'
        '- index.jsonl：决策边界、实际执行量、反馈状态、来源与成绩。\n'
        '- raw-outputs.jsonl：原始响应调用、实际执行调用及对应反馈。\n'
        '- manifest.json / validation.json：来源哈希和完整校验结果。\n\n'
        '上一轮观察、memory 所在的调用和执行反馈完整保留在命名历史区；'
        'history_references、原始 context 字符串和输入测量精度均保留。'
        '加密推理不进入训练内容，当前执行反馈和最终成绩不添加到当前输入。\n\n'
        'assistant.content 逐字符保留实际执行响应的 arguments，包括 assessment、plan、memory、'
        'left、right、steps、settle，无 phase；保留 null 和连续夹爪值。'
        '全部决策均保留，跟踪保护中断和提前成功时也不将请求 steps 改写为实际执行量。\n\n'
        '**steps 与 settle 均为 250 Hz 控制周期，每周期 4 ms；任务/观测保持 25 Hz，'
        '每任务步 40 ms。每局预算 200 任务步 = 2,000 控制周期 = 8 秒。**'
        'system 中仅将原生工具调用改为 JSON 文本输出并补充序列化说明与原 schema，保留原技能和控制约束。\n\n'
        '图片使用绝对路径，迁移数据时需同步修改；未随机拆分相邻决策。训练参数示例：\n\n'
        f'```bash\n--dataset {output / "train.jsonl"} --split_dataset_ratio 0\n```\n\n'
        '已校验 JSONL、原图解码与哈希、实际 API 历史、标签、执行命令和时间边界；'
        '未运行模型训练或模型专用模板编码。\n\n'
        f'[Swift 自定义数据文档]({prior.DOC})\n\n'
        '复核：\n\n```bash\n.venv-policy/bin/python -B scripts/export_timed_io_ms_swift.py '
        f'--validate-only --output {output}\n```\n')
    return manifest


def validate(output):
    manifest = prior.read(output / 'manifest.json')
    runs = [Path(run) for run in manifest.get('source_runs', [manifest.get('source_run')])]
    results, records, hashes = load_sources(runs)
    prior.require(hashes == manifest['source_sha256'], 'Source files changed since export')
    samples, all_samples = prior.jsonl(output / 'train.jsonl'), prior.jsonl(output / 'all.jsonl')
    indices, raw = prior.jsonl(output / 'index.jsonl'), prior.jsonl(output / 'raw-outputs.jsonl')
    prior.require(len(samples) == len(all_samples) == len(indices) == len(raw) == len(records)
                  == manifest['samples'] and samples == all_samples, 'Sample count or train/all mismatch')
    prior.require(manifest['action_hz'] == 250 and manifest['observation_hz'] == 25
                  and manifest['timing_protocol'] == 'timed_joint_v1', 'Dataset timing mismatch')
    files, references, label_hashes, statuses = set(), 0, [], Counter()
    for line, (sample, record, index, raw_row) in enumerate(zip(samples, records, indices, raw), 1):
        prior.require(sample == sample_for(output, record), f'Source input/label/history/image order mismatch: {line}')
        prior.require(index == prior.index_for(line, record), f'Index mismatch: {line}')
        prior.require(raw_row == dict(line=line, raw_function_call=record['raw_call'],
                                     effective_function_call=record['call'], tool_result=record['result']),
                      'Raw audit mismatch')
        prior.require(set(sample) == {'messages', 'images'}
                      and [item['role'] for item in sample['messages']] == ['system', 'user', 'assistant'],
                      'Invalid Swift message layout')
        prior.require(sum(item['content'].count('<image>') for item in sample['messages']) == len(sample['images'])
                      == (6 if record['previous'] else 3), 'Image placeholder/count mismatch')
        validate_arguments(sample['messages'][-1]['content'])
        sources = ([] if record['previous'] is None else record['previous']['request']['images']) + record['request']['images']
        for path, source in zip(sample['images'], sources):
            prior.require(Path(path).is_absolute() and prior.digest(path) == source['sha256'], 'Exported image mismatch')
            if path not in files:
                with Image.open(path) as picture:
                    prior.require(picture.format == 'JPEG' and list(picture.size) == source['size'], 'Invalid image')
                    picture.load()
                files.add(path)
            references += 1
        label_hashes.append(hashlib.sha256(sample['messages'][-1]['content'].encode()).hexdigest())
        statuses[index['execution_status']] += 1
    prior.require(len(files) == manifest['image_files'] and references == manifest['image_references'], 'Image totals mismatch')
    prior.require(prior.read(output / 'example-first.json') == samples[0], 'First example mismatch')
    followup = next((sample for sample, record in zip(samples, records) if record['previous']), None)
    if followup is not None:
        prior.require(prior.read(output / 'example-with-history.json') == followup, 'History example mismatch')
    prior.require((output / 'source-system.txt').read_text() == records[0]['request']['system']
                  and (output / 'training-system.txt').read_text() == training_system(records[0]), 'System text mismatch')
    report = dict(passed=True, validated_at=datetime.now(timezone.utc).isoformat(), samples=len(samples),
                  per_layout=manifest['per_layout'], image_files=len(files), image_references=references,
                  complete_history_samples=sum(record['previous'] is not None for record in records),
                  episodes=len(results), execution_status_counts=dict(statuses),
                  action_hz=250, observation_hz=25, physics_hz=250, timing_protocol='timed_joint_v1',
                  exact_source_labels=True, original_context_strings=True, original_input_precision=True,
                  actual_api_history_checked=True, executed_goals_match_labels=True,
                  requested_durations_preserved=True, all_images_decoded=True, source_hashes_unchanged=True,
                  current_execution_result_not_in_current_input=True, current_or_future_reward_not_added=True,
                  ms_swift_model_template_tested=False,
                  native_results={str(result['seed_index']): result['official_observation'] for result in results},
                  label_sha256=label_hashes, dataset_sha256={name: prior.digest(output / name) for name in
                      ('train.jsonl', 'all.jsonl', 'index.jsonl', 'raw-outputs.jsonl')})
    if len(results) == 1:
        report['native_result'] = results[0]['official_observation']
    prior.save(output / 'validation.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, action='append', help='Completed run; repeat to merge distinct layouts')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if not args.validate_only:
        prior.require(args.run is not None, '--run is required for export')
        export_runs([run.resolve() for run in args.run], output)
    report = validate(output)
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ('label_sha256', 'dataset_sha256')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
