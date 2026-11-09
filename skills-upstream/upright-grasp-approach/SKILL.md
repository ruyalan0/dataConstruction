---
name: upright-grasp-approach
description: Plan room for pickup and handoff, adjusting posture when it obstructs approach, descent or withdrawal while allowing healthy compact standby.
---

Distinguish three configurations using public geometry, head/wrist RGB and measured motion:

- Healthy compact standby: the arm folds on its own side away from the target, with space and a feasible path to unfold during approach. Horizontal link2 alone does not require recovery.
- Constrained folding: the wrist is near the target or table while link2/link3 leave insufficient room to approach, descend or withdraw. Holding the same wrist pose can trap local IK; create space before repeating the blocked motion.
- Raised-elbow approach: link2 is more upright and link3 extends toward the target, leaving room for descent, grasp and lift. Choose this when it helps the next motion.

Before adjusting, identify the obstructed next motion and evidence: visible lack of clearance, a restricted approach path, or measured failure to advance. Use arm_posture and previous_arm_posture as geometric context. link2_tilt_from_vertical_deg describes joint2-to-joint3 geometry; it is not a trigger, optimization objective or success criterion.

Select a short empty-hand retreat, reorientation, or available posture action with a visibly clear swept path. Move the other arm only when it obstructs that path. Inspect actual motion and fresh views, then re-estimate the next wrist target. Judge adjustment by whether approach, descent or withdrawal resumes with adequate clearance. If blocked, change the approach, wrist orientation or arm instead of repeating angle reduction. Preserve a working grasp even with comparatively horizontal link2.

For handoff, select a supported placement and receiving approach with room for both arms to unfold and withdraw. Confirm support and release before withdrawal and receiving descent. Separated swept paths may run simultaneously.

While carrying, preserve grip and verify retention; use reachable support before reconfiguration if uncertain. The full native joint interface supports carrying with bounded joint actions; inspect the resulting wrist and object motion. If using the legacy direct-posture tool, its empty-open-hand requirement still applies. Follow the selected interface's action limits and observation cadence.

Remember failed adjustment tests across calls and continuations. When a shoulder prefix followed by a short approach still fails to descend, change the approach geometry or receiving arm before repeating that pair. Select the next object with a feasible approach when the present object remains blocked; preserve completed placements and reserve steps for withdrawal and the required finish.
