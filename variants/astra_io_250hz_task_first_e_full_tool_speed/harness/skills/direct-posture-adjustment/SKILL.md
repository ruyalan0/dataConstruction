---
name: direct-posture-adjustment
description: Invoke bounded shoulder recovery when configuration obstructs approach, descent or withdrawal; healthy compact standby alone does not require recovery.
---

Establish which next motion is obstructed and why reconfiguration can provide space. Initial folding on the arm's own side is acceptable when it can unfold along the approach. Use measured motion and visible clearance; horizontal link2 alone is insufficient.

When posture_action is available, action:{posture:{left:upright},steps:1..6} with phase:transit turns the shoulder directly. Select right instead, or both arms when swept paths are separated. This changes configuration without Cartesian IK for an unchanged wrist target.

First establish selected hands are empty, already open, and clear of objects, table and other arm. If carrying, support, release and verify withdrawal first. Review predicted paths against RGB; shoulder, elbow, wrist and fingers sweep through space. Open measurements alone prove neither emptiness nor clearance.

Use a short prefix and fresh views. Choose one step when uncertain. Bounds are 24 mm per native step for predicted downstream origins/wrist/estimated fingertip and 0.15 rad at the shoulder. Wrist pose is not held; obstacles are not automatically planned around. Tracking interruption requires a new clearance decision.

After adjusting, re-estimate the wrist target and test a short segment of the previously obstructed motion. Recovery is effective when that motion resumes with clearance; smaller tilt alone is not success. Stop adjusting once sufficient space exists, even before vertical. If still blocked, revise the approach instead of repeatedly straightening.

Keep a recovery ledger in memory: obstructed motion, adjustment tried, and measured test result. If the test still undershoots or contracts, do not restart the same shoulder-adjustment/approach pair as a new plan. Choose a materially different wrist orientation or approach direction, or assign an empty other arm after clearing its route. A homeward wrist target is useful only when it actually restores tracking; repeatedly naming home does not escape a constrained IK branch.

When posture_recovery_program is exposed, a single recover_posture call executes up to 40 bounded native steps with per-step tracking checks. Supply the selected arm, the obstructed-motion reason, a visually supported empty clearance_box in world metres, and max_steps. Include downstream joint origins, wrist and fingertip with 3 cm margin inside that box. The entire predicted prefix must fit before movement. Use a shorter prefix or retreat first when that clear volume cannot be established. This program turns the shoulder; it does not automatically move around obstacles or unfold other joints.

Inspect its stop status and fresh views, then test the obstructed motion. target_reached only reports the programmed shoulder target, not restored grasp ability.

The short action has development and IPC evidence. The recovery program is a development candidate; collision safety and task improvement remain unproven.
