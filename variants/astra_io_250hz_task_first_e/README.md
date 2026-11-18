# 版本 e：任务优先、临时观察与桌面换臂

2026-11-03，从版本 d `../astra_io_250hz_linear_scene_feedback` 独立复制。
版本 d 的 324 个文件原字节记录在 `parent-d-sha256.json`，由
`verify_originals.py` 联同既有父版本/发布文件一起校验。

## 决策规则

1. 任务操作优先。先检查已有三路图像、状态和场景信息；只有存在阻碍下一步执行、且当前证据无法回答的具体疑问，才移动辅助相机，并在 assessment 中说明疑问与预期证据。
2. 观察是临时分工。获得证据后重新分配双臂，不默认长期保留观察角色。
3. 失败后重新分工。连续两次抓取无进展后，比较换抓取几何、换任务允许的目标和换臂；在 memory 中记录失败与具体改变。目标身份或操作顺序受限时，保持任务约束。
4. 当前臂能完成拾取、抬升、运输和放置全程时，不交接；来源与终点分处左右两侧本身不构成交接理由。只有具体可达/接近限制或公开任务要求换手时，才使用桌面换臂。
5. 所有换臂按顺序进行：放到双臂可达的稳定桌面 → 释放 → 原手撤离 → 确认物体静止受支撑 → 另一只手重新定位并从桌面抓取 → 短试提确认。禁止空中直接两手交接。
6. 明确 RESET / RETURN TO ORIGIN：公开任务要求 reset 时，预留释放、撤离和双臂回位预算；使用 `observation.home_joints.left/right`，核对实测关节、`scene_feedback.home_joint_error_rad`、控制器容差和夹爪开度，再观察原生完成结果。辅助观察臂也要回位。要求保持举起的 `general_pickup` 不提前释放回位。

系统提示词与默认加载的 `idle-arm-observation`、`cross-side-handoff` 统一执行上述规则。
同步修改 `general-pickup`、`stack-bowls`、`push-t`、`solve-equation`、`swap-blocks`，
并移除 `make-kong`、`insert-key` 中的直接空中交接指令。

## 可达性判断的输入

新增 `harness/egg_harness/arm_workspace.py`，在推理和导出共用的输入构建边界，
由本次实测关节/腕部世界位姿、公开 X5 运动学和当前场景几何计算
`observation.arm_workspace`，不增加工具或推进物理：

- 各臂基座第一关节原点、当前腕部和接触参考点的世界坐标。
- 链长三角不等式给出的腕部/接触参考点可触及距离上界。
- 各物体 bbox 中心（缺失时用根位置）到每臂基座/腕部的距离与上界余量。
- 到物体世界 AABB 的距离下界，区分“参考中心超界”和“整个 bbox 明确超界”，避免把仍可抓的边缘排除。

这个外包范围只能排除过远的点。范围内的点是否满足姿态、关节限制、环境清障和整段路径要求，
仍由模型结合现有 FK、RGB 和执行反馈判断；不宣称通过 IK、碰撞或接触验证。
直接操作或换臂的理由要求记录在 memory。交接与分工规则属于模型策略指令，
执行器不会自动生成抓取或交接动作。

新输入协议为 `observation_pair_joint_goal_privileged_v7`，运行及导出标识为
`astra_io_250hz_task_first_e`。默认输出目录含 `task-first-e`。
沿用 d 的 scene_feedback、位姿检查、linear 插值、时钟、动作格式和模型预算。
默认示例配置的 API 重试状态列表补入 499，以匹配仓库当前共享重试实现；历史 d 重跑配置已经包含该项。

## 使用与验证

从本目录运行离线检查：

```bash
../../.venv-policy/bin/python -B -m unittest discover -s tests -q
../../.venv-policy/bin/python -B verify_originals.py
```

启动默认任务的入口：

```bash
../../.venv-policy/bin/python -B run_local.py --task general_pickup --seed 20 --env-gpu 0 --interpolation-profile linear
```

其他任务可用 `--task-config` 指向对应 d 任务配置；本版本入口会写入新协议/版本标识，
并确保加载通用分工与桌面交接规则。实际运行仍需要现有仿真资产、GPU 和 API 配置。
本次修改只做离线验证，未启动新的仿真任务或真实模型评测；成绩变化待实验确认。
