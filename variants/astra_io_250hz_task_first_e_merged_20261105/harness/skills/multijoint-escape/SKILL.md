---
name: multijoint-escape
description: Adjust coordinated shoulder, elbow and wrist joints when an empty arm cannot approach, descend or withdraw; preview measured-base joint motion before execution.
---

Identify the obstructed next motion from achieved movement, contraction and RGB. Healthy compact standby alone is acceptable. Use Cartesian actions for ordinary approach, grasp and transport. This joint capability is for empty-hand reconfiguration; preserve a held object with existing Cartesian control until supported release is confirmed.

Use the supplied joint semantics and signed motion previews. Joint numbers name actuated joints, not links; positive increments can move the two arms differently because their measured bases differ. Choose a coordinated small shoulder/elbow/wrist change when a shoulder-only test did not restore progress. Unnamed joints hold their starting measured targets; gripper commands never change in this primitive. Both arms may be selected only with visibly separated swept paths.

First request preview_joint_delta with joint_delta arm maps and steps1..6. The map uses joint1 through joint6 and total requested increments in radians, not increments repeated each step. Inspect predicted elbow, wrist and fingertip paths against current head/wrist RGB and the other arm. Then issue the identical map and duration with the returned preview_id. Each native step bounds joint increments and conservative downstream point sweep; a requested delta may remain unfinished after the prefix. Inspect actual angles, tracking feedback and fresh RGB before choosing again. A preview does not consume native steps; intervening motion invalidates it.

For a common folded configuration, request preview_unfold_arm with one arm, the obstructed-motion reason, an RGB-supported clearance_box and max_steps1..24. The planner searches coordinated shoulder/elbow/wrist candidates within this empty volume and returns its chosen path. Review the whole preview, then execute unfold_arm with the identical specification and preview_id. Reject a visually obstructed plan even if proxy-envelope checks pass. If no candidate fits, create visible room or use a different small joint combination; do not enlarge the box beyond observed clearance.

Once the short adjustment ends, re-estimate the wrist target and test the previously obstructed Cartesian motion. Keep the attempted configuration and measured retest outcome in memory. Geometric extension, target_reached and open fingers do not establish restored grasping or empty space. If the retest still fails, change approach geometry or arm instead of repeating the same pair.

Evidence status: development candidate pending native execution qualification. Point sweep and envelope bounds do not model full link meshes or automatically avoid obstacles.
