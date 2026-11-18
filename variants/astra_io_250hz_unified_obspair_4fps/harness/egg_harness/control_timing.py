"""One 250 Hz clock for physics, actions, RGB, task counts and rewards."""
import math

CONTROL_HZ = 250
OBSERVATION_HZ = 250
TASK_HZ = 250
REWARD_HZ = 250
CAMERA_HZ = 250
TICKS_PER_TASK_STEP = 1
MAX_ACTION_STEPS = 10 * CONTROL_HZ
PROTOCOL = 'timed_joint_250hz_unified_v1'
# Conversion of the unchanged upstream task's step limit, not a running clock.
SOURCE_BUDGET_HZ = 25


def rescale_task_budget(source_steps):
    if type(source_steps) is not int or source_steps <= 0:
        raise ValueError('Expected a positive upstream task step budget')
    ticks = source_steps * TASK_HZ / SOURCE_BUDGET_HZ
    if not ticks.is_integer():
        raise ValueError('Upstream task duration must contain whole 4 ms ticks')
    return int(ticks)


def timing_record():
    return dict(protocol=PROTOCOL, action_hz=CONTROL_HZ,
                action_interval_seconds=1 / CONTROL_HZ, physics_hz=CONTROL_HZ,
                physics_steps_per_action=1, observation_hz=OBSERVATION_HZ,
                camera_hz=CAMERA_HZ, task_hz=TASK_HZ, reward_hz=REWARD_HZ,
                task_step_seconds=1 / TASK_HZ,
                control_ticks_per_task_step=TICKS_PER_TASK_STEP,
                render_interval=1, max_action_steps=MAX_ACTION_STEPS,
                steps_unit='250 Hz control ticks', settle_unit='250 Hz control ticks')


def validate_timing_record(record):
    if record != timing_record():
        raise ValueError('Expected unified 250 Hz action/observation/camera/task/reward timing')


def configure_timing(config):
    if (not math.isclose(float(config.sim.dt), 1 / CONTROL_HZ)
            or config.sim.decimation != 1):
        raise ValueError('Unified joint control requires dt=0.004 and decimation=1')
    config.eval_cfg.observation.collect_freq = OBSERVATION_HZ
    config.camera.default_frequency = CAMERA_HZ
    config.sim.render_interval = 1
    return timing_record()


def verify_environment_timing(env):
    if (env.obs_manager.collect_freq != OBSERVATION_HZ
            or not math.isclose(float(env.obs_manager.collect_interval), 1.)
            or not math.isclose(float(env.dt), 1 / CONTROL_HZ)
            or not math.isclose(float(env.sim.unwrapped.step_dt), 1 / CONTROL_HZ)):
        raise ValueError('Expected one 4 ms tick per action, observation and task step')
