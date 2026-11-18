# 250 Hz：最近 5 秒 4fps 图像 + 两个决策点观测

这是 `astra_io_250hz_obspair` 的独立副本。每次请求提供最近 5 秒、4fps 的三路 RGB，再加上上一决策点和当前时刻的三路 RGB 与关节状态；memory 位于 `observation.memory`。可行性与参考数据核对见 [EVALUATION.md](EVALUATION.md)。原有版本、脚本、`val.jsonl`、`4fps_val.jsonl` 均保留。

## 输入

每次 API 请求仍只有一条 user 消息，包含：

| 字段 | 内容 |
|---|---|
| `input_protocol` | `visual_history_5s_4fps_v1` |
| `visual_history.observations` | 按仿真时间排序的观测列表；记录 step、relative_time_ms、roles 和对应图片名 |
| `history.previous_observation` | 上一决策点动作开始前的关节、腕部位姿、夹爪测量；首轮为 null |
| `history.duration_ms` | 上一决策点到当前的真实仿真间隔，按实际执行周期计算 |
| `observation` | 当前测得状态、memory、当前命令和公共关节语义等 |

每个图像时刻均包含 `cam_head`、`cam_left_wrist`、`cam_right_wrist`，依次命名为 `observation_NN_cam_head` 等。`roles` 区分 `history`、`previous_decision`、`current`；两个端点用 `state_source` 指向对应状态字段。中间历史帧只有 RGB，不补造关节状态，也不加入旧调用、执行反馈或推理。

采样以回合起点为零，目标时刻为 0、250、500、750ms……。250 Hz 的周期为 4ms，因此向后取第一个可达控制时刻，实际为 0、252、500、752ms……；同时记录目标和实际时刻，偏差为 0 或 2ms，不会累积。模型等待期间不推进仿真，也不重复填充图像。

历史窗口为当前时刻前 5 秒，启动不足 5 秒时只使用已有图像；上一决策点即使早于窗口也保留。历史采样与上一决策时间相同时合并角色，使用决策时的原图；当前图单独保留。零步拒绝时允许两个端点同为 0ms。最多 20 组历史 + 2 组端点，即 66 张图片。

## 运行

```bash
# 示例使用 GPU 3；可改为所需编号。
ISAACSIM_GPU=3 .venv-policy/bin/python -B variants/astra_io_250hz_obspair_4fps/run_local.py \
  --seed 20 --env-gpu 3
```

输出自动进入新目录 `runs/general_pickup-io-250hz-obspair-4fps-<时间>/`。如果需要共享在用的卡，可添加 `--allow-shared-gpu`。

运行 L20/L21/L22 并自动保存、合并 Swift 输入输出：

```bash
ISAACSIM_GPU=3 .venv-policy/bin/python -B variants/astra_io_250hz_obspair_4fps/run_batch.py \
  --env-gpu 3 --layouts 20 21 22 \
  --output /absolute/path/to/a/new-batch-directory
```

250 Hz 关节目标、25 Hz 任务时钟、8 秒 general_pickup 预算、原物理后端与控制保护沿用现有实现。采样在动作执行途中进行，不会为了凑历史而额外运动。`seed-N/visual-history.jsonl` 记录本局所有周期采样的实际时刻和图像路径；服务响应仅携带当前窗口。每个会话重新建立采样器，不复用其他回合图片。

## Swift 导出

```bash
.venv-policy/bin/python -B variants/astra_io_250hz_obspair_4fps/export_swift.py \
  --run /absolute/path/to/new-4fps-run \
  --output /absolute/path/to/new-swift-dataset
```

可重复 `--run` 合并不同 layout。每条 `train.jsonl` 包含 system/user/assistant 和 images，保留实际 API 输入的时间顺序、图像和 `observation.memory`，assistant 为实际生效的 control_arms 参数。导出时核对服务端采样记录、两个端点、实际 API 消息和有效动作响应；修改标签或历史时间戳都会被拒绝。数据不会混入旧双观测版本。

```bash
.venv-policy/bin/python -B variants/astra_io_250hz_obspair_4fps/export_swift.py \
  --validate-only --output /absolute/path/to/new-swift-dataset
```

## 验证

```bash
.venv-policy/bin/python -B -m unittest discover \
  -s variants/astra_io_250hz_obspair_4fps/tests -p 'test_*.py' -v
.venv-policy/bin/python -B variants/astra_io_250hz_obspair_4fps/verify_originals.py
```

已通过 34 项离线测试，包括实际 server/client 代码及原生控制队列的长动作采样、5 秒裁剪、66 图请求组装、保护与拒绝、memory、Swift 导出和篡改检测。API 使用替身，物理使用确定性测试环境；尚未运行本版本真实 API/Isaac 抓取回合，未生成新的实跑训练数据。之前双观测版的三局成功结果不代表本版本结果。

## 关键文件

| 文件 | 用途 |
|---|---|
| [visual_history.py](harness/egg_harness/visual_history.py) | 仿真采样网格、滚动窗口和完整性检查 |
| [observation_pair.py](harness/egg_harness/observation_pair.py) | 合并采样图与决策端点，构造时序输入 |
| [server_loop.py](harness/scripts/server_loop.py) | 保存实际采样图和公共窗口 |
| [action_control.py](harness/egg_harness/action_control.py) | 动作执行途中触发采样 |
| [native_responses.py](harness/egg_harness/native_responses.py) | 单 user 请求、显式 memory，不回放 self.previous |
| [policy.py](policy.py) | 对应输入说明及控制入口 |
| [export_swift.py](export_swift.py) | 完整时序输入输出的 Swift 导出 |
