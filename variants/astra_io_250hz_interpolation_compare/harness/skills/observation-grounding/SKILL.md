---
name: observation-grounding
description: Ground decisions in current public observations and same-episode memory.
---

Derive identities, positions, assignments and joint goals from the current instruction, paired RGB, public robot state and observations already made in this episode. General strategies and robot calibration do not provide the current scene's answers. Keep uncertain identifications and unseen outcomes explicit.

Each decision replaces memory. Preserve a compact ledger of required objects, completed placements, held objects, pending goals, relevant earlier observations and unresolved questions. Update facts with new evidence instead of overwriting the ledger with only the latest action. Occlusion does not erase an earlier identity, but later contact may invalidate an earlier placement.

Only the exposed joint-goal tool is available. Earlier views are not retrievable by a separate tool; preserve relevant evidence in memory and obtain a new permitted view when needed. Do not request object truth, layout/seed files, evaluator internals, support-arm trajectories, future frames or another episode's solution.

Pursue the complete public task. The simulator determines success; satisfying an easier partial subtask or merely reaching a joint goal is insufficient. Unchanged goals can obtain another settling observation. Once a scene is stationary, identical waits add no geometric evidence: inspect a specific unresolved relationship and correct only an observed discrepancy. If inspection cannot resolve it, retain the uncertainty.
