---
name: visual-contact-control
description: Choose observed joint-goal waypoints for approach, contact, carrying and withdrawal.
---

Use RGB and measured robot state to choose each joint goal. The wrist frame is link6; the approximate finger reference is 0.138 m along its local +X, supplied as contact_estimates. This is a geometric reference, not measured object contact. Use it with the FK direction references to reason about where joint changes move the fingers.

Approach a visible pre-contact waypoint with clearance around both arms and the whole gripper. Near contact, choose small intermediate joint goals so fresh images arrive before further descent or rotation. Separate alignment, closure, test lift, transfer, release and withdrawal whenever their outcomes need checking. In clear space, use a useful destination waypoint instead of many redundant tiny motions.

Close by setting the desired gripper opening and holding the joint goal. Inspect the resulting fingers and RGB before lifting. A gripper that settles short of its requested opening may be resisted; this alone establishes neither contact with the intended object nor retention. Compare object motion relative to the pads and background across views.

Read last_motion.status and measured errors after each return. If motion stalled, timed out or was interrupted, inspect actual posture and clearance rather than assuming the final goal executed. After repeated low-progress contact attempts, retreat or change the approach or arm. Independent empty-hand moves may be tested separately when a bilateral attempt obscures which arm is blocked; preserve object support during coupled grasps.

Return to the observed home only when required by the public task. Withdraw through clear space before resetting; choose intermediate goals when fingers, a wrist attachment or a carried object would otherwise sweep into nearby geometry. Plan the full public finish within remaining simulation time.
