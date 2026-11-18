"""Export one ms-swift example per original ASTRA control_arms response.

Assistant labels are the exact recorded function argument strings. Source runs
are read only; existing datasets must be backed up before replacing them.
"""

import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATCH = ROOT / 'runs/ten-tasks-control-only-20261014-001220'
DOC_URL = 'https://github.com/modelscope/ms-swift/blob/main/docs/source_en/Customization/Custom-dataset.md'
ARMS = ('left', 'right')
CAMERAS = (('cam_head', 'third_view'), ('cam_left_wrist', 'left_wrist'),
           ('cam_right_wrist', 'right_wrist'))
SCHEMA_VERSION = 2
SYSTEM = (
    'Control a bimanual ARX-5 robot. Return the JSON argument object for one '
    'control_arms operation. Include assessment, plan, phase, memory, left, right, '
    'steps and settle. Each non-null arm has control="joint", joints (six absolute '
    'angles in radians, or null to hold), and gripper (a continuous target from '
    '0 closed to 1 open, or null to retain its command). A null arm holds. '
    'Choose phase="transit" for a longer movement or phase="fine" for adjustment. '
    'steps is the requested number of native action intervals at 25 Hz '
    '(40 milliseconds per step): fine allows 1..6 and transit allows 1..20. '
    'settle is a separate 0..2 native-step hold after the movement. The simulator '
    'runs at 250 Hz, but steps and settle do not count its physics cycles. '
    'Execution guards may interrupt a requested segment; inspect actual previous '
    'feedback before choosing the next operation. Use the supplied observation '
    'context and named images, and update the explicit episode memory. '
    'Output the argument JSON directly as text.'
)
NO_MEMORY_SYSTEM = SYSTEM.replace('phase, memory, left', 'phase, left').replace(
    'context and named images, and update the explicit episode memory.',
    'context and named images. Episode memory and prior model conversation replay '
    'are disabled; do not emit a memory field.')
LEGACY_MEMORY_POLICY = dict(explicit_episode_memory=True, model_history_replay=True,
                            rgb_memory_enabled=False)
DATA_FILES = ('all.jsonl', 'train.jsonl', 'failed.jsonl', 'index.jsonl', 'raw-outputs.jsonl')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def rows(path):
    with Path(path).open() as stream:
        for line in stream:
            require(bool(line.strip()), f'Blank JSONL line in {path}')
            yield json.loads(line)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def scalar_yaml(path, name):
    values = re.findall(r'^\s*' + re.escape(name) + r':\s*([0-9.eE+-]+)\s*$',
                        path.read_text(), re.M)
    require(len(values) == 1, f'Expected exactly one {name} in {path}')
    return float(values[0])


def timing(root):
    config = root / 'dependencies/RoboDojo/env_cfg'
    dt = scalar_yaml(config / 'sim/sim_config.yml', 'dt')
    decimation = scalar_yaml(config / 'sim/sim_config.yml', 'decimation')
    native_hz = scalar_yaml(config / 'arx_x5.yml', 'collect_freq')
    require(math.isclose(1 / dt, 250) and decimation == 1 and native_hz == 25,
            'Expected the recorded 250 Hz physics / 25 Hz native action runtime')
    return {'control_hz': 250, 'source_native_action_hz': native_hz,
            'cycles_per_native_action': 10, 'native_step_duration_ms': 1000 / native_hz,
            'label_steps_unit': 'original 25 Hz native action intervals, unchanged',
            'source_sim_dt_seconds': dt, 'source_sim_decimation': decimation}


def observation(episode, step):
    tag = 'initial' if step == 0 else f'step-{step:04d}'
    path = episode / 'frames' / (tag + '.json')
    value = read(path)
    require(value['step'] == step and value['action_interface'] == 'joint' and not value['ended'],
            f'Invalid decision-start observation: {path}')
    return path, value


