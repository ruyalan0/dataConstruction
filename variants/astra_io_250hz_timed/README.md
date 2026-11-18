# 250 Hz 定时关节控制

参考 nvidia11 `/data/chenxinlong/RoboDojo` 的 QwenSwift 适配，分开执行时钟和任务时钟。独立入口为 `variants/astra_io_250hz_timed/run_local.py`。

| 内容 | 本版本 |
|---|---|
| 关节目标执行 | 250 Hz，每个控制周期 4 ms |
| 原生观测/任务时钟 | 25 Hz，每个任务步 40 ms |
| general_pickup 回合预算 | 原生 200 步 = 2,000 控制周期 = 8 秒 |
| `steps` | 1..2500 个控制周期，最长 10 秒；超出回合剩余时间只执行原轨迹前缀 |
| 轨迹 | 从动作开始时测得的关节状态线性插值到目标 |
| 夹爪 | 第一控制周期即下发目标，沿用原生开度映射、mimic 和底层限速 |
| 奖励推进 | 每 10 个控制周期一次，保持原任务时钟；提前成功仍可结束回合 |
| 观测证据 | 每个控制周期记录目标和测得跟踪误差；每个任务步及动作返回时保存 RGB |

模型沿用 Astra 的 `control_arms` 工具格式、memory、上一轮交互回放、相等内容去重、最多四位小数和无 phase 输出。`settle` 保留 0..2，单位也是 4 ms；需要等待时可输出相同关节目标和更长的 `steps`。保留原有测得状态关节增量上限及腕部/夹爪跟踪中断检查，因此与远端无此检查的执行器并非完全相同。

`step`、动作日志和工具反馈的 `executed_steps` 计控制周期；`budget.total_native_steps` 等字段计原生任务步，并提供 `native_step_unit`、`total_control_ticks`、`elapsed_simulation_seconds` 和 `task_horizon_seconds`。`control-timing.json` 的 `protocol=timed_joint_v1` 用于阻止误连另一种计时方式的服务。模型请求墙钟等待时间不计入仿真时间。

## 远端核对

读取的远端提交为 `726e9aa`，工作区包含未提交的适配修改，不能只用提交号复现。文件原文已保存到 [参考归档](../../runs/nvidia11-250hz-reference-20261019-192705/)，逐文件 SHA-256 见 [remote-reference.json](audit/remote-reference.json)。

用户指定的 `qwen_actions.jsonl` 共 266 行：133 个计划和 133 个执行反馈，`steps` 范围 15..120。计划时长合计 44.444 秒，实际合计 44 秒，最后一段计划 0.48 秒只执行 0.036 秒，符合原生 44 秒任务时间截断。该局 `_result.json` 为未成功、过程分 30；用于核对协议和计时。

原本本地 `astra_io_250hz` 的失败记录保留：它将 `collect_freq` 改为 250，同时仍按 200 次动作结束，使 general_pickup 仅有 0.8 秒，单次动作最多 20 周期即 80 ms。本版本修正这两个时间约束。它们是已确认的协议差异；尚未通过新仿真证明抓取失败的全部原因。

## 运行

在项目根目录，选择空闲 GPU 后执行：

```bash
ISAACSIM_GPU=4 .venv-policy/bin/python -B variants/astra_io_250hz_timed/run_local.py --seed 20
```

也可通过 `--env-gpu N` 指定原始设备编号。如果所有卡都有任务，可显式添加 `--allow-shared-gpu`；此时要求所选卡至少有 12 GiB 空余显存，并在 `launch-record.json` 中记录共享选项与启动前显存、利用率。默认仍要求空闲卡。

结果默认写入 `runs/general_pickup-io-250hz-timed-<时间>/`。设备选择、CPU 物理后端和资产配置沿用原入口。独立环境适配器仅支持单环境 joint 控制；不接受旧回合重放。底层 RoboDojo 源码和 YAML 无需修改。

```bash
.venv-policy/bin/python -B -m unittest discover -s variants/astra_io_250hz_timed/tests -p 'test_*.py' -v
.venv-policy/bin/python -B variants/astra_io_250hz_timed/verify_originals.py
```

22 项离线测试通过：真实原生动作校验、控制队列、默认插值和文件通信服务代码参与验证；物理演化使用替身。覆盖 200 步对应 8 秒、25 Hz 奖励推进、125 周期线性插值和夹爪首周期目标、回合边界截断不加速、跟踪中断、非任务步边界的预算回传，以及全部 133 个远端动作的格式和时间。四位小数、memory、历史去重、选卡和工具回放检查同时通过。前版 333 个文件、原封存源码 546 个文件和历史日志 4,711 个文件哈希校验通过。

截至 2026-10-19，尚未运行本版本的真实 Isaac 回合：检查时本机 8 张 GPU 均处于高利用率，入口要求空闲 GPU。因此此处不提供新任务成绩。

2026-10-20，在 GPU 7 以共享模式完成 general_pickup L20：成功，100 分，24 次模型调用，1,680 个控制周期（168 任务步，6.72 秒仿真时间），0 次无效动作。修正了首次 `env.reset()` 前检查尚未创建的仿真对象的问题。详情见 [实跑报告](../../runs/general_pickup-io-250hz-timed-20261020-084923/RUN_REPORT.md)。

本局已按之前的 `messages + images` JSONL 格式导出 24 条 Swift 训练样本，包含原始输入、上一轮完整交互、memory 和实际执行参数标签；72 张原图、141 次图片引用，`steps` 与 `settle` 保持 250 Hz 控制周期。数据和校验见 [Swift 数据说明](../../runs/general_pickup-io-250hz-timed-20261020-084923/swift-dataset/README.md)。可使用 `scripts/export_timed_io_ms_swift.py --run <运行目录> --output <新的数据目录>` 导出其他本版本完整回合；`--validate-only --output <数据目录>` 可重新核验。

同日补跑 L21、L22，各一场：均成功、100 分，分别为 8/7 次模型决策、870/740 控制周期、3.48/2.96 秒仿真时间，0 次无效动作。两场在 GPU 3 串行执行，与 L20 的 276 个 Harness 文件快照完全一致，视频和 Swift 数据校验通过。[三回合汇总](../../runs/general_pickup-io-250hz-timed-3layouts-20261020-090459/SUMMARY.md)包含原始结果和视频链接。

三个 layout 的 Swift 合并集共 39 条（L20=24、L21=8、L22=7），117 张原图、225 次图片引用，历史在回合边界重置。入口为 [train.jsonl](../../runs/general_pickup-io-250hz-timed-3layouts-20261020-090459/swift-dataset/train.jsonl)，[数据说明与校验](../../runs/general_pickup-io-250hz-timed-3layouts-20261020-090459/swift-dataset/README.md)。导出器支持重复传入 `--run` 合并不同 layout；串行运行和自动导出入口为 `scripts/run_timed_pickup_layouts.py`。

## 核心实现

- [control_timing.py](harness/egg_harness/control_timing.py)：两个时钟的配置及协议校验。
- [timed_joint.py](harness/egg_harness/timed_joint.py)：原生控制队列、4 ms 物理步与 40 ms 任务计数桥接。
- [action_control.py](harness/egg_harness/action_control.py)：定时目标插值、跟踪反馈和按观测周期采图。
- [test_timed_joint.py](tests/test_timed_joint.py)：原生代码与远端样例验证。
