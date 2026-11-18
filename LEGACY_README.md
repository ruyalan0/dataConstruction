# Dojo-Astra Harness

面向 RoboDojo 双臂 ARX X5 的 Astra 控制与证据闭环。当前为第一阶段开发版本，尚无榜单成绩。

```bash
uv sync --locked
uv run pytest -q
uv run ruff check src tests scripts
uv run python scripts/probe_api.py \
  --credentials-example /workspace/APIs/Azure-gpt-astra.py \
  --output artifacts/api-probe-new
```

API 探测会发真实请求；凭据从外部示例 AST 读取，不执行示例，不写入仓库。当前网关只验证了 `chat.completions + reasoning_effort=none` 的函数调用，默认推理模式函数调用失败，Responses 两条已测路径返回 404。

默认 `json-high` 用 JSON 动作协议请求 high，不发送原生 function tools。早期探测 reasoning_tokens=0；连续仿真闭环已报告非零 reasoning tokens，但尚未证明 high 相对 none 的任务收益。工具参数/缺帧/缺相机错误返回结构化结果，允许最多3次原地修正且不执行动作；损坏协议、NaN、观测或物理执行错误仍严格失败。

`dojo_astra.deploy.Model` 提供 XPolicyLab 的 `reset/update_obs/get_action/finish` 接口，`eval_one_episode(TASK_ENV, model_client)` 每次发送一个原生动作并取得动作后观测，原生终止时闭合未完成动作段，不再请求模型。每个环境使用独立 Model。正式 adapter 和开发进程共用 `HarnessConfig`，未知键拒绝；保存模型deployment、有效配置、SYSTEM/tools/工作记忆schema和源码哈希。默认evidence/json-high、6图、64 KiB活跃文本；凭据从外部文件读取。当前尚未安装到外部 XPolicyLab policy 目录。

`scripts/smoke_sim.py` 在远端已有 uv IsaacSim 环境中验证官方观测、IK 和 take_action；只替换重置阶段的传输客户端与资产根路径，不用于正式评测。其 output 必须是新目录，保留每次失败产物，不静默重试。

已通过 `artifacts/sim-smoke-v7` 的单布局控制校准：左手 +z 5 mm 实测 4.998 mm，另一臂漂移 0.028 mm。worker 0000000 的可复现入口为 `bash scripts/run_worker_smoke.sh 新目录名`，在 worker 中运行，并先用 adcli 确認 Running。启动脚本统一 CUDA compat 驱动、检查实际资源和 Warp/Torch 互操作，结束后独立检查结果文件。

项目私有资产需要 `prepare_assets.py` 生成 X5 配置视图，并用 `restore_task_assets.py` 恢复 `Rigid/cube`、`Rigid/triangular_prism`、`Geometry/camera_stand`，其 manifest 记录固定版本与逐文件哈希。当前路径绑定此 NAS/worker，尚未打包为通用安装器。

执行器最大单段总路径30 mm / 15° / 8个原生动作。旧 `act.steps` 仍重复绝对终点；新 `trajectory` 支持1–4个三轴位置/姿态联动路点，位置线性插值、旋转SLERP，并分开 `motion_steps` 与 `settle_steps`。`hold_current` 保持最新位姿，`hold_latched` 使用持久参考，`latch` 显式重设参考。连续3个低进展边界可以中断剩余队列并交回Astra，不推断碰撞原因。支持world/link6 tool坐标，TCP/pivot尚未标定启用；位姿到达不会提前跳过夹爪等待，也不能证明抓放成功。

本轮改动与验证边界见 [REVIEW-1实施记录](docs/review-1-implementation.md)，早期计划见 [docs/my-plan.md](docs/my-plan.md)，连续进度见 [docs/progress.md](docs/progress.md)。

连续实验在worker运行 `HARNESS_PROFILE=evidence bash scripts/run_worker_episode.sh 新目录名 0 0 任务名`；两个0表示不设harness环境步/API上限，回合只由RoboDojo原生逻辑终止，无回合墙钟上限，单次请求900秒；连接错误、408/409/429/5xx最多5次退避重试。默认最多6张RGB，保护当前三视图和显式查询结果，优先保留上一动作起始帧，历史图按内容去重。`recall` 回查原图，`inspect` 支持批量多相机/ROI/前后对比，`history` 分页查询文本与动作，`remember` 更新有界模型假设并撤销旧结论。模型不接收内部任务时限、奖励、物体真值或成功标志。旧200步存档仍按开发截断解读。

图像与消息按内容摘要存储；v2会话快照和请求manifest使用引用，`TraceStore.load_messages` 可核验摘要并重建实际请求。原始证据不随活跃上下文压缩删除。`scripts/summarize_episode.py` 统计请求/重试/工具修复、动作实测进展、hold/recall连续次数，以及每次请求的活跃文本字节和不同图像数；缺失的旧版指标明确标为不可用。

恢复开发轨迹：`run_worker_episode.sh 新目录 0 0 任务名 旧产物绝对路径`。逐步重放并验证末端2mm/2°，恢复消息、模型工作状态和显式锁定位姿；新版配置/提示/schema/源码摘要不同则拒绝恢复，须开新回合。旧版档案仍可读取，但缺少这些摘要时不能证明版本一致。重放不保证物体状态完全相同，恢复回合不计正式成绩。


通用 harness 对照与边界见 [实施计划](docs/harness-execution-plan.md)。配置依次为 baseline、vision、steps、feedback、evidence；各组沿用同一官方任务和非特权观测，任务名不进入策略分支。evidence 仅整理模型原笔记和实测末端反馈，不根据奖励或对象真值补充结论。

公开任务说明补全：新回合按原始instruction精确匹配六类已核查任务（含solve_equation），追加版本化public_task_requirements，原文instruction保留；不提供分数、内部阈值或动态成功状态。未匹配指令明确标记not_reviewed，六类说明不代表全部任务覆盖。来源与审计见 [官方指令对齐](docs/public-instruction-audit.md)，数据在src/dojo_astra/public_instructions.py。旧轨迹恢复保留旧提示合同；合同版本冲突直接失败，新提示需新开回合。
