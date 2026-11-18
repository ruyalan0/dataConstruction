"""Current-frame RGB triangulation using fixed calibration and public wrist poses."""
from itertools import combinations
from pathlib import Path
import json

import numpy as np
from scipy.optimize import least_squares

from .kinematics import pose_matrix

CAMERAS = ('cam_head', 'cam_left_wrist', 'cam_right_wrist')
DEFAULT_CALIBRATION = Path(__file__).resolve().parents[1] / 'memory/multiview-calibration.json'
MIN_ANGLE_DEG = 10.
MAX_REPROJECTION_PX = 3.
MAX_RAY_DISTANCE_M = .015
PIXEL_SIGMA_PX = 2.
MAX_PIXEL_SENSITIVITY_M = .020


def validate_camera(camera):
    k = np.asarray(camera['intrinsic'], float)
    t = np.asarray(camera['world_from_camera'], float)
    if k.shape != (3, 3) or t.shape != (4, 4) or not np.isfinite(k).all() or not np.isfinite(t).all():
        raise ValueError('Invalid camera matrix')
    if k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1]) or k[0,1] != 0 or k[1,0] != 0:
        raise ValueError('Unsupported intrinsic matrix')
    if not np.allclose(t[3], [0,0,0,1]) or not np.allclose(t[:3,:3].T @ t[:3,:3], np.eye(3), atol=1e-5) or abs(np.linalg.det(t[:3,:3])-1)>1e-5:
        raise ValueError('Invalid camera rigid transform')
    if camera['resolution'] != [640, 480]:
        raise ValueError('Unqualified image resolution')
    return k, t


def ray(pixel, camera):
    pixel = np.asarray(pixel, float)
    if pixel.shape != (2,) or not np.isfinite(pixel).all() or not (0 <= pixel[0] < 640 and 0 <= pixel[1] < 480):
        raise ValueError('Pixel outside 640x480 image')
    k, t = validate_camera(camera)
    direction = t[:3,:3] @ [(pixel[0]-k[0,2])/k[0,0], -(pixel[1]-k[1,2])/k[1,1], -1.]
    return t[:3,3], direction / np.linalg.norm(direction)


def project(point, camera):
    k, t = validate_camera(camera)
    local = t[:3,:3].T @ (np.asarray(point)-t[:3,3])
    if local[2] >= -1e-6:
        raise ValueError('Triangulated point behind a camera')
    return np.array([k[0,0]*local[0]/-local[2]+k[0,2], k[1,1]*local[1]/local[2]+k[1,2]])


