"""Interrupt a commanded segment using only achieved robot state."""
import numpy as np


def evaluate_guard(state, action, guard, reference_opening):
    arm=guard['arm']
    error=float(np.linalg.norm(np.asarray(state[arm+'_ee_pose'][:3])-action[arm+'_ee_pose'][:3]))
    if error>guard['tracking_error_m']:
        return {'reason':'tracking_error','error_m':error}
    if guard['watch_grip']:
        opening=float(state[arm+'_gripper_measured_joints'][0])
        if abs(opening-reference_opening)>guard['opening_change']:
            return {'reason':'gripper_opening_change','reference':reference_opening,'opening':opening}
    return None
