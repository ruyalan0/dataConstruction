"""Paid review -> temporal triangulation -> result-consumption test, no motion."""
import argparse
import json
from pathlib import Path

import numpy as np

from egg_harness.active_vision import locate_temporal
from egg_harness.multiview import MultiViewGeometry
from egg_harness.native_responses import NativeToolVision
from egg_harness.public_review import review_frames
from egg_harness.usage import model_usage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode', type=Path, required=True)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider', choices=['azure-astra', 'zzz-astra', 'zzy-astra'], required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads(Path('configs/classify-objects-multijoint-development.json').read_text())
    config.update(astra_provider=args.provider, reasoning_effort='xhigh', max_model_calls=3,
                  joint_control=False, direct_posture_action=False, posture_recovery_program=False, public_posture_feedback=False)
    qualification = json.loads(args.qualification.read_text())
    steps = [r['step'] for r in qualification['three_frame_result']['source_frames']]
    obs = json.loads((args.episode / 'latest.json').read_text())
    if obs['step'] != steps[-1]: raise ValueError('Fixture endpoint changed')
    camera = 'cam_left_wrist'; geometry = MultiViewGeometry()
    expected = [{'step': step, 'camera': camera} for step in steps]
    expected += [{'step': steps[0], 'camera': 'cam_head'}, {'step': steps[-1], 'camera': 'cam_head'}]
    model = NativeToolVision(config, args.output / 'model')
    try:
        question = {'step': obs['step'], 'state': obs['state'],
                    'instruction': 'Protocol test after three small TCP observation moves of an empty open left hand. One small magenta visual marker is fixed in world space, uncontacted; the other arm holds. First call review_frames with exactly the supplied list. No robot motion.',
                    'frames_to_review': expected}
        decision = model.ask(json.dumps(question), {name: obs['cameras'][name] for name in (camera, 'cam_head')})
        if decision['action'] != {'review': expected}: raise ValueError('Expected the specified public frame review')
        images, records = review_frames(args.episode, obs['step'], decision['action']['review'])
        model.observe({'reviewed_frames': records, 'native_steps_executed': 0}, obs['step'], False)
        question = {'step': obs['step'], 'state': obs['state'], 'reviewed_observations': records,
                    'instruction': 'Use triangulate_temporal_point on the fixed small bright magenta marker CENTER seen in each of the THREE reviewed LEFT WRIST images. Supply sample steps in increasing order, raw 640x480 pixels inferred from RGB, step=current endpoint. The head images provide context. Explain stationarity and empty-hand evidence. No coordinates/point truth have been supplied; infer each pixel from its image. No robot action.'}
        decision = model.ask(json.dumps(question), images)
        if set(decision['action']) != {'triangulate_temporal'}: raise ValueError('Expected native temporal tool')
        result = locate_temporal(geometry, decision['action']['triangulate_temporal'], args.episode, obs, records, model.last_images)
        (args.output / 'triangulation.json').write_text(json.dumps(result, indent=2))
        model.observe({'temporal_triangulation': result}, obs['step'], False)
        decision = model.ask(json.dumps({'step': obs['step'], 'instruction': 'Read the actual temporal tool result. Call stop_episode with reason ACTIVE_VISION_OK and memory containing ONLY a JSON array of its returned XYZ rounded to six decimals. This is protocol completion, not robot task success.'}), {})
        if decision['action'] != {'stop': True, 'reason': 'ACTIVE_VISION_OK'} or json.loads(decision['memory']) != [round(x, 6) for x in result['position']]:
            raise ValueError('Actual temporal tool result was not consumed')
        model.observe({'stopped': 'protocol_complete', 'native_steps_executed': 0}, obs['step'], True)
        truth = json.loads((args.episode / 'qualification-fixture.json').read_text())['world_point']
        report = {'status': 'passed', 'provider': args.provider, 'model_calls': model.calls,
                  'usage': model_usage(args.output / 'model'), 'native_steps': 0,
                  'position_error_m': float(np.linalg.norm(np.asarray(result['position']) - truth)),
                  'scope': 'Astra-selected pixels from one real TCP observation fixture; no point truth/pixels supplied to model; not task/grasp accuracy',
                  'joint_tools_enabled': False}
        (args.output / 'result.json').write_text(json.dumps(report, indent=2)); print(json.dumps(report))
    except BaseException as error:
        model.fail_pending(error, obs['step'])
        (args.output / 'result.json').write_text(json.dumps({'status': 'failed', 'error': str(error), 'usage': model_usage(args.output / 'model')}, indent=2))
        raise
    finally:
        model.client.close()


if __name__ == '__main__': main()