def triangulate(pixels, cameras):
    """Fit every supplied view; never silently discard a disagreeing third view."""
    if len(pixels) != len(cameras) or not 2 <= len(pixels) <= 3:
        raise ValueError('Triangulation requires two or three views')
    rays = [ray(pixel, camera) for pixel, camera in zip(pixels, cameras)]
    angles = [float(np.degrees(np.arccos(np.clip(abs(np.dot(a[1],b[1])),0,1)))) for a,b in combinations(rays,2)]
    if max(angles) < MIN_ANGLE_DEG:
        raise ValueError('Insufficient parallax: best ray angle below 10 degrees; acquire a separated view')
    normals = [np.eye(3)-np.outer(d,d) for _,d in rays]
    a = np.sum(normals,axis=0)
    if np.linalg.cond(a)>1e4:
        raise ValueError('Ill-conditioned viewing geometry')
    initial = np.linalg.solve(a, np.sum([n@o for n,(o,_) in zip(normals,rays)],axis=0))
    targets = np.asarray(pixels)
    def residual(point):
        return (np.array([project(point,c) for c in cameras])-targets).ravel()
    fit = least_squares(residual,initial,loss='linear',max_nfev=30,ftol=1e-10,xtol=1e-10,gtol=1e-10)
    if not fit.success or not np.isfinite(fit.x).all():
        raise ValueError('Triangulation optimizer did not converge')
    residuals = residual(fit.x).reshape(-1,2)
    errors = np.linalg.norm(residuals,axis=1)
    if max(errors)>MAX_REPROJECTION_PX:
        raise ValueError(f'Inconsistent matched pixels: maximum reprojection error {max(errors):.2f}px exceeds 3px')
    distances = [float(np.linalg.norm(n@(fit.x-o))) for n,(o,_) in zip(normals,rays)]
    if max(distances)>MAX_RAY_DISTANCE_M:
        raise ValueError('Matched rays disagree by more than 15mm')
    information = fit.jac.T @ fit.jac
    if np.linalg.cond(information)>1e5:
        raise ValueError('Unstable depth sensitivity')
    covariance = np.linalg.inv(information)*PIXEL_SIGMA_PX**2
    sensitivity = float(np.sqrt(np.linalg.eigvalsh(covariance)[-1]))
    if sensitivity>MAX_PIXEL_SENSITIVITY_M:
        raise ValueError(f'Pixel-noise sensitivity {sensitivity*1000:.1f}mm exceeds 20mm; improve baseline or move closer')
    return {'position':fit.x.tolist(), 'reprojection_error_px':errors.tolist(),
            'reprojection_rms_px':float(np.sqrt(np.mean(errors**2))),
            'ray_distance_m':distances, 'pair_ray_angles_deg':angles,
            'pixel_noise_sensitivity':{'assumed_independent_pixel_sigma_px':PIXEL_SIGMA_PX,
                'axis_sigma_m':np.sqrt(np.diag(covariance)).tolist(), 'largest_axis_sigma_m':sensitivity,
                'scope':'Local propagation of assumed pixel noise only; excludes calibration error and wrong feature identity. Not a measured confidence bound.'},
            'interpretation':'Estimated common visible feature, not object center, contact target, or ground truth.'}


class MultiViewGeometry:
    def __init__(self, path=DEFAULT_CALIBRATION):
        raw = Path(path).read_bytes()
        self.calibration = json.loads(raw)
        import hashlib
        self.calibration_id = hashlib.sha256(raw).hexdigest()
        if self.calibration['schema_version'] != 1 or self.calibration['status'] != 'render_qualified':
            raise ValueError('Multi-view calibration is not qualified')
        if set(self.calibration['cameras']) != set(CAMERAS):
            raise ValueError('Calibration must define all three cameras')
        for name,camera in self.calibration['cameras'].items():
            t = camera['world_from_camera'] if name == 'cam_head' else camera['ee_from_camera']
            validate_camera({**camera,'world_from_camera':t})

    def cameras(self, observation):
        result = {}
        for name,calibration in self.calibration['cameras'].items():
            if name == 'cam_head':
                result[name] = calibration
            else:
                arm = 'left' if name == 'cam_left_wrist' else 'right'
                t = pose_matrix(observation['state'][arm+'_ee_pose']) @ np.asarray(calibration['ee_from_camera'])
                result[name] = {**calibration,'world_from_camera':t.tolist()}
        return result

    def locate(self, request, observation):
        if observation.get('camera_calibration_id') != self.calibration_id:
            raise ValueError('Observation lacks matching camera qualification; restart with the qualified simulator version')
        if type(request['step']) is not int or request['step'] != observation['step']:
            raise ValueError('Triangulation requires the current observation step; use fresh corresponding views')
        matches = request['views']
        names = [v['camera'] for v in matches]
        if not 2<=len(matches)<=3 or len(set(names))!=len(names) or not set(names)<=set(CAMERAS):
            raise ValueError('Select two or three distinct current cameras')
        from PIL import Image
        for name in names:
            with Image.open(observation['cameras'][name]) as picture:
                if list(picture.size)!=self.calibration['cameras'][name]['resolution']:
                    raise ValueError('Current RGB resolution differs from calibration')
        cameras = self.cameras(observation)
        result = triangulate([v['pixel'] for v in matches],[cameras[name] for name in names])
        return {**result,'cameras':names,'step':observation['step'],'calibration_id':self.calibration_id,
                'feature':request['feature'],'native_steps_executed':0}
