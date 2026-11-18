"""Isolated developer camera qualification. Never called by the policy."""
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
from scipy.ndimage import label

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from egg_harness.kinematics import pose_matrix
from scripts.public_capture import capture_public_observation


def plain(value):
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, np.generic): return value.item()
    raise TypeError(type(value).__name__)


def project(points, camera):
    transform = np.asarray(camera['world_from_camera'])
    local = (np.asarray(points) - transform[:3, 3]) @ transform[:3, :3]
    k = np.asarray(camera['intrinsic'])
    uv = np.column_stack([k[0, 0] * local[:, 0] / -local[:, 2] + k[0, 2],
                          k[1, 1] * local[:, 1] / local[:, 2] + k[1, 2]])
    valid = (local[:, 2] < -.08) & (uv[:, 0] > 40) & (uv[:, 0] < 600) & (uv[:, 1] > 40) & (uv[:, 1] < 440)
    return uv, valid


def run_calibration(env, output):
    from isaacsim.core.utils.stage import get_current_stage
    from pxr import Gf, Sdf, UsdGeom, UsdShade

    def save(name, value):
        (output / name).write_text(json.dumps(value, indent=2, default=plain, allow_nan=False))

    def capture():
        for _ in range(30): env.sim.sim_step()
        return capture_public_observation(env)

    def snapshot(obs):
        result = {}
        cm = env.camera_manager
        for i, name in enumerate(cm.camera_names[0]):
            camera = cm.cameras[0][i]
            t = cm.get_camera_extrinsics(i, 0)
            data = {'intrinsic': np.asarray(camera.get_intrinsics_matrix()),
                    'metadata_intrinsic': cm.get_camera_intrinsics(i, 0),
                    'resolution': list(camera.get_resolution()), 'world_from_camera': t}
            if name != 'cam_head':
                arm = 'left' if name == 'cam_left_wrist' else 'right'
                data['ee_from_camera'] = np.linalg.inv(pose_matrix(obs['state'][arm + '_ee_pose'])) @ t
            result[name] = data
        return result

    observations = []
    for index, delta in enumerate((0., .10, -.10)):
        if index:
            state = env.get_obs()['state']
            native = {}
            for arm in ('left', 'right'):
                joints = np.array(state[arm + '_arm_joint_state'])
                joints[4] += delta
                native[arm + '_arm_joint_state'] = joints
                native[arm + '_ee_joint_state'] = np.array([1.])
            env.take_action(native)
        obs = capture()
        cams = snapshot(obs)
        observations.append({'state': obs['state'], 'cameras': cams})
        save('camera-observations.json', observations)
        for name, image in obs['vision'].items(): Image.fromarray(image['color']).save(output / f'pose-{index}-{name}.png')

    cameras = observations[-1]['cameras']
    stage = get_current_stage()
    sphere = UsdGeom.Sphere.Define(stage, '/World/QualificationMarker')
    sphere.CreateRadiusAttr(.005)
    position = sphere.AddTranslateOp()
    position.Set(Gf.Vec3d(0, 0, -10))
    material = UsdShade.Material.Define(stage, '/World/QualificationMarkerMaterial')
    shader = UsdShade.Shader.Define(stage, '/World/QualificationMarkerMaterial/Shader')
    shader.CreateIdAttr('UsdPreviewSurface')
    shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1, 0, 1))
    shader.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(5, 0, 5))
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
    UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(material)
    background = capture()
    # Known marker positions are developer-only fixtures. Detection uses RGB changes.
    grid = np.array(np.meshgrid(np.linspace(-.6,.6,17), np.linspace(-.45,.6,17), np.linspace(.80,1.20,9))).reshape(3,-1).T
    projections = {name: project(grid, cam) for name, cam in cameras.items()}
    visible_count = np.sum([v[1] for v in projections.values()], axis=0)
    candidates = grid[visible_count >= 2]
    if len(candidates) < 8:
        raise ValueError('Insufficient shared views for marker qualification')
    selected = candidates[np.linspace(0, len(candidates)-1, 12, dtype=int)]
    results = []
    for index, point in enumerate(selected):
        position.Set(Gf.Vec3d(*point))
        obs = capture()
        row = {'world_point': point, 'observations': {}, 'state': obs['state'], 'cameras': snapshot(obs)}
        for name, frame in obs['vision'].items():
            rgb = np.asarray(frame['color'])[:,:,:3].astype(float)
            before = np.asarray(background['vision'][name]['color'])[:,:,:3].astype(float)
            mask = (rgb[:,:,0] > 140) & (rgb[:,:,2] > 140) & (rgb[:,:,1] < .6*np.minimum(rgb[:,:,0],rgb[:,:,2])) & (np.max(rgb-before,axis=2)>60)
            regions, count = label(mask)
            components = [np.argwhere(regions == i) for i in range(1,count+1)]
            components = [c for c in components if len(c)>=4]
            Image.fromarray(frame['color']).save(output / f'marker-{index:02d}-{name}.png')
            if len(components) == 1:
                pixels = components[0]
                row['observations'][name] = {'pixel': pixels.mean(axis=0)[::-1]+.5, 'area': len(pixels)}
            else:
                row['observations'][name] = {'rejected_components': [len(c) for c in components]}
        results.append(row)
        save('rendered-markers.json', results)
        background = obs
    save('qualification-capture.json', {'status': 'captured', 'markers': len(results),
         'scope': 'Synthetic visual targets in isolated development environment; not task success or model correspondence accuracy'})
