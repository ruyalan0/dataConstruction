"""Drive three bounded TCP observation moves and audit a static rendered marker."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import label

from egg_harness.active_vision import locate_temporal
from egg_harness.client import Robot
from egg_harness.multiview import MultiViewGeometry, project
from egg_harness.public_review import review_frames
from egg_harness.skill_policy import validate


def marker_pixel(path):
    rgb = np.asarray(Image.open(path)).astype(float)
    mask = (rgb[:, :, 0] > 140) & (rgb[:, :, 2] > 140) & (rgb[:, :, 1] < .85 * np.minimum(rgb[:, :, 0], rgb[:, :, 2]))
    regions, count = label(mask)
    parts = [np.argwhere(regions == i) for i in range(1, count + 1)]
    parts = [part for part in parts if len(part) >= 12]
    if len(parts) != 1:
        raise ValueError('Expected one independently detected magenta marker: ' + str([len(p) for p in parts]))
    return (parts[0].mean(axis=0)[::-1] + .5).tolist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    robot = Robot(args.episode, args.output)
    geometry = MultiViewGeometry(); moves = []
    if robot.obs['step'] != 0:
        raise ValueError('Qualification requires a fresh isolated fixture')
    try:
        # A newly inserted developer marker needs a native update before measuring.
        # Keep this hold in the public action log and step budget.
        robot.command('move_local', arms={}, steps=1, settle=0, label='active_vision_fixture_settle')
        if robot.obs['step'] != 1:
            raise ValueError('Fixture initialization hold did not execute exactly once')
        observations = [robot.obs]
        # Scene was inspected from head and wrist RGB: empty hand, upward clearance.
        for _ in range(3):
            before = robot.obs
            goal = np.asarray(before['state']['left_ee_pose'][:3]) + [0, 0, .020]
            action = {'arm': 'left', 'position': goal.tolist(), 'steps': 3, 'settle': 0}
            validate(action, 'fine', before)
            robot.command('move_local', arms={'left': {'position': goal.tolist()}}, steps=3, settle=0,
                label='active_vision_tcp_observation', guard={'arm': 'left', 'tracking_error_m': .035, 'watch_grip': False, 'opening_change': .003})
            moves.append({'before_step': before['step'], 'after_step': robot.obs['step'], 'requested_tcp_position': goal.tolist(),
                          'actual_ee_pose': robot.obs['state']['left_ee_pose']})
            (args.output / 'moves.json').write_text(json.dumps(moves, indent=2))
            if robot.obs['step'] != before['step'] + 3:
                raise ValueError('Observation move was interrupted; inspect recorded prefix')
            observations.append(robot.obs)
        camera = 'cam_left_wrist'; samples = []; all_pixels = []
        for obs in observations:
            images, records = review_frames(args.episode, robot.obs['step'], [{'step': obs['step'], 'camera': camera}])
            pixel = marker_pixel(images[records[0]['image']]); all_pixels.append({'step': obs['step'], 'pixel': pixel})
        samples = [all_pixels[i] for i in (0, 1, 3)]
        images, records = review_frames(args.episode, robot.obs['step'], [{'step': s['step'], 'camera': camera} for s in samples])
        manifest = {name: {'path': path, 'size': [640, 480], 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()} for name, path in images.items()}
        request = {'step': robot.obs['step'], 'camera': camera, 'feature': 'Static magenta marker center',
                   'stationary_target': True, 'camera_hand_empty': True,
                   'stationarity_evidence': 'Fixed visual fixture; the empty open camera hand moves upward without contact; the other arm holds.', 'samples': samples}
        result = locate_temporal(geometry, request, args.episode, robot.obs, records, manifest)
        two = locate_temporal(geometry, {**request, 'samples': [samples[0], samples[-1]]}, args.episode, robot.obs, records, manifest)
        fixture = json.loads((args.episode / 'qualification-fixture.json').read_text())
        error = float(np.linalg.norm(np.asarray(result['position']) - fixture['world_point']))
        report = {'status': 'passed' if error < .01 else 'failed', 'scope': 'Single synthetic static marker after actual TCP motion; no task/grasp accuracy claim',
                  'native_steps': robot.obs['step'], 'fixture_initialization_hold_steps': 1,
                  'all_detected_pixels': all_pixels, 'three_frame_result': result,
                  'two_frame_result': two, 'position_error_m': error,
                  'requested_camera_control': 'TCP move_local, fine 20mm/3 native steps per move; no policy joint tool',
                  'truth_usage': 'Fixture world position used only after localization for developer error measurement'}
        (args.output / 'result.json').write_text(json.dumps(report, indent=2))
        if report['status'] != 'passed': raise ValueError('Temporal rendered geometry error exceeds 10mm')
        print(json.dumps({'status': report['status'], 'error_m': error, 'steps': robot.obs['step'], 'baseline_m': result['measured_camera_baselines_m']}))
    except BaseException as error:
        (args.output / 'failure.json').write_text(json.dumps({'type': type(error).__name__, 'message': str(error), 'step': robot.obs['step']}, indent=2))
        raise
    finally:
        robot.command('finish')


if __name__ == '__main__': main()
