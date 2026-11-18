"""Build all 11 L20 format examples offline; these are not new 250 Hz rollouts."""
import argparse
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / 'harness'))
from egg_harness.io_contract import deduplicate_observation, readable_input, round_output
from egg_harness.native_tools import ToolRegistry
from policy import JOINT_SYSTEM


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def build(source, output):
    output.mkdir(parents=True, exist_ok=False)
    previous, records = [], []
    registry = ToolRegistry({'action_interface': 'joint'})
    tool = next(item for item in registry.definitions if item['name'] == 'control_arms')
    config = read(HERE / 'general_pickup.json')
    skills = '\n'.join((HERE / 'harness/skills' / name / 'SKILL.md').read_text()
                       for name in config['skill_names'])
    for request_path in sorted(source.glob('*.request.json')):
        stem = request_path.name.split('.')[0]
        request = read(request_path)
        context = json.loads(request['question'])
        for name in ('multiview_geometry', 'temporal_geometry', 'review_capability', 'reviewed_observations'):
            context.pop(name, None)
        # Target units are explicit, but all numeric observations remain historical.
        context['control_timing'] = dict(action_hz=250, action_interval_seconds=0.004,
                                         source_observation_action_hz=25, offline_format_preview=True)
        images = {item['name']: Path(item['path']) for item in request['images']}
        context, images, deduplication = deduplicate_observation(context, images, previous)
        content = [dict(type='input_text', text=json.dumps(context, allow_nan=False))]
        manifest = []
        for name, path in images.items():
            data = path.read_bytes()
            content.extend([dict(type='input_text', text=name),
                            dict(type='input_image', detail='high',
                                 image_url='data:image/jpeg;base64,' + base64.b64encode(data).decode())])
            manifest.append(dict(name=name, path=str(path), sha256=hashlib.sha256(data).hexdigest()))
        observation = dict(role='user', content=content)
        raw = read(source / (stem + '.response.json'))
        response = deepcopy(raw)
        calls = [item for item in response['output'] if item['type'] == 'function_call']
        if len(calls) != 1:
            raise ValueError('Expected one historical control_arms operation')
        call = calls[0]
        original_arguments = json.loads(call['arguments'])
        original_arguments.pop('phase')
        decision = registry.decision(call['name'], json.dumps(original_arguments))
        arguments = round_output(original_arguments)
        call['arguments'] = json.dumps(arguments, allow_nan=False)
        result = read(source / (stem + '.tool-result.json'))
        scope = 'Offline conversion of historical 25 Hz data; no new model call or 250 Hz execution'
        save(output / (stem + '.input.json'), dict(
            scope=scope, model=request['model'], instructions=JOINT_SYSTEM + '\n' + skills,
            tools=[tool], input=readable_input([*previous, observation]),
            current_images=manifest, deduplication=deduplication))
        save(output / (stem + '.context.json'), context)
        save(output / (stem + '.output.json'), dict(
            scope=scope, name=call['name'], arguments=arguments, decision=decision,
            source_response=str(source / (stem + '.response.json'))))
        records.append(dict(round=int(stem), source_step=context['step'],
                            source_current_images=len(request['images']),
                            new_current_images=len(images),
                            removed=deduplication['removed'],
                            input_file=stem + '.input.json', output_file=stem + '.output.json'))
        previous = [observation, *response['output'], result]
    save(output / 'manifest.json', dict(
        scope='Offline format preview only, not a new rollout or task result',
        source=str(source), source_action_hz=25, target_action_hz=250,
        observations_and_feedback='Historical measurements, not regenerated or re-executed',
        outputs='Historical tool arguments with phase removed and numeric precision normalized',
        rounds=records))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT /
                        'runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model')
    parser.add_argument('--output', type=Path, default=HERE / 'examples/general_pickup_L20')
    args = parser.parse_args()
    records = build(args.source.resolve(), args.output.resolve())
    print(json.dumps(dict(rounds=len(records), output=str(args.output), live_model_calls=0,
                          current_images=[record['new_current_images'] for record in records])))


if __name__ == '__main__':
    main()
