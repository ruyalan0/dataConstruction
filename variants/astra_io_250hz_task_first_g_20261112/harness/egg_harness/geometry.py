"""RGB ray/learned plane intersections; no simulator imports."""
import numpy as np
from scipy.spatial.transform import Rotation


def unproject(pixel, z, camera):
    k = np.asarray(camera['intrinsic'])
    t = np.asarray(camera['world_from_camera'])
    u, v = pixel
    if not 0 <= u < 640 or not 0 <= v < 480:
        raise ValueError('Pixel outside image')
    ray = t[:3, :3] @ [(u-k[0, 2])/k[0, 0], -(v-k[1, 2])/k[1, 1], -1]
    distance = (z-t[2, 3])/ray[2]
    if distance <= 0:
        raise ValueError('Plane behind camera')
    return t[:3, 3]+distance*ray


def rotation(pitch, yaw):
    return Rotation.from_euler('z', yaw, degrees=True)*Rotation.from_euler('y', pitch, degrees=True)


def quaternion(r):
    return r.as_quat()[[3, 0, 1, 2]].tolist()
