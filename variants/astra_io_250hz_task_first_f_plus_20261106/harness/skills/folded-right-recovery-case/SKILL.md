---
name: folded-right-recovery-case
description: Development case test of empty right-arm escape from an obstructed folded configuration, followed by a real approach test.
---

This run tests recovery of the right arm visible in the current RGB. Inspect the right shoulder, elbow, wrist, gripper and table clearance using supplied public state and views. The healthy initial folding rule still applies, but this recorded case has a right-arm configuration obstructing further approach. Prioritize recovering that arm rather than substituting the left arm to finish sorting. Preserve the left arm and anything it holds; change it only if its observed position blocks a safe right-arm path.

With the right hand visibly empty and open, inspect signed multi-joint previews and use unfold_arm when a visible clear envelope supports it, or joint_delta for coordinated shoulder/elbow/wrist adjustment. Adjust the whole configuration in short observed prefixes. Do not force a clearance box that intersects the table, rack, other arm or objects. Existing Cartesian withdrawal remains available if it creates room for joint adjustment. Judge progress from measured tracking and the next motion, not link2 tilt alone.

After adjustment, choose a visible reachable object in front of the red basket and test right-arm approach, short descent, opposing-pad grasp and retention lift. Re-localize it from current RGB rather than assuming a stored target. Report the exact public evidence of restored motion or remaining contraction in memory. If recovery fails, preserve the failed attempts and choose a materially different coordinated configuration instead of repeating the same posture/descent pair. Continue the original task only after this right-arm recovery test has been assessed.
