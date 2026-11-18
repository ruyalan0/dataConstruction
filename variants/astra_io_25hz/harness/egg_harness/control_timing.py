"""Select real action intervals without changing the original RoboDojo source/YAML."""
import math

CONTROL_HZ = 25
PHYSICS_HZ = 250
PHYSICS_STEPS_PER_ACTION = PHYSICS_HZ // CONTROL_HZ


def timing_record():
    return dict(action_hz=CONTROL_HZ, action_interval_seconds=1 / CONTROL_HZ,
                physics_hz=PHYSICS_HZ, physics_steps_per_action=PHYSICS_STEPS_PER_ACTION, render_interval=1,
                steps_unit='25 Hz action intervals', settle_unit='25 Hz action intervals')


def validate_timing_record(record):
    if record != timing_record():
        raise ValueError('Expected the isolated 25 Hz action timing contract')


def configure_timing(config):
    dt = float(config.sim.dt)
    if not math.isclose(dt, 1 / PHYSICS_HZ) or config.sim.decimation != 1:
        raise ValueError('25 Hz action variant requires 250 Hz physics: dt=0.004 and decimation=1')
    config.eval_cfg.observation.collect_freq = CONTROL_HZ
    config.camera.default_frequency = CONTROL_HZ
    config.sim.render_interval = 1
    return timing_record()


def verify_environment_timing(env):
    if (env.obs_manager.collect_freq != CONTROL_HZ
            or not math.isclose(float(env.obs_manager.collect_interval), PHYSICS_STEPS_PER_ACTION)
            or not math.isclose(float(env.dt), 1 / PHYSICS_HZ)):
        raise ValueError('Environment did not apply ten 4 ms physics cycles per 40 ms action')
