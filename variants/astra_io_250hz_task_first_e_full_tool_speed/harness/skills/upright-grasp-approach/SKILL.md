---
name: upright-grasp-approach
description: Select measured joint configurations that leave room for approach, descent and withdrawal.
---

Distinguish healthy compact standby from a configuration that obstructs the next required motion. A folded arm away from the target may have enough space to unfold during approach; horizontal link2 alone is not a reason to reconfigure. Near a target or table, the same compact posture may leave insufficient clearance. A raised elbow can help when the resulting arm and gripper sweeps remain clear.

Use arm_posture, paired RGB and measured motion as context. link2_tilt_from_vertical_deg describes the joint2-to-joint3 link, not an optimization target or success criterion. Identify the blocked approach, descent or withdrawal before selecting an adjustment. Choose an ordinary control_arms joint goal for a clear retreat or reorientation, then inspect whether the next required motion becomes feasible.

Move the other arm only when needed to clear the route. Preserve a working grasp even if its elbow appears relatively horizontal. If carrying prevents a useful reconfiguration, first use reachable visible support and confirm release. For handoff, leave room for the receiving approach and both wrists to withdraw; keep unsupported objects held.

Remember failed adjustment tests. If an adjustment followed by another approach still fails, change approach geometry or arm instead of repeating the same pair. Continue pursuing the required object and public task; reorder multiple objects only when the task permits it. Reserve time for task-required release, withdrawal and home return.
