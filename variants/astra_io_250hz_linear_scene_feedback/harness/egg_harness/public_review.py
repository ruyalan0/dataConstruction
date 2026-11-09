"""Read bounded recorded RGB and proprioception from this episode only."""
import json
import math
import hashlib
from pathlib import Path

CAMERAS = frozenset(('cam_head', 'cam_left_wrist', 'cam_right_wrist'))


def load_public_frame(episode, current_step, step, camera):
    """Read one episode-local, immutable RGB/pose pair; strip non-public fields."""
    root = Path(episode).resolve() / 'frames'
    if root.is_symlink() or type(current_step) is not int or type(step) is not int or not 0 <= step <= current_step or camera not in CAMERAS:
        raise ValueError('Review requires an episode-local past step and allowed camera')
    name = 'initial' if step == 0 else f'step-{step:04d}'
    frame_path = root / (name + '.json')
    if frame_path.is_symlink() or frame_path.resolve().parent != root:
        raise ValueError('Review record outside episode')
    frame = json.loads(frame_path.read_text())
    if type(frame['step']) is not int or frame['step'] != step:
        raise ValueError('Recorded frame step mismatch')
    path = Path(frame['cameras'][camera])
    if path.is_symlink() or path.resolve().parent != root or path.suffix != '.jpg' or not path.is_file():
        raise ValueError('Recorded RGB unavailable in this episode')
    state = frame['state']
    public = {arm + suffix: state[arm + suffix] for arm in ('left', 'right')
              for suffix in ('_ee_pose', '_ee_joint_state', '_gripper_measured_joints')}
    for field, values in public.items():
        size = 7 if field.endswith('_ee_pose') else (2 if field.endswith('_gripper_measured_joints') else 1)
        if not isinstance(values, list) or len(values) != size or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError('Malformed public proprioception vector')
    certificate = frame.get('camera_calibration_id')
    if certificate is not None and (not isinstance(certificate, str) or len(certificate) != 64 or any(c not in '0123456789abcdef' for c in certificate)):
        raise ValueError('Invalid historical camera calibration certificate')
    record = {'step': step, 'camera': camera, 'state': public,
              'camera_calibration_id': certificate,
              'image_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return path.resolve(), record


def review_frames(episode, current_step, requests):
    if type(current_step) is not int or not isinstance(requests, list) or not 1 <= len(requests) <= 6:
        raise ValueError('Review requires one to six recorded camera/step pairs')
    images, records, seen = {}, [], set()
    for item in requests:
        if not isinstance(item, dict) or set(item) != {'step', 'camera'}:
            raise ValueError('Review accepts only step and camera')
        step, camera = item['step'], item['camera']
        if type(step) is not int or not 0 <= step <= current_step or not isinstance(camera, str) or camera not in CAMERAS:
            raise ValueError('Review requires a past step and allowed camera')
        if (step, camera) in seen:
            raise ValueError('Duplicate review frame')
        seen.add((step, camera))
        path, record = load_public_frame(episode, current_step, step, camera)
        key = f'review_{step}_{camera}'
        images[key] = str(path.resolve())
        records.append({'image': key, **record})
    return images, records
