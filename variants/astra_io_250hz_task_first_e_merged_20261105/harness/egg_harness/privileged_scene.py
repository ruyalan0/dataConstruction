"""Read current simulator geometry for the privileged demonstration teacher.

No physics advance, action generation, reward query, or student-data cleaning.
All positions are world-frame metres; quaternions use wxyz.
"""
from copy import deepcopy
from itertools import product

import numpy as np
from scipy.spatial.transform import Rotation

SCHEMA = 'robodojo_privileged_scene_v1'


def array(value, shape=None):
    if hasattr(value, 'detach'):
        value = value.detach().cpu().numpy()
    result = np.asarray(value, dtype=float)
    if shape is not None:
        result = result.reshape(shape)
    if not np.all(np.isfinite(result)):
        raise ValueError('Non-finite simulator geometry')
    return result


def pose(position, quaternion):
    position, quaternion = array(position, (3,)), array(quaternion, (4,))
    norm = np.linalg.norm(quaternion)
    if norm < 1e-8:
        raise ValueError('Zero simulator quaternion')
    return dict(position_world=position.tolist(), quaternion_wxyz=(quaternion / norm).tolist())


def empty_scene(step, origin=(0., 0., 0.)):
    return dict(schema=SCHEMA, source='simulator_current_state', step=step,
                coordinate_frame='world', position_unit='m', quaternion_order='wxyz',
                environment_origin_world=array(origin, (3,)).tolist(), objects={})


def _bbox(points, source):
    points = array(points).reshape(-1, 3)
    if not len(points):
        raise ValueError('Empty object geometry')
    low, high = points.min(axis=0), points.max(axis=0)
    return dict(source=source, center_world=((low + high) / 2).tolist(),
                min_world=low.tolist(), max_world=high.tolist(),
                extent_world=(high - low).tolist(), vertices_world=points.tolist())


def _geometry(obj, metadata, world_pose, deformable=False):
    geometry = metadata.get('geometry') or {}
    box = geometry.get('oriented_bbox') or geometry.get('aligned_bbox') or {}
    vertices = box.get('vertices')
    # A garment's root pose and undeformed asset box do not track its shape.
    if vertices is not None and not deformable and world_pose is not None:
        local = array(vertices).reshape(-1, 3)
        if len(local) != 8:
            raise ValueError('Asset bounding box must have eight vertices')
        scale = obj.get_local_scale() if callable(getattr(obj, 'get_local_scale', None)) else getattr(obj, 'scale', (1., 1., 1.))
        local = local * array(scale, (3,))
        rotation = Rotation.from_quat(np.asarray(world_pose['quaternion_wxyz'])[[1, 2, 3, 0]])
        world = rotation.apply(local) + world_pose['position_world']
        return _bbox(world, 'asset_bbox_transformed_by_current_pose_and_scale')
    if not callable(getattr(obj, 'sample_mesh_vertices', None)):
        raise ValueError('No current mesh or asset bounding box available')
    points = array(obj.sample_mesh_vertices()[0]).reshape(-1, 3)
    if not len(points):
        raise ValueError('Empty current mesh')
    low, high = points.min(axis=0), points.max(axis=0)
    corners = np.asarray(list(product(*zip(low, high))))
    return _bbox(corners, 'current_mesh_world_axis_aligned_bbox')


