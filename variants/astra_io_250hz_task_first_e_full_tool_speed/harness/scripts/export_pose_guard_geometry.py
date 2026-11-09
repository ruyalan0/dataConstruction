"""Export static X5 collision geometry from its USD asset; never starts simulation."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.spatial import ConvexHull
from scipy.spatial.transform import Rotation
from pxr import Usd, UsdGeom, UsdPhysics


def frame(position, quaternion):
    result = np.eye(4)
    result[:3, 3] = np.asarray(position)
    result[:3, :3] = Rotation.from_quat(
        [*quaternion.GetImaginary(), quaternion.GetReal()]).as_matrix()
    return result


def export(asset, destination):
    stage = Usd.Stage.Open(str(asset))
    if stage is None:
        raise ValueError('Cannot open X5 USD asset')
    cache = UsdGeom.XformCache()
    bodies = {'base_link', *(f'link{i}' for i in range(1, 7))}
    joints, ignored = [], []
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue
        joint = UsdPhysics.Joint(prim)
        parents, children = joint.GetBody0Rel().GetTargets(), joint.GetBody1Rel().GetTargets()
        if not parents or not children:
            continue
        parent, child = parents[0].name, children[0].name
        if parent not in bodies or child not in bodies:
            continue
        if prim.GetTypeName() != 'PhysicsRevoluteJoint':
            raise ValueError('Expected revolute X5 arm joint')
        if not joint.GetCollisionEnabledAttr().Get():
            ignored.append([parent, child])
        joints.append(dict(name=prim.GetName(), parent=parent, child=child,
            before=frame(joint.GetLocalPos0Attr().Get(), joint.GetLocalRot0Attr().Get()).tolist(),
            after=frame(joint.GetLocalPos1Attr().Get(), joint.GetLocalRot1Attr().Get()).tolist(),
            axis=prim.GetAttribute('physics:axis').Get()))
    joints.sort(key=lambda row: int(row['name'].removeprefix('joint')))
    if [row['name'] for row in joints] != [f'joint{i}' for i in range(1, 7)]:
        raise ValueError('Unexpected X5 joint mapping')
    shapes, arrays = [], {}
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        ancestor, collision = prim, None
        while ancestor and str(ancestor.GetPath()) != '/':
            if ancestor.HasAPI(UsdPhysics.CollisionAPI):
                collision = ancestor
                break
            ancestor = ancestor.GetParent()
        if collision is None or not UsdPhysics.CollisionAPI(collision).GetCollisionEnabledAttr().Get():
            continue
        body = prim
        while body and not body.HasAPI(UsdPhysics.RigidBodyAPI):
            body = body.GetParent()
        if not body or body.GetName() not in bodies:
            continue
        transform = np.asarray(cache.ComputeRelativeTransform(prim, body)[0]).T
        points = np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get(), dtype=float)
        points = np.unique(points @ transform[:3, :3].T + transform[:3, 3], axis=0)
        hull = ConvexHull(points)
        key = f'shape{len(shapes)}'
        arrays[key + '_points'] = points
        arrays[key + '_hull'] = points[hull.vertices]
        arrays[key + '_planes'] = hull.equations
        shapes.append(dict(key=key, body=body.GetName(), mesh=str(prim.GetPath()),
                           approximation=collision.GetAttribute('physics:approximation').Get()))
    if {row['body'] for row in shapes} != bodies:
        raise ValueError('Missing X5 collision mesh')
    destination.mkdir(parents=True, exist_ok=True)
    data = destination / 'x5-collision-geometry.npz'
    np.savez_compressed(data, **arrays)
    record = dict(schema='x5_usd_collision_geometry_v1', source_asset=asset.name,
        source_asset_sha256=hashlib.sha256(asset.read_bytes()).hexdigest(),
        geometry_sha256=hashlib.sha256(data.read_bytes()).hexdigest(),
        joints=joints, ignored_self_pairs=ignored, shapes=shapes,
        scope='X5 base_link and link1..6; excludes gripper, cameras, carried objects and environment')
    (destination / 'x5-collision-geometry.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(dict(shapes=len(shapes), vertices=sum(len(a) for k,a in arrays.items() if k.endswith('_points')),
                         approximations={s['body']:s['approximation'] for s in shapes}, data_bytes=data.stat().st_size)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(args.asset, args.output)
