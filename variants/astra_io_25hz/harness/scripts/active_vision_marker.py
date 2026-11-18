"""Developer-only static visual fixture for a real TCP observation-move test."""
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from egg_harness.multiview import MultiViewGeometry
from public_capture import capture_public_observation


def prepare_marker(env, output):
    from isaacsim.core.utils.stage import get_current_stage
    from pxr import Gf, Sdf, UsdGeom, UsdShade

    geometry = MultiViewGeometry()
    observation = capture_public_observation(env)
    camera = geometry.cameras(observation)['cam_left_wrist']
    transform = np.asarray(camera['world_from_camera'])
    point = transform[:3, 3] - .20 * transform[:3, 2]
    stage = get_current_stage()
    sphere = UsdGeom.Sphere.Define(stage, '/World/ActiveVisionMarker')
    sphere.CreateRadiusAttr(.003)
    sphere.AddTranslateOp().Set(Gf.Vec3d(*point))
    material = UsdShade.Material.Define(stage, '/World/ActiveVisionMarkerMaterial')
    shader = UsdShade.Shader.Define(stage, '/World/ActiveVisionMarkerMaterial/Shader')
    shader.CreateIdAttr('UsdPreviewSurface')
    shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1, 0, 1))
    shader.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(5, 0, 5))
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
    UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(material)
    for _ in range(5):
        env.render()
    (output / 'qualification-fixture.json').write_text(json.dumps({
        'audience': 'developer_only', 'world_point': point.tolist(),
        'radius_m': .003, 'motion': 'fixed world pose, visual only, no collision',
        'scope': 'Synthetic observation-move qualification; not task evaluation'}, indent=2))
