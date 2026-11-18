# 关节目标合法性反馈与闲置手臂观察

2026-10-29，修改当前 astra_io_250hz_linear_scene_feedback 版本，输入协议升级为 observation_pair_joint_goal_privileged_v6。现有 run_local.py 启动流程默认启用，无需增加功能开关。

## 原有代码位置与缺口

- harness/egg_harness/kinematics.py 的 joints_array 原本检查六个有限关节角及声明范围。
- harness/egg_harness/joint_control.py 的 validate_joint_segment 检查连杆原点/指尖代理运动边界，不是完整碰撞检测，且当前关节目标执行链没有调用其旧增量动作流程。
- harness/scripts/check_arm_collision_proxy.py 是离线球体筛查脚本，原先没有接到 control_arms 执行入口。
- 原始 skills-upstream/plug-in-charger/SKILL.md 与 skills-upstream/pour-by-language/SKILL.md 要求用空闲手臂腕部相机辅助观察；当前 general-pickup skill 也有简短提示，但没有一个为所有当前任务加载的通用观察 skill。

## 执行前检查和模型反馈

harness/egg_harness/pose_guard.py 默认在 joint_goal.execute_control 的目标提交之前运行。它检查关节范围、完整请求目标的几何关系，以及本次参考运动段的抽样几何关系。使用从 X5 USD 导出的静态碰撞几何和关节变换，覆盖 base_link 与 link1..6 的自身/双臂关系，遵从直接相邻碰撞忽略对。没有写死任何案例的关节角阈值。

convexHull 部件使用真实声明的凸包；link3 的 convexDecomposition 不被当作整体实心凸包，而使用原始表面顶点进入另一碰撞凸包的正重叠证据。几何资源附 SHA-256 校验；缺失或损坏会使服务器启动失败。

拒绝时 status=rejected_before_motion，executed_steps=0，goal_submissions=0，两臂和夹爪命令均不执行。action_feedback.pose_validation 经客户端校验和两层输入压缩保留为 observation.last_motion.pose_validation，包含 accepted、stage、violations、被拒绝的目标和修正提示。关节范围错误提供手臂、关节序号、请求角和范围；几何重叠提供两侧手臂、连杆对及几何余量。几何余量不是运行时接触力或 PhysX 穿透深度。

已经存在的重叠允许保持或减小，以便退让；不接受新重叠或相对起始状态更深的重叠。执行后仍保留原有实测到达、停滞、超时和夹爪变化反馈。

## 闲置手臂辅助观察

新 skill：harness/skills/idle-arm-observation/SKILL.md。run_local.py 和 skill_policy.py 都会自动确保其加载一次，因此旧任务配置也能使用。observation.auxiliary_observation 显式给出 left/right 与 cam_left_wrist/cam_right_wrist 的映射、夹爪命令和实测开度。

模型通过现有 control_arms 工具选择并移动空闲观察臂，操作臂设为 null，观察臂 gripper 设为 null 保持命令。新鲜的三路 RGB 在动作返回后照常提供，无需新增相机工具。操作臂 null 表示保持实测起始关节目标，并不刚性锁住机械臂；技能要求检查其后续实测状态和持物情况。

模型必须从 RGB 和本轮证据确认观察臂为空闲且空手。open_hand_candidate 仅表示张开状态，empty_hand_confirmed 默认 false，不把开手当作已确认空手。新视角应揭示具体遮挡关系；保持支撑或持物的手臂不能直接拿来观察。

## 验证与证据

- 102 项本地测试全部通过，0 失败、0 错误；包括实际工具 schema、原生控制队列夹具、模型请求序列、非法目标零执行、夹爪保持和两层反馈压缩。
- 从原案例动作执行前 step 3779 的存档状态重算，原目标在提交前被判定为右臂 link3/link5 重叠。原动作在旧运行中于 step 3904、15.616 秒返回停滞。
- 原版完整性检查：952 个受保护文件全部匹配。
- 未启动新的 Isaac/PhysX 任务实验，未调用真实模型 API；尚未验证模型在新任务运行中实际选择的观察视角质量或任务成功率改善。

记录和修改前备份位于 runs/pose-validation-aux-camera-20261029/：tests.log、test-results.json、archived-case-preflight.json、changes.patch、changes.json、before/。

## 检测边界

这是局部的执行前几何筛查。夹爪、相机外壳、持物和环境碰撞没有覆盖；有限运动段抽样可能遗漏采样之间的碰撞，凹部件顶点筛查也可能遗漏没有包含表面顶点的相交。实际执行受动力学和接触约束影响。accepted=true 不能解释为目标必然可达或整条路径已证明无碰撞。