def capture_privileged_scene(env, expected_step, env_idx=0):
    """Read layout-manager live state at the same stationary boundary as RGB."""
    if int(env.take_action_cnt[env_idx]) != expected_step:
        raise RuntimeError('Privileged scene does not match the RGB capture step')
    manager = env.scene_manager.layout_manager
    origin = array(env.scene_manager.env_origins[env_idx], (3,))
    result = empty_scene(expected_step, origin)
    for label in sorted(set(manager.get_labels_by_prefix(env_idx, ''))):
        instance = manager.get_instance_name(env_idx, label)
        obj = manager.get_scene_object(env_idx, instance)
        metadata = manager.get_instance_metadata(env_idx=env_idx, inst_name=instance) or {}
        record = dict(instance_name=instance, category=metadata.get('model_name'),
                      object_type=manager.instance_type_by_env[env_idx].get(instance))
        descriptions = metadata.get('description') or []
        if isinstance(descriptions, str):
            descriptions = [descriptions]
        record['asset_descriptions'] = [text for text in descriptions if isinstance(text, str)][:3]
        unavailable = {}
        try:
            position, quaternion = manager.get_instance_pose(env_idx=env_idx, inst_name=instance, relative=True)
            if position is None or quaternion is None:
                raise ValueError('No live root pose for this object type')
            # Read relative=True for every type: geometry/garment implementations
            # do not consistently honor relative=False. Convert explicitly here.
            record.update(pose(array(position, (3,)) + origin, quaternion))
        except (ValueError, TypeError, AttributeError, RuntimeError) as error:
            unavailable['pose'] = str(error)
        try:
            record['bbox'] = _geometry(obj, metadata, record if 'position_world' in record else None,
                                       deformable=record['object_type'] in ('garment', 'fluid'))
        except (ValueError, TypeError, AttributeError, RuntimeError) as error:
            unavailable['bbox'] = str(error)
        for kind in ('active', 'passive'):
            section = metadata.get(kind) or {}
            for field, getter in (('functional_points', 'get_functional_points'), ('support_points', 'get_support_points')):
                entries = section.get('functional' if field == 'functional_points' else 'support') or {}
                for tag in sorted(entries):
                    try:
                        scale = obj.get_local_scale() if callable(getattr(obj, 'get_local_scale', None)) else getattr(obj, 'scale', (1., 1., 1.))
                        if not np.allclose(array(scale, (3,)), 1.):
                            raise ValueError('Native point metadata does not apply instance scaling')
                        arguments = dict(tag=tag, type=kind, config=metadata, ret='list',
                                         obj_name=instance, env_idx=env_idx)
                        if field == 'support_points':
                            arguments['usd_path'] = getattr(obj, 'usd_path', None)
                        points = getattr(manager, getter)(**arguments)
                        radii = None
                        if field == 'support_points':
                            if points is None:
                                raise ValueError('Support points unavailable')
                            points, radii = points
                        values = []
                        for point in points:
                            point = array(point, (7,))
                            values.append(pose(point[:3] + origin, point[3:]))
                        if not values:
                            raise ValueError('Point metadata did not yield current poses')
                        value = dict(poses_world=values)
                        if radii is not None:
                            value['radii_m'] = array(radii).tolist()
                        record.setdefault(field, {}).setdefault(kind, {})[tag] = value
                    except (ValueError, TypeError, AttributeError, RuntimeError, KeyError) as error:
                        unavailable[f'{field}.{kind}.{tag}'] = str(error)
        if unavailable:
            record['unavailable'] = unavailable
        result['objects'][label] = record
    if int(env.take_action_cnt[env_idx]) != expected_step:
        raise RuntimeError('Physics advanced while reading privileged scene')
    validate_privileged_scene(result, expected_step)
    return result


def validate_privileged_scene(scene, step):
    if (not isinstance(scene, dict) or scene.get('schema') != SCHEMA
            or type(scene.get('step')) is not int or scene['step'] != step
            or scene.get('source') != 'simulator_current_state' or scene.get('coordinate_frame') != 'world'
            or scene.get('position_unit') != 'm' or scene.get('quaternion_order') != 'wxyz'
            or not isinstance(scene.get('objects'), dict)):
        raise ValueError('Invalid or stale privileged scene')
    array(scene['environment_origin_world'], (3,))
    for label, record in scene['objects'].items():
        if not isinstance(label, str) or not isinstance(record, dict):
            raise ValueError('Invalid privileged object')
        if 'position_world' in record:
            pose(record['position_world'], record['quaternion_wxyz'])
        if 'bbox' in record:
            box = record['bbox']
            points = array(box['vertices_world'], (8, 3))
            for key in ('center_world', 'min_world', 'max_world', 'extent_world'):
                array(box[key], (3,))
            if np.any(array(box['extent_world']) < 0) or not len(points):
                raise ValueError('Invalid privileged bounding box')
        for field in ('functional_points', 'support_points'):
            for entries in record.get(field, {}).values():
                for value in entries.values():
                    for point in value['poses_world']:
                        pose(point['position_world'], point['quaternion_wxyz'])
    return deepcopy(scene)
