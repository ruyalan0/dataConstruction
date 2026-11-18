"""Development-only native action replay; no pose writes or budget resets."""
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation

ARMS = ('left', 'right')

def load_replay(episode, through, task, seed_index, mode):
    if mode != 'development' or type(through) is not int or through < 1:
        raise ValueError('Replay requires development and a positive native prefix')
    episode = Path(episode).resolve(strict=True)
    provenance = json.loads((episode / 'provenance.json').read_text())
    if provenance.get('task') != task or provenance.get('seeds') != [seed_index] or provenance.get('mode') != 'development':
        raise ValueError('Replay source task/seeds/development provenance mismatch')
    rows = []
    for line in (episode / 'actions.jsonl').read_text().splitlines():
        row = json.loads(line)
        if type(row.get('step')) is not int: raise ValueError('Invalid replay native step')
        if row['step'] > through: break
        action = row['action']
        joint_keys = {a + s for a in ARMS for s in ('_arm_joint_state', '_ee_joint_state')}
        pose_keys = {a + s for a in ARMS for s in ('_ee_pose', '_ee_joint_state')}
        if set(action) not in (joint_keys, pose_keys): raise ValueError('Replay requires exact official bilateral native action keys')
        for name, value in action.items():
            size = 1 if name.endswith('_ee_joint_state') else 7 if name.endswith('_ee_pose') else 6
            values = np.asarray(value, dtype=float)
            if values.shape != (size,) or not np.isfinite(values).all(): raise ValueError('Invalid replay action vector')
            if size == 1 and not 0 <= values[0] <= 1: raise ValueError('Invalid replay gripper intent')
            if size == 7 and abs(np.linalg.norm(values[3:]) - 1) > .003: raise ValueError('Invalid replay pose quaternion')
        if not isinstance(row.get('command'), str) or Path(row['command']).name != row['command']: raise ValueError('Invalid replay source command identity')
        rows.append(row)
    if [row['step'] for row in rows] != list(range(1, through + 1)):
        raise ValueError('Replay prefix must contain every native action exactly once')
    frame = episode / 'frames' / f'step-{through:04d}.json'
    target = json.loads(frame.read_text())
    if target['step'] != through: raise ValueError('Replay endpoint public frame step mismatch')
    for identifier in {row['command'] for row in rows}:
        command = json.loads((episode / 'commands' / (identifier + '.json')).read_text())
        if command['id'] != identifier: raise ValueError('Replay command identity mismatch')
    for step in range(1, through + 1):
        frame_path = episode / 'frames' / f'step-{step:04d}.json'
        if not frame_path.is_file(): raise ValueError('Replay source lacks a public comparison frame')
    return {'episode': str(episode), 'through': through, 'provenance': provenance, 'rows': rows,
            'prefix_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(), 'target': target}

def compare_public(expected, actual):
    report = {}
    for arm in ARMS:
        q_error = np.abs(np.asarray(actual[arm + '_arm_joint_state']) - expected[arm + '_arm_joint_state'])
        target = np.asarray(expected[arm + '_ee_pose']); observed = np.asarray(actual[arm + '_ee_pose'])
        orientation = (Rotation.from_quat(target[[4, 5, 6, 3]]).inv() * Rotation.from_quat(observed[[4, 5, 6, 3]])).magnitude()
        report[arm] = {'max_joint_error_rad': float(q_error.max()), 'wrist_position_error_m': float(np.linalg.norm(target[:3] - observed[:3])),
                       'wrist_rotation_error_rad': float(orientation), 'gripper_error_m': float(np.max(np.abs(np.asarray(actual[arm + '_gripper_measured_joints']) - expected[arm + '_gripper_measured_joints'])))}
    return report

def compare_objects(source_episode, through, actual_objects):
    matches = []
    for path in sorted((Path(source_episode) / 'developer-only').glob('*.json')):
        data = json.loads(path.read_text())
        if data.get('step') == through and isinstance(data.get('objects'), dict): matches.append((path, data['objects']))
    if not matches: return {'status': 'missing_same_step_source_diagnostic', 'errors': {}}
    path, expected = matches[-1]; errors = {}
    if set(expected) != set(actual_objects): raise ValueError('Replay object labels differ from source diagnostic')
    for label, target in expected.items():
        actual = actual_objects[label]
        errors[label] = {'position_error_m': float(np.linalg.norm(np.asarray(actual['position']) - target['position'])),
                         'quaternion_absolute_dot': float(abs(np.dot(np.asarray(actual['quaternion']).reshape(-1), np.asarray(target['quaternion']).reshape(-1))))}
    return {'status': 'compared', 'source_diagnostic': str(path), 'errors': errors}
