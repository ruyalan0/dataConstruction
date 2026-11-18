# 250 Hz 两时刻观测输入版

基于 `astra_io_250hz_timed` 的独立副本。输入采用上一决策点 + 当前决策点观测，memory 位于当前 `observation`。原有代码和用户提供的 `val.jsonl` 均保留。方案评估见 [EVALUATION.md](EVALUATION.md)。

每次 API 请求只包含一条 user 消息，结构为：

```json
{
  "input_protocol": "observation_pair_v1",
  "instruction": "当前任务要求",
  "history": {
    "available": true,
    "duration_ms": 332.0,
    "previous_observation": {
      "step": 0,
      "state": {"left_arm_joint_state": [0, 0, 0, 0, 0, 0], "right_arm_joint_state": [0, 0, 0, 0, 0, 0]}
    }
  },
  "observation": {
    "step": 83,
    "state": {"left_arm_joint_state": [0, 0, 0, 0, 0, 0], "right_arm_joint_state": [0.1, 0, 0, 0, 0, 0]},
    "memory": "上一决策返回的文字记忆"
  }
}
```

上面是简化结构；实际 state 还包含两臂腕部位姿、夹爪命令回显和实测手指位置，当前 observation 带有原有公共关节语义、姿态和当前命令。完整输入另含控制协议、任务预算及公共任务要求。图像依次为 previous_cam_head、previous_cam_left_wrist、previous_cam_right_wrist、current_cam_head、current_cam_left_wrist、current_cam_right_wrist。首轮 history.available=false、previous_observation=null，只带后三图。

`history` 中只有一个观测快照，不携带旧工具调用、反馈或推理；`recent_executed_segments`、逐周期 tracking、history_references 也不进入输入。原始动作和保护事件继续记入本地日志。memory 仍由模型输出，每轮显式放进新的 observation；同一 memory 不再通过旧调用回放。

## 入口

```bash
ISAACSIM_GPU=3 .venv-policy/bin/python -B variants/astra_io_250hz_obspair/run_local.py --seed 20 --env-gpu 3
# 所有卡都在使用时，可显式添加 --allow-shared-gpu（要求至少 12 GiB 空余显存）
```

输出目录为新建的 `runs/general_pickup-io-250hz-obspair-<时间>/`。250 Hz 关节控制、25 Hz 任务计时、原 CPU 物理后端、插值、夹爪映射和保护保持原实现；控制输出仍是原 control_arms 工具。旧接口的 continuation_request 不支持直接接入。

## 检查与样例

```bash
.venv-policy/bin/python -B -m unittest discover -s variants/astra_io_250hz_obspair/tests -p 'test_*.py' -v
.venv-policy/bin/python -B variants/astra_io_250hz_obspair/verify_originals.py
```

可先阅读[第二轮完整输入预览](examples/historical-input-preview/L20/0002.input.json)及其[结构化 observation](examples/historical-input-preview/L20/0002.context.json)。39 次历史决策全部转换用于格式预览，未调用 API 或 Isaac；这些预览不是本版本新采集的训练数据。

## Swift 导出

真实回合结束后使用本目录的独立导出器，支持重复 `--run` 合并不同 layout：

```bash
.venv-policy/bin/python -B variants/astra_io_250hz_obspair/export_swift.py \
  --run /absolute/path/to/new-obspair-run \
  --output /absolute/path/to/new-swift-dataset
```

`train.jsonl` / `all.jsonl` 每行包含 system/user/assistant 和 images，user 原样保留一次请求的两个观测点和 observation.memory；没有上一轮调用/反馈块。assistant 标签保留实际执行响应的参数 JSON。验证命令为 `export_swift.py --validate-only --output <数据目录>`。旧版本导出器不应用到本版本日志。

## 主要新实现

| 文件 | 用途 |
|---|---|
| [observation_pair.py](harness/egg_harness/observation_pair.py) | 两时刻输入、真实间隔、三路图及 memory 组织 |
| [native_responses.py](harness/egg_harness/native_responses.py) | 单 user 请求、观测快照生命周期、不回放 self.previous |
| [policy.py](policy.py) | 新输入说明及原有重试/工具过滤 |
| [run_local.py](run_local.py) | 独立运行、选卡与运行来源标记 |
| [export_swift.py](export_swift.py) | 新日志的训练数据导出及校验 |
| [test_observation_pair.py](tests/test_observation_pair.py) | 请求、执行主循环与导出验证 |

2026-10-20 已完成本版本真实 API/Isaac 测试：general_pickup L20、L21、L22 各一场，均成功、100 分；分别为 22、7、9 次模型调用，6.40、3.00、4.16 秒仿真时间。无效动作均为 0，L20 另有 5 次夹爪保护中断。三场采用相同的 277 个 Harness 文件快照，原有代码与 val.jsonl 校验通过。

结果见 [三局汇总](../../runs/general_pickup-io-250hz-obspair-3layouts-20261020-111723/SUMMARY.md)。[合并 Swift train.jsonl](../../runs/general_pickup-io-250hz-obspair-3layouts-20261020-111723/swift-dataset/train.jsonl) 共 38 条真实输入输出，包含 observation.memory、上一决策点与当前状态及图片，无工具历史回放；[可读输入输出样例](../../runs/general_pickup-io-250hz-obspair-3layouts-20261020-111723/swift-dataset/example-followup.json)。全部数据与视频核验见 [final-audit.json](../../runs/general_pickup-io-250hz-obspair-3layouts-20261020-111723/final-audit.json)。

三局实际输入 token 共 473,813；之前完整历史版三局为 5,093,220。该比较涉及不同的模型决策和轨迹，不是固定动作下的消融实验。重复运行三布局并自动导出可使用本目录的 `run_batch.py --output <新的批次目录> --env-gpu N --layouts 20 21 22`。
