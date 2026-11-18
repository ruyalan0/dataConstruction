"""Three real Astra calls, including bounded replay; no robot or GPU actions."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from egg_harness.geometry import unproject
from egg_harness.native_responses import NativeToolVision
from egg_harness.usage import model_usage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path('configs/classify-objects-multijoint-development.json'))
    parser.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh'], default='xhigh')
    parser.add_argument('--provider', choices=['auto', 'azure-astra', 'zzz-astra', 'zzy-astra'], default='auto')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.config.read_text())
    config['reasoning_effort'] = args.effort
    config['astra_provider'] = args.provider
    config['max_model_calls'] = 3
    model = NativeToolVision(config, args.output / 'model')
    # The synthetic input makes the live check independent of private robot episodes.
    picture = Image.new('RGB', (640, 480), 'white')
    ImageDraw.Draw(picture).ellipse((311, 261, 321, 271), fill='red')
    path = args.output / 'fixture.jpg'; picture.save(path)
    first = {'step': 0, 'instruction': 'Protocol test without a robot. Call project_pixels for exactly pixel [316,266], z=0.77. Do not call movement tools.',
             'memory': 'Synthetic image and supplied coordinates; no robot execution.'}
    decision = model.ask(json.dumps(first), {'cam_head': path}, system='Follow this synthetic protocol test exactly; never request robot motion.')
    if decision['action'] != {'project': [{'pixel': [316, 266], 'z': .77}]}:
        raise AssertionError('Astra did not select the requested projection tool')
    calibration = json.loads(Path(config['memory']).read_text())['head_camera']
    point = unproject([316, 266], .77, calibration).tolist()
    feedback = {'projections': [point], 'native_steps_executed': 0}
    model.observe(feedback, 0, False)
    second = {'step': 0, 'instruction': 'Continue the protocol test: call project_pixels for pixel [320,240], with z equal to the third coordinate of the preceding projection tool result. Do not request motion.',
              'memory': 'Projection has returned; read the preceding tool output.'}
    decision = model.ask(json.dumps(second), {'cam_head': path}, system='Synthetic protocol test. Read the tool result and request the next projection.')
    if decision['action'] != {'project': [{'pixel': [320, 240], 'z': point[2]}]}:
        raise AssertionError('Astra did not consume the first projection result')
    point = unproject([320, 240], point[2], calibration).tolist()
    model.observe({'projections': [point], 'native_steps_executed': 0}, 0, False)
    # Third request drops the first complete cycle, exercising real bounded replay.
    third = {'step': 0, 'instruction': 'Finish the protocol test by calling stop_episode. Set reason to NATIVE_TOOLS_OK and memory to ONLY a JSON array containing the three most recently returned projection coordinates rounded to six decimal places. Read them from the preceding project_pixels tool output. Do not request motion.',
             'memory': 'Second projection has returned; read the preceding tool output.'}
    decision = model.ask(json.dumps(third), {'cam_head': path}, system='Synthetic protocol test. Read the tool result and stop as requested.')
    if decision['action'] != {'stop': True, 'reason': 'NATIVE_TOOLS_OK'}:
        raise AssertionError('Astra did not complete the tool roundtrip')
    returned = json.loads(decision['memory'])
    if returned != [round(value, 6) for value in point]:
        raise AssertionError('Astra did not consume the returned projection')
    model.observe({'stopped': 'synthetic_test_complete', 'native_steps_executed': 0}, 0, True)
    result = {'status': 'passed', **model.provider.audit(), 'effort': args.effort, 'tools': list(model.registry.contracts),
              'returned_projection': returned, 'model_calls': model.calls, 'native_steps': 0,
              'usage': model_usage(args.output / 'model')}
    (args.output / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__': main()
