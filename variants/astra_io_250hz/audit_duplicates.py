"""Check the proposed duplicate pairs against original, unmodified episode logs."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCHES = (
    ROOT / 'runs/ten-tasks-control-only-20261014-001220',
    ROOT / 'runs/general-pickup-control-only-20261015-155448',
)
IMAGE_ALIASES = {
    'previous_head': 'cam_head',
    'previous_left_wrist': 'cam_left_wrist',
    'previous_right_wrist': 'cam_right_wrist',
}


def read(path):
    return json.loads(path.read_text())


def audit(batches=DEFAULT_BATCHES):
    counts = {name: dict(checked=0, equal=0, mismatches=[]) for name in
              ('state', 'arm_posture', 'images', 'memory', 'feedback')}
    episodes, sources, verified_images = [], {}, set()

    def source(path):
        sources[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return read(path)

    def check(name, left, right, request):
        counter = counts[name]
        counter['checked'] += 1
        if left == right:
            counter['equal'] += 1
        else:
            counter['mismatches'].append(str(request.relative_to(ROOT)))

    for batch in batches:
        for model in sorted(batch.glob('*/L*/policy-*/model')):
            paths = sorted(model.glob('*.request.json'))
            requests = [source(path) for path in paths]
            for previous_path, previous, path, current in zip(paths, requests, paths[1:], requests[1:]):
                before, now = json.loads(previous['question']), json.loads(current['question'])
                response = source(previous_path.with_name(previous_path.name.replace('.request.', '.response.')))
                calls = [item for item in response['output'] if item['type'] == 'function_call']
                if len(calls) != 1 or current['replayed_call_id'] != calls[0]['call_id']:
                    raise ValueError('Expected one replayed original tool cycle: ' + str(path))
                arguments = json.loads(calls[0]['arguments'])
                tool = source(previous_path.with_name(previous_path.name.replace('.request.', '.tool-result.')))
                result = json.loads(tool['output'])
                if tool['call_id'] != calls[0]['call_id'] or result['step'] != now['step']:
                    raise ValueError('Feedback/request boundary mismatch: ' + str(path))
                check('state', before['state'], now['previous_observation']['state'], path)
                check('arm_posture', before['arm_posture'], now['previous_arm_posture'], path)
                check('memory', arguments['memory'], now['memory'], path)
                check('feedback', result['feedback'], now['previous_result'], path)
                images = {item['name']: item for item in previous['images']}
                for item in current['images']:
                    if item['name'] not in IMAGE_ALIASES:
                        continue
                    original = images[IMAGE_ALIASES[item['name']]]
                    check('images', original['sha256'], item['sha256'], path)
                    for image in (original, item):
                        image_path = Path(image['path'])
                        if image_path not in verified_images:
                            if hashlib.sha256(image_path.read_bytes()).hexdigest() != image['sha256']:
                                raise ValueError('Image bytes do not match recorded SHA-256: ' + str(image_path))
                            verified_images.add(image_path)
            if paths:
                episodes.append(dict(run=str(model.parent.parent.relative_to(ROOT)),
                                     requests=len(paths), adjacent_pairs=len(paths) - 1))
    return dict(passed=bool(episodes) and all(c['checked'] and not c['mismatches'] for c in counts.values()),
                scope='Original control-only logs, compared before implementing the new variant',
                episodes=episodes, counts=counts, verified_image_files=len(verified_images),
                source_sha256=sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'audit/duplicates.json')
    args = parser.parse_args()
    result = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({key: result[key] for key in ('passed', 'counts', 'verified_image_files')}, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
