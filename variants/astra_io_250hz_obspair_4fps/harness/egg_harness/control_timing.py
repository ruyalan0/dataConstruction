"""Separate the 250 Hz actuator clock from RoboDojo's 25 Hz task clock."""
import math

CONTROL_HZ = 250
OBSERVATION_HZ = 25
TICKS_PER_TASK_STEP = CONTROL_HZ // OBSERVATION_HZ
MAX_ACTION_STEPS = 10 * CONTROL_HZ


def timing_record():
    return dict(protocol='timed_joint_v1', action_hz=CONTROL_HZ,
                action_interval_seconds=1 / CONTROL_HZ, physics_hz=CONTROL_HZ,
                physics_steps_per_action=1, observation_hz=OBSERVATION_HZ,
                task_step_seconds=1 / OBSERVATION_HZ,
                control_ticks_per_task_step=TICKS_PER_TASK_STEP,
                render_interval=10, max_action_steps=MAX_ACTION_STEPS,
                steps_unit='250 Hz control ticks', settle_unit='250 Hz control ticks')


def validate_timing_record(record):
    if record != timing_record():
        raise ValueError('Expected timed 250 Hz joints with the original 25 Hz task horizon')


def configure_timing(config):
    if (not math.isclose(float(config.sim.dt), 1 / CONTROL_HZ)
            or config.sim.decimation != 1):
        raise ValueError('Timed joint control requires dt=0.004 and decimation=1')
    if config.eval_cfg.observation.collect_freq != OBSERVATION_HZ:
        raise ValueError('Preserve the original 25 Hz benchmark clock')
    config.camera.default_frequency = OBSERVATION_HZ
    config.sim.render_interval = 10
    return timing_record()


def verify_environment_timing(env):
    if (env.obs_manager.collect_freq != OBSERVATION_HZ
            or not math.isclose(float(env.obs_manager.collect_interval), TICKS_PER_TASK_STEP)
            or not math.isclose(float(env.dt), 1 / CONTROL_HZ)
            or not math.isclose(float(env.sim.unwrapped.step_dt), 1 / CONTROL_HZ)):
        raise ValueError('Expected one 4 ms control tick and ten ticks per task step')
