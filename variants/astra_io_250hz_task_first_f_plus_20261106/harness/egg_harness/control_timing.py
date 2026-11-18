"""Describe physics and task clocks separately from goal submissions."""
import math
from .clocks import (PHYSICS_HZ, TASK_HZ, OBSERVATION_HZ,
                     PHYSICS_TICKS_PER_TASK_STEP, TIMING_PROTOCOL, clock_metadata)
from .joint_goal import controller_settings


def timing_record():
    return dict(**clock_metadata(), protocol=TIMING_PROTOCOL,
                physics_tick_seconds=1 / PHYSICS_HZ,
                task_step_seconds=1 / TASK_HZ,
                physics_ticks_per_task_step=PHYSICS_TICKS_PER_TASK_STEP,
                render_interval=PHYSICS_HZ // OBSERVATION_HZ,
                goal_controller=controller_settings(),
                steps_unit='executed physics ticks; not model-requested steps')


def validate_timing_record(record):
    if record != timing_record():
        raise ValueError('Expected synchronized joint goals, 250 Hz physics and the original 25 Hz task horizon')


def configure_timing(config):
    if (not math.isclose(float(config.sim.dt), 1 / PHYSICS_HZ)
            or config.sim.decimation != 1):
        raise ValueError('Joint-goal physics requires dt=0.004 and decimation=1')
    if config.eval_cfg.observation.collect_freq != OBSERVATION_HZ:
        raise ValueError('Preserve the original 25 Hz benchmark clock')
    config.camera.default_frequency = OBSERVATION_HZ
    config.sim.render_interval = PHYSICS_HZ // OBSERVATION_HZ
    return timing_record()


def verify_environment_timing(env):
    if (env.obs_manager.collect_freq != OBSERVATION_HZ
            or not math.isclose(float(env.obs_manager.collect_interval), PHYSICS_HZ // OBSERVATION_HZ)
            or not math.isclose(float(env.dt), 1 / PHYSICS_HZ)
            or not math.isclose(float(env.sim.unwrapped.step_dt), 1 / PHYSICS_HZ)):
        raise ValueError('Expected one 4 ms physics tick and ten ticks per task step')
