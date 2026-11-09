---
name: idle-arm-observation
description: Reposition an empty idle wrist camera using the joint-goal interface while holding the working arm.
---

When the head or working wrist view hides grasp contact, insertion alignment, wrist/forearm clearance or an object's support, use the other arm's wrist camera to resolve that specific uncertainty. The three current RGB images are already supplied on every decision: cam_head, cam_left_wrist and cam_right_wrist. observation.auxiliary_observation maps each wrist camera to its arm. First inspect the existing view; reposition only if it will reveal missing evidence.

Select an arm that is idle, visibly empty and unnecessary for object support or a coupled grasp. open_hand_candidate describes its opening only, and empty_hand_confirmed is false until you establish emptiness from RGB and episode evidence. A held or supporting hand cannot become an observer until safe support, release and separation are confirmed. If neither arm is available, preserve the grasp and report the unresolved view.

Use control_arms to move only the observer through short joint waypoints toward an elevated oblique side view. Set the working arm to null and the observer gripper to null, preserving both gripper commands. For a left observer, send left={control:"joint", joints:[six observed-state-derived angles], gripper:null}, right:null; mirror these fields for a right observer. The null working arm holds its measured starting joints; this is a position target, not a rigid freeze. Recheck its measured posture and object retention after the move.

Choose the joint changes from current joint_semantics, measured state and visible swept clearance. Keep the whole observer arm, camera and open fingers outside both arms' motion corridors, the carried object and the target contact area. Seek a view that shows the relevant fingers or wrist together with a fixed target/support feature. Check last_motion.pose_validation after every command. A rejected observer waypoint executes neither arm; change the reported geometry or route before retrying.

Inspect the observer's fresh wrist RGB together with the head and working-wrist views. A useful new view requires visible relevant geometry, not simply a camera move. Once established, hold the observer while the working arm makes small observed corrections. If the view becomes occluded, pause further approach and reposition only the observer. Withdraw it through clear space before transport or reset when it would obstruct the next motion. Identical stationary waits do not reveal an occluded surface.
