---
name: observation-grounding
description: Ground robot task decisions in the current public instruction, permitted observations and same-episode memory.
---

Derive object identities, locations, poses, assignments and action targets from the current task instruction, permitted RGB views, public robot state and observations already made in this episode. Keep uncertain identifications and unseen outcomes explicit. General manipulation strategies and robot/camera calibration do not supply the current scene's answers.

Every decision replaces the episode-memory text, including zero-motion observation calls. Carry forward a compact ledger of observed identities and frame references, last confirmed placements, the held object, pending goals and unresolved questions. Update it with new evidence instead of replacing it with only the current tool query. Separate identity from current pose and support: occlusion can make present placement unknown without erasing an earlier observed identity. If later contact may have disturbed a placement, retain its last observation as history and recheck its current state before counting it complete.

Use only the exposed action and observation tools. Historical review is limited to recorded frames from this episode. Never request or use simulator object state, layout or seed files, evaluator implementation or thresholds, support-arm trajectories, future frames, other episodes' solutions or recorded joint action sequences. An internal object identifier is not a visual identity.

Pursue the complete task instruction. Use the supplied native completion signal to report the outcome, not as an oracle for searching hidden geometric tolerances or substituting an easier scored subtask. A task skill is a general strategy: resolve concrete object choices and every movement from the present scene. When observations cannot establish a fact, obtain a permitted view or report uncertainty rather than importing an answer from another layout.

Use completion checks to allow observed motion to settle and read the native outcome. Once the scene is stationary, repeated identical holds add no geometric evidence. If completion remains absent, revisit the public task checklist and seek a permitted view of a specific unresolved relationship, such as support, alignment or release. Correct only discrepancies supported by observations. If further inspection cannot resolve the uncertainty, retain and report it rather than assuming more waiting will complete the task.
