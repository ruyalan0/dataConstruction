"""Flush render latency at a stationary action boundary; never step physics."""


def capture_public_observation(env):
    step = int(env.take_action_cnt[0])
    # Tiled RGB can otherwise expose the preceding rendered pose on the first read.
    env.render()
    env.render()
    observation = env.get_obs()
    if int(env.take_action_cnt[0]) != step:
        raise RuntimeError('Native step changed during public RGB capture')
    return observation


def validate_camera_rig(env, geometry, observation):
    """Check hardware configuration only; expose no simulator matrices to policy."""
    import numpy as np
    expected = geometry.cameras(observation)
    manager = env.camera_manager
    if set(manager.camera_names[0]) != set(expected):
        raise ValueError('Camera names differ from qualified rig')
    for index,name in enumerate(manager.camera_names[0]):
        camera = expected[name]
        if not np.allclose(manager.get_camera_intrinsics(index,0),camera['intrinsic'],rtol=0,atol=1e-4):
            raise ValueError('Camera intrinsics differ from qualified rig: '+name)
        if not np.allclose(manager.get_camera_extrinsics(index,0),camera['world_from_camera'],rtol=0,atol=1e-4):
            raise ValueError('Camera mounting differs from qualified rig: '+name)
        if list(manager.cameras[0][index].get_resolution()) != camera['resolution']:
            raise ValueError('Camera resolution differs from qualified rig: '+name)
    return geometry.calibration_id