def without_nulls(value):
    if isinstance(value, dict):
        return {key: without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [without_nulls(item) for item in value]
    return value


def original_decision(arguments):
    fields = {'assessment', 'plan', 'phase', 'left', 'right', 'steps', 'settle'}
    require(set(arguments) in (fields, fields | {'memory'}),
            'Unexpected original control_arms argument fields')
    require(arguments['phase'] in ('fine', 'transit'), 'Invalid original phase')
    require(type(arguments['steps']) is int and 1 <= arguments['steps'] <= 20, 'Invalid original steps')
    require(type(arguments['settle']) is int and 0 <= arguments['settle'] <= 2, 'Invalid original settle')
    for key in ('assessment', 'plan', *(('memory',) if 'memory' in arguments else ())):
        require(isinstance(arguments[key], str), f'Invalid original {key}')
    for arm in ARMS:
        spec = arguments[arm]
        if spec is None:
            continue
        require(set(spec) == {'control', 'joints', 'gripper'} and spec['control'] == 'joint',
                'Unexpected original joint arm fields')
        if spec['joints'] is not None:
            require(len(spec['joints']) == 6 and all(type(v) in (int, float) and math.isfinite(v)
                    for v in spec['joints']), 'Invalid original joint target')
        value = spec['gripper']
        require(value is None or (type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1),
                'Invalid original gripper target')
    arms = {arm: without_nulls(arguments[arm]) for arm in ARMS if arguments[arm] is not None}
    decision = {key: arguments[key] for key in ('assessment', 'plan', 'phase')}
    decision['memory'] = arguments.get('memory', '')
    decision['action'] = {'control_arms': {'arms': arms, 'steps': arguments['steps'],
                                         'settle': arguments['settle']}}
    return decision


def source_plans(batches):
    plans, hashes, seen = [], {}, set()
    memory_policies = []
    clock = timing(batches[0].parent.parent)
    for batch in batches:
        require(timing(batch.parent.parent) == clock, 'Source batches have incompatible control timing')
        manifest, summary, audit = (read(batch / name) for name in
                                    ('batch.json', 'summary.json', 'final-audit.json'))
        require(manifest['status'] == 'complete' and manifest['mode'] == 'control_only'
                and manifest['enabled_tools'] == ['control_arms']
                and summary['final_audit_passed'] and audit.get('passed'),
                'Expected completed, audited, control_arms-only batch')
        policy = {key: manifest.get(key, value) for key, value in LEGACY_MEMORY_POLICY.items()}
        require(all(type(value) is bool for value in policy.values()), 'Invalid source memory policy')
        require(policy['explicit_episode_memory'] == policy['model_history_replay']
                and not policy['rgb_memory_enabled'], 'Unsupported source memory policy')
        memory_policies.append(policy)
        for name in ('batch.json', 'summary.json', 'final-audit.json'):
            hashes[str(batch / name)] = sha256(batch / name)
        for result in manifest['episodes']:
            task, layout = result['task'], result.get('layout', 20)
            require((task, layout) not in seen, f'Duplicate task/layout: {(task, layout)}')
            seen.add((task, layout))
            run = Path(result.get('output', batch / task / f'L{layout}'))
            plans.append((batch, task, layout, run, result))
    require(all(policy == memory_policies[0] for policy in memory_policies),
            'Cannot mix memory-enabled and memory-disabled trajectories')
    return plans, hashes, clock, memory_policies[0]


def decision_records(run, layout, expected_steps, memory_enabled=True):
    """Bind each raw response to its input, command, result and executed prefix."""
    episode, policy = run / f'seed-{layout}', run / f'policy-{layout}'
    decisions_path, commands_path = policy / 'decisions.jsonl', policy / 'commands.jsonl'
    actions_path = episode / 'actions.jsonl'
    decisions, commands, actions = (list(rows(p)) for p in (decisions_path, commands_path, actions_path))
    require(len(actions) == expected_steps, f'Action count differs from episode results: {run}')
    require([row['step'] for row in actions] == list(range(1, len(actions) + 1)),
            f'Non-contiguous executed actions: {run}')
    require(commands[0]['command']['op'] == 'observe' and commands[0]['step'] == 0,
            'Episode must start with a zero-step observation')
    controls = commands[1:]
    paths = sorted((policy / 'model').glob('*.response.json'))
    require(len(paths) == len(decisions) == len(controls), f'Decision/response/command mismatch: {run}')
    grouped = {}
    for row in actions:
        grouped.setdefault(row['command'], []).append(row)
    files = [decisions_path, commands_path, actions_path]
    records, end_step, seen = [], 0, set()
    for position, (response_path, decision, issued) in enumerate(zip(paths, decisions, controls), 1):
        stem = response_path.name.removesuffix('.response.json')
        request_path = response_path.with_name(stem + '.request.json')
        result_path = response_path.with_name(stem + '.tool-result.json')
        request, response, result = read(request_path), read(response_path), read(result_path)
        calls = [item for item in response['output'] if item['type'] == 'function_call']
        require(response['status'] == 'completed' and len(calls) == 1, 'Expected one completed original call')
        call = calls[0]
        require(call['name'] == 'control_arms' and call['status'] == 'completed', 'Unexpected original tool')
        require(call['call_id'] not in seen, 'Duplicate original call ID')
        seen.add(call['call_id'])
        arguments = json.loads(call['arguments'])
        require(('memory' in arguments) == memory_enabled, 'Response differs from source memory policy')
        normalized = original_decision(arguments)
        start = request['step']
        require(start == end_step == decision['step'] and decision['decision'] == normalized,
                f'Original response/decision mismatch: {response_path}')
        context = json.loads(request['question'])
        require(('memory' in context) == memory_enabled, 'Request differs from source memory policy')
        if not memory_enabled:
            require(not {'recent_executed_segments', 'stalled_segments_this_controller'} & context.keys()
                    and not any(request.get(key) for key in
                                ('replayed_call_id', 'replayed_response_id', 'history_replayed')),
                    'Memory-disabled request contains a history ledger or model replay')
        obs_path, obs = observation(episode, start)
        require(context['step'] == start and context['state'] == obs['state']
                and context['instruction'] == obs['instruction'], 'Request/frame state mismatch')
        command = issued['command']
        require(command['op'] == 'control_action' and command['phase'] == arguments['phase']
                and command['action'] == normalized['action']['control_arms'],
                'Original response/issued command mismatch')
        require(result['type'] == 'function_call_output' and result['call_id'] == call['call_id'],
                'Original response/result mismatch')
        actual = json.loads(result['output'])
        executed = grouped.pop(command['id'], [])
        end_step = issued['step']
        count = end_step - start
        require(actual['step'] == end_step and actual['feedback']['executed_steps'] == count
                and [row['step'] for row in executed] == list(range(start + 1, end_step + 1)),
                'Executed prefix does not match command/result boundaries')
        require(0 <= count <= arguments['steps'] + arguments['settle'], 'Invalid executed duration')
        images = request['images']
        require(len({item['name'] for item in images}) == len(images), 'Duplicate image names')
        require({camera for camera, _ in CAMERAS} <= {item['name'] for item in images},
                'Missing original current camera')
        records.append(dict(position=position, request=request, call=call, arguments=arguments,
                            result=result, start=start, end=end_step, actual=actual,
                            command=command['id'], observation_path=obs_path,
                            request_path=request_path, response_path=response_path, result_path=result_path))
        files.extend((request_path, response_path, result_path, obs_path))
    require(not grouped and end_step == expected_steps, 'Unmatched executed actions or incomplete episode')
    return records, files


def prompt(request):
    views = '\n'.join(item['name'] + ': <image>' for item in request['images'])
    return 'Observation context:\n' + request['question'] + '\nNamed views:\n' + views


def image_target(output, task, layout, record, item):
    require(re.fullmatch(r'[A-Za-z0-9_-]+', item['name']) is not None, 'Invalid image name')
    return output / 'images' / task / f'episode{layout:06d}' / f"decision{record['position']:06d}" / (item['name'] + '.jpg')


def sample_for(output, task, layout, record):
    system = SYSTEM if 'memory' in record['arguments'] else NO_MEMORY_SYSTEM
    return {'messages': [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': prompt(record['request'])},
                         {'role': 'assistant', 'content': record['call']['arguments']}],
            'images': [str(image_target(output, task, layout, record, item))
                       for item in record['request']['images']]}


def index_for(line, task, layout, run, expected, record):
    args, actual = record['arguments'], record['actual']
    previous = json.loads(record['request']['question']).get('previous_observation')
    return {'all_line': line, 'subset': 'train' if expected['success'] else 'failed',
            'task': task, 'layout': layout, 'decision': record['position'],
            'observation_step': record['start'], 'previous_observation_step': previous['step'] if previous else None,
            'command': record['command'], 'function_name': record['call']['name'], 'call_id': record['call']['call_id'],
            'requested_steps': args['steps'], 'requested_settle': args['settle'], 'phase': args['phase'],
            'executed_native_steps': record['end'] - record['start'], 'end_native_step': record['end'],
            'execution_status': actual['feedback']['status'],
            'shorter_than_requested': record['end'] - record['start'] < args['steps'] + args['settle'],
            'source_observation': str(record['observation_path']), 'source_request': str(record['request_path']),
            'source_response': str(record['response_path']), 'source_tool_result': str(record['result_path']),
            'source_episode': str(run / f'seed-{layout}'), 'episode_success': expected['success'],
            'episode_score': expected['native_score'], 'episode_outcome': expected['outcome']}


def raw_for(line, record):
    return {'all_line': line, 'function_call': record['call'], 'tool_result': record['result']}


def export(batch, output, additional_batches=()):
    batches = [Path(batch).resolve(), *(Path(p).resolve() for p in additional_batches)]
    output = Path(output).resolve()
    plans, hashes, clock, memory_policy = source_plans(batches)
    output.mkdir(parents=True, exist_ok=False)
    counts, steps, phases, statuses = Counter(), Counter(), Counter(), Counter()
    episodes, first_example, pickup_example = [], None, None
    with ExitStack() as stack:
        handles = {name: stack.enter_context((output / name).open('w')) for name in DATA_FILES}
        for source_batch, task, layout, run, expected in plans:
            records, files = decision_records(run, layout, expected['steps'], memory_policy['explicit_episode_memory'])
            for path in files:
                hashes[str(path)] = sha256(path)
            short = zero = 0
            for record in records:
                sample = sample_for(output, task, layout, record)
                for item, target in zip(record['request']['images'], sample['images']):
                    source, target = Path(item['path']), Path(target)
                    require(sha256(source) == item['sha256'], f'Original image changed: {source}')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                counts['all'] += 1
                counts['unique_images'] += len(sample['images'])
                index = index_for(counts['all'], task, layout, run, expected, record)
                subset = index['subset']
                counts[subset] += 1
                text = compact(sample) + '\n'
                handles['all.jsonl'].write(text)
                handles[subset + '.jsonl'].write(text)
                handles['index.jsonl'].write(compact(index) + '\n')
                handles['raw-outputs.jsonl'].write(compact(raw_for(counts['all'], record)) + '\n')
                steps[record['arguments']['steps']] += 1
                phases[record['arguments']['phase']] += 1
                statuses[index['execution_status']] += 1
                short += index['shorter_than_requested']
                zero += index['executed_native_steps'] == 0
                if first_example is None and expected['success']:
                    first_example = sample
                if pickup_example is None and task == 'general_pickup':
                    pickup_example = sample
            episodes.append({'task': task, 'layout': layout, 'samples': len(records),
                             'native_actions': expected['steps'], 'success': expected['success'],
                             'score': expected['native_score'], 'outcome': expected['outcome'],
                             'shorter_than_requested': short, 'zero_motion_decisions': zero,
                             'source_episode': str(run / f'seed-{layout}'), 'source_run': str(run),
                             'source_batch': str(source_batch), 'enabled_tools': ['control_arms']})
            print(f'EXPORTED {task} L{layout}: {len(records)} original decisions', flush=True)
    manifest = {'schema_version': SCHEMA_VERSION, 'format': 'ms-swift messages + images JSONL',
                'source_batch': str(batches[0]), 'source_batches': [str(p) for p in batches],
                'created_at': datetime.now(timezone.utc).isoformat(), 'output': str(output),
                'documentation': DOC_URL, 'sampling': 'one sample per original ASTRA control_arms response',
                'assistant_label': 'exact recorded function_call.arguments string, unchanged',
                'timing': clock, 'history': 'recorded request context and images in their original order',
                'counts': {'episodes': len(episodes), **{name: counts[name] for name in
                           ('all', 'train', 'failed', 'unique_images')}},
                'steps_distribution': dict(sorted(steps.items())), 'phase_distribution': dict(phases),
                'execution_status_distribution': dict(statuses), 'episodes': episodes,
                'source_sha256': hashes, 'memory_policy': memory_policy,
                'system_prompt': SYSTEM if memory_policy['explicit_episode_memory'] else NO_MEMORY_SYSTEM,
                'execution_note': 'Requested target, steps, settle, nulls and explicit text fields remain original. '
                                  'Actual execution may stop early or reject before motion; index.jsonl and '
                                  'raw-outputs.jsonl preserve the actual result separately. No claim that '
                                  'a requested target was reached. Physics cycles do not replace label steps.'}
    save(output / 'manifest.json', manifest)
    save(output / 'example.json', first_example or next(rows(output / 'all.jsonl')))
    if pickup_example:
        save(output / 'general_pickup.example.json', pickup_example)
    write_readme(output, manifest)
    return manifest


def write_readme(output, manifest):
    counts, clock = manifest['counts'], manifest['timing']
    memory_enabled = manifest.get('memory_policy', LEGACY_MEMORY_POLICY)['explicit_episode_memory']
    lines = ['# ASTRA 原始决策：ms-swift 训练数据', '',
             ('注意：源轨迹启用了回合文字 memory 和模型历史回放，不符合无 memory 要求。'
              '保留此数据用于原始记录审计。' if memory_enabled else
              '源轨迹关闭回合文字 memory、模型历史回放及历史动作账本；'
              '导出输入与标签均无 memory 字段。'), '',
             '每个样本对应 ASTRA 的一次原始 control_arms 输出。assistant.content 与源响应的 '
             'function_call.arguments 字符串完全相同，保留字段顺序、数值、null、steps、settle、'
             'phase 及 assessment/plan' + ('/memory' if memory_enabled else '') + '，不做插值或时长换算。', '',
             f"- train.jsonl：{counts['train']} 条，来自成功回合。",
             f"- failed.jsonl：{counts['failed']} 条，来自失败回合。",
             f"- all.jsonl：{counts['all']} 条，覆盖 {counts['episodes']} 个回合。",
             '- index.jsonl：逐条来源、决策边界、请求时长与实际执行结果。',
             '- raw-outputs.jsonl：原始 function_call 和对应 tool_result，一一对应 all.jsonl。',
             '- images/：按请求复制的原始图片；顺序和名称与请求一致。', '',
             '## 时间和执行含义', '',
             f"steps 和 settle 的单位均为 {clock['source_native_action_hz']:g} Hz 原生动作步，"
             f"每步 {clock['native_step_duration_ms']:g} 毫秒。steps 保留模型原值；"
             '它不是 250 Hz 物理周期数，也不会统一为 10。settle 保持独立。', '',
             'fine 阶段通常为短动作（执行上限 6），transit 可请求到 20 步。'
             '提前中断、零步拒绝和回合结束时的原始请求全部保留；实际执行量只写在索引及工具结果中，'
             '不把未达到的目标当作已执行结果。成功回合也可能包含被中断或拒绝的请求。', '',
             '## 输入与标签', '',
             '每行仍为 messages（system/user/assistant）和 images。user 使用该次原始 request.question '
             '及 request.images 的命名顺序，包含当时的公共状态、此前反馈和当前/上一步观测。'
             '只使用决策当时已有的信息。当前请求的执行结果及最终回合标签只保存在审计文件。', '',
             'assistant 现在是原始 control_arms 参数 JSON，顶层为 left/right/steps/settle/phase '
             '以及文字字段；不再是旧版 arms 包装。null 表示保持，夹爪数值不裁剪、不补值。'
             '推理加密内容不进入训练集。', '',
             '此版本训练输出为参数 JSON 文本；没有运行 ms-swift 训练或模型专属分词验证。'
             '执行时需按原始 native-step 语义调用 control_arms，不能直接当作 250 Hz 简化执行器接口。', '',
             '## 使用与复核', '', f'[ms-swift 格式文档]({DOC_URL})', '', '```bash',
             f'--dataset {output / "train.jsonl"} --split_dataset_ratio 0', '```', '',
             '图片使用绝对路径；移动数据集时必须同步修改 images 路径。'
             '按完整回合区分成功/失败，不随机拆相邻帧为训练和验证。', '',
             '```bash', f'{ROOT / ".venv-policy/bin/python"} {ROOT / "scripts/export_control_only_ms_swift.py"} \\',
             f'  --batch {manifest["source_batch"]} '
             + ''.join(f'--additional-batch {p} ' for p in manifest['source_batches'][1:])
             + '--output /path/to/new-dataset',
             f'{ROOT / ".venv-policy/bin/python"} {ROOT / "scripts/export_control_only_ms_swift.py"} \\',
             f'  --validate-only --output {output}', '```', '', '## steps 分布', '',
             '| 原始 steps | 样本数 |', '| --- | --- |']
    lines.extend(f'| {step} | {count} |' for step, count in manifest['steps_distribution'].items())
    lines.extend(['', '## 逐回合数量', '', '| 任务 | 布局 | 结果 | 原生动作数 | 原始决策数 |',
                  '| --- | --- | --- | --- | --- |'])
    lines.extend(f"| {e['task']} | L{e['layout']} | {'成功' if e['success'] else '失败'} | "
                 f"{e['native_actions']} | {e['samples']} |" for e in manifest['episodes'])
    (output / 'README.md').write_text('\n'.join(lines) + '\n')


def validate(output):
    """Verify exact original labels, request images, temporal boundaries and results."""
    from PIL import Image

    output = Path(output).resolve()
    manifest = read(output / 'manifest.json')
    require(manifest['schema_version'] == SCHEMA_VERSION, 'Expected an original-decision dataset (schema 2)')
    memory_policy = manifest.get('memory_policy', LEGACY_MEMORY_POLICY)
    _, _, _, source_policy = source_plans([Path(path) for path in manifest['source_batches']])
    require(memory_policy == source_policy, 'Dataset differs from source memory policy')
    require(manifest['system_prompt'] == (SYSTEM if memory_policy['explicit_episode_memory'] else NO_MEMORY_SYSTEM),
            'Dataset system contract differs from exporter')
    for path, digest in manifest['source_sha256'].items():
        require(sha256(path) == digest, f'Source changed: {path}')
    counts, by_task, steps, phases, statuses = Counter(), Counter(), Counter(), Counter(), Counter()
    targets, null_arms, null_fields = set(), 0, 0
    with ExitStack() as stack:
        data = {name: iter(rows(output / name)) for name in DATA_FILES}
        for info in manifest['episodes']:
            run, task, layout = Path(info['source_run']), info['task'], info['layout']
            records, _ = decision_records(run, layout, info['native_actions'], memory_policy['explicit_episode_memory'])
            require(len(records) == info['samples'], 'Episode decision count changed')
            expected = {'success': info['success'], 'native_score': info['score'], 'outcome': info['outcome']}
            for record in records:
                counts['all'] += 1
                line = counts['all']
                sample, index, raw = (next(data[name]) for name in ('all.jsonl', 'index.jsonl', 'raw-outputs.jsonl'))
                require(sample == sample_for(output, task, layout, record), f'Original label/input mismatch: line {line}')
                require(index == index_for(line, task, layout, run, expected, record), f'Boundary/index mismatch: line {line}')
                require(raw == raw_for(line, record), f'Raw function call/result mismatch: line {line}')
                subset = index['subset']
                require(next(data[subset + '.jsonl']) == sample, 'Success/failure partition mismatch')
                counts[subset] += 1
                by_task[task] += 1
                require(sample['messages'][1]['content'].count('<image>') == len(sample['images']),
                        'Image placeholder count differs')
                for item, target in zip(record['request']['images'], sample['images']):
                    target = Path(target)
                    require(target.is_absolute() and sha256(target) == item['sha256'] == sha256(item['path']),
                            f'Original image bytes differ: {target}')
                    with Image.open(target) as picture:
                        require(picture.format == 'JPEG' and picture.size == tuple(item['size']) == (640, 480),
                                f'Invalid original image: {target}')
                        picture.load()
                    counts['unique_images'] += 1
                args = record['arguments']
                steps[args['steps']] += 1
                phases[args['phase']] += 1
                statuses[index['execution_status']] += 1
                counts['shorter_than_requested'] += index['shorter_than_requested']
                counts['zero_motion_decisions'] += index['executed_native_steps'] == 0
                for arm in ARMS:
                    if args[arm] is None:
                        null_arms += 1
                    else:
                        null_fields += sum(args[arm][key] is None for key in ('joints', 'gripper'))
                        if args[arm]['gripper'] is not None:
                            targets.add(args[arm]['gripper'])
            print(f'VALIDATED {task} L{layout}: {len(records)} original decisions', flush=True)
        for name, iterator in data.items():
            require(next(iterator, None) is None, f'Extra rows in {name}')
    require({key: counts[key] for key in ('all', 'train', 'failed', 'unique_images')}
            == {key: manifest['counts'][key] for key in ('all', 'train', 'failed', 'unique_images')}, 'Count mismatch')
    require({str(k): v for k, v in sorted(steps.items())} == manifest['steps_distribution'], 'Steps distribution mismatch')
    require(dict(phases) == manifest['phase_distribution'] and dict(statuses) == manifest['execution_status_distribution'],
            'Phase/status distribution mismatch')
    require(read(output / 'example.json') == next(rows(output / ('train.jsonl' if counts['train'] else 'all.jsonl'))),
            'Example differs from dataset')
    if (output / 'general_pickup.example.json').exists():
        pickup_line = next(i['all_line'] for i in rows(output / 'index.jsonl') if i['task'] == 'general_pickup')
        pickup = next(s for i, s in enumerate(rows(output / 'all.jsonl'), 1) if i == pickup_line)
        require(read(output / 'general_pickup.example.json') == pickup, 'Pickup example differs from dataset')
    result = {'passed': True, 'validated_at': datetime.now(timezone.utc).isoformat(), 'counts': manifest['counts'],
              'memory_policy': memory_policy,
              'steps_distribution': dict(sorted(steps.items())), 'by_task': dict(by_task),
              'shorter_than_requested': counts['shorter_than_requested'],
              'zero_motion_decisions': counts['zero_motion_decisions'],
              'null_arms_preserved': null_arms, 'null_arm_fields_preserved': null_fields,
              'gripper_target_values': sorted(targets), 'ms_swift_runtime_tested': False,
              'checks': {key: True for key in ('exact_original_argument_strings', 'original_nulls_and_values',
                         'original_request_context', 'original_image_bytes_and_order', 'all_images_decode',
                         'decision_start_and_executed_prefix_alignment', 'unmodified_requested_steps_and_settle',
                         'raw_function_call_and_tool_result', 'success_failure_partition', 'source_hashes_unchanged')},
              'dataset_sha256': {name: sha256(output / name) for name in DATA_FILES}}
    save(output / 'validation.json', result)
    return result


def merge_dataset(base, additional_batches, output):
    require(bool(additional_batches), 'A merge requires at least one additional batch')
    original, audit = read(base / 'manifest.json'), read(base / 'validation.json')
    require(original['schema_version'] == SCHEMA_VERSION and audit['passed'],
            'Re-export the base dataset as original decisions before merging')
    for name, digest in audit['dataset_sha256'].items():
        require(sha256(base / name) == digest, f'Base dataset changed since validation: {name}')
    batches = [Path(p) for p in original['source_batches']]
    manifest = export(batches[0], output, [*batches[1:], *additional_batches])
    preserved = {}
    for name in DATA_FILES:
        new_rows = iter(rows(output / name))
        count = 0
        for old in rows(base / name):
            if name in ('all.jsonl', 'train.jsonl', 'failed.jsonl'):
                old['images'] = [str(output / Path(p).relative_to(base)) for p in old['images']]
            require(next(new_rows) == old, f'Merge changed existing original decision: {name}:{count + 1}')
            count += 1
        require(sha256(base / name) == audit['dataset_sha256'][name], f'Base changed during merge: {name}')
        preserved[name] = count
    manifest['merged_from_dataset'] = str(base)
    save(output / 'manifest.json', manifest)
    save(output / 'merge-record.json', {'base_dataset': str(base), 'base_sha256': audit['dataset_sha256'],
         'additional_batches': [str(p) for p in additional_batches], 'preserved_original_rows': preserved,
         'original_rows_unchanged_except_image_root': True, 'base_counts': original['counts'],
         'merged_counts': manifest['counts'], 'added_samples': manifest['counts']['all'] - original['counts']['all']})
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, default=DEFAULT_BATCH)
    parser.add_argument('--additional-batch', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--merge-from', type=Path)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if not args.validate_only:
        additional = [p.resolve() for p in args.additional_batch]
        if args.merge_from:
            merge_dataset(args.merge_from.resolve(), additional, output)
        else:
            export(args.batch.resolve(), output, additional)
    result = validate(output)
    print(compact({'output': str(output), 'passed': result['passed'], 'counts': result['counts']}), flush=True)


if __name__ == '__main__':
    main()
