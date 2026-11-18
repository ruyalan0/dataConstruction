"""Simulation clocks; model decisions have no fixed frequency or tick budget."""
PHYSICS_HZ = 250
TASK_HZ = 25
OBSERVATION_HZ = 25
PHYSICS_TICKS_PER_TASK_STEP = PHYSICS_HZ // TASK_HZ
TIMING_PROTOCOL = 'native_joint_goal_v3'
EXECUTION_MODE = 'synchronized_joint_goal_v3'


def clock_metadata():
    return dict(physics_hz=PHYSICS_HZ, controller_check_hz=PHYSICS_HZ,
                observation_hz=OBSERVATION_HZ, task_hz=TASK_HZ,
                timing_protocol=TIMING_PROTOCOL, execution_mode=EXECUTION_MODE,
                goal_submission='once_per_model_action',
                model_decision_schedule='after_execution_returns')
