"""Temporal triangulation of reviewed RGB using recorded, measured camera poses."""
from itertools import combinations
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

from .kinematics import pose_matrix
from .multiview import triangulate
from .public_review import load_public_frame

MAX_STEP_SPAN = 24
OTHER_ARM_POSITION_TOLERANCE_M = .005
OTHER_ARM_ROTATION_TOLERANCE_DEG = 3.


def locate_temporal(geometry, request, episode, observation, reviewed, image_manifest):
    """Bind reviewed pixels to exact images/poses; stationarity remains assumed."""
    current = observation['step']
    if type(request['step']) is not int or request['step'] != current:
        raise ValueError('Temporal request must bind the current observation step')
    if observation.get('camera_calibration_id') != geometry.calibration_id:
        raise ValueError('Current observation lacks matching camera qualification')
    camera = request['camera']
    if camera not in ('cam_left_wrist', 'cam_right_wrist'):
        raise ValueError('Temporal observation requires one wrist camera')
    if request['stationary_target'] is not True or request['camera_hand_empty'] is not True or not request['stationarity_evidence'].strip():
        raise ValueError('State visual evidence for a stationary uncontacted target and empty camera hand')
    samples = request['samples']
    steps = [sample['step'] for sample in samples]
    if not 2 <= len(steps) <= 3 or any(type(step) is not int or step < 0 for step in steps):
        raise ValueError('Select two or three nonnegative recorded steps')
    if steps != sorted(set(steps)) or steps[-1] != current or current - steps[0] > MAX_STEP_SPAN:
        raise ValueError('Samples must increase strictly, end at current step and span at most 24 native steps')
    records = {(row['step'], row['camera']): row for row in reviewed}
    sources, cameras = [], []
    for sample in samples:
        step = sample['step']
        if (step, camera) not in records:
            raise ValueError('First review_frames for every requested sample, including current endpoint')
        shown = records[step, camera]
        path, record = load_public_frame(episode, current, step, camera)
        if record['camera_calibration_id'] != geometry.calibration_id:
            raise ValueError('Historical frame lacks matching camera qualification')
        if any(shown[key] != value for key, value in record.items()):
            raise ValueError('Recorded image or public pose changed since review')
        sent = image_manifest.get(shown['image'])
        if sent is None or Path(sent['path']).resolve() != path or sent['sha256'] != record['image_sha256']:
            raise ValueError('Sample was not presented as the same RGB in this model request')
        if list(sent['size']) != [640, 480]:
            raise ValueError('Presented RGB resolution differs from calibration')
        with Image.open(path) as picture:
            if list(picture.size) != [640, 480]:
                raise ValueError('Recorded RGB resolution differs from calibration')
        cameras.append(geometry.cameras(record)[camera])
        sources.append({'step': step, 'camera': camera, 'image_sha256': record['image_sha256']})

    arm = 'left' if camera == 'cam_left_wrist' else 'right'
    other = 'right' if arm == 'left' else 'left'
    interval = []
    for step in range(steps[0], current + 1):
        _, record = load_public_frame(episode, current, step, camera)
        if record['camera_calibration_id'] != geometry.calibration_id:
            raise ValueError('Camera qualification changed within observation interval')
        interval.append(record['state'])
    start = interval[0]
    fixed = pose_matrix(start[other + '_ee_pose'])
    for state in interval:
        if min(state[arm + '_gripper_measured_joints']) < .035 or state[arm + '_ee_joint_state'][0] < .8:
            raise ValueError('Observation camera hand must remain open throughout the interval')
        pose = pose_matrix(state[other + '_ee_pose'])
        distance = float(np.linalg.norm(pose[:3, 3] - fixed[:3, 3]))
        angle = float(np.degrees(Rotation.from_matrix(pose[:3, :3] @ fixed[:3, :3].T).magnitude()))
        if distance > OTHER_ARM_POSITION_TOLERANCE_M or angle > OTHER_ARM_ROTATION_TOLERANCE_DEG:
            raise ValueError('Other arm moved during observation; acquire an isolated observation interval')
        for hand in ('left', 'right'):
            for suffix, tolerance in (('_ee_joint_state', 1e-6), ('_gripper_measured_joints', .003)):
                if np.max(np.abs(np.asarray(state[hand + suffix]) - start[hand + suffix])) > tolerance:
                    raise ValueError('Gripper changed during observation interval')
    for hand in ('left', 'right'):
        for suffix in ('_ee_pose', '_ee_joint_state', '_gripper_measured_joints'):
            if not np.allclose(interval[-1][hand + suffix], observation['state'][hand + suffix], atol=1e-6, rtol=0):
                raise ValueError('Current public state differs from recorded endpoint')
    result = triangulate([sample['pixel'] for sample in samples], cameras)
    origins = [np.asarray(camera['world_from_camera'])[:3, 3] for camera in cameras]
    baselines = [float(np.linalg.norm(a - b)) for a, b in combinations(origins, 2)]
    return {**result, 'step': current, 'camera': camera, 'feature': request['feature'],
            'source_frames': sources, 'calibration_id': geometry.calibration_id,
            'measured_camera_baselines_m': baselines, 'native_steps_executed': 0,
            'observation_interval_native_steps': current - steps[0],
            'stationarity': {'assumed': True, 'verified': False,
                'model_evidence': request['stationarity_evidence'],
                'scope': 'Open camera hand, held other arm and unchanged grippers checked at every recorded step. These checks and low reprojection error cannot prove a static target or correct feature identity.'},
            'interpretation': 'World position of the matched surface feature conditional on a static target throughout the interval; not a grasp target or measured confidence bound.'}
