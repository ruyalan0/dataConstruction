# 统一 250 Hz 双观测输入版

独立复制自 `astra_io_250hz_obspair`。原来的 variants、RoboDojo 源码、YAML 和历史数据均不修改，使用 `verify_originals.py` 核验 1002 个原文件及上游封存来源。

物理、关节动作执行、相机更新、RGB 观测、任务计数、奖励/终止检查均为 250 Hz；每周期 4 ms、每任务步一个控制周期，render_interval=1。模型仍按动作段决策，并非每秒推理 250 次。上一决策点与当前决策点的观测对及 observation.memory 保持原协议。

原 RoboDojo 的 general_pickup 定义预算为 200 步。适配器在内存中按原有时长换算成 2000 步，保留 8 秒仿真预算；SOURCE_BUDGET_HZ=25 仅用于读取旧预算后的单位换算，不是运行中的时钟。其余任务也按同一规则换算原预算。动作 steps=1..2500，settle=0..2，均为 4 ms 周期；跟踪及夹爪保护保留。

独立时序协议为 timed_joint_250hz_unified_v1，拒绝旧协议或只更新部分频率的输入。训练数据导出同步记录所有新频率，视频从逐周期 RGB 按 250 FPS 编码，保持仿真时间。数值精度沿用父版：输出最多四位小数，输入原测量精度。

运行单布局：

```bash
.venv-policy/bin/python -B variants/astra_io_250hz_unified_obspair/run_local.py --seed 20 --env-gpu 4 --allow-shared-gpu
```

运行三个开发布局并导出新数据：

```bash
.venv-policy/bin/python -B variants/astra_io_250hz_unified_obspair/run_batch.py --env-gpu 4 --layouts 20 21 22 --output runs/general_pickup-io-250hz-unified-obspair-3layouts-<timestamp>
```

检查：

```bash
.venv-policy/bin/python -B -m unittest discover -s variants/astra_io_250hz_unified_obspair/tests -p 'test_*.py' -v
.venv-policy/bin/python -B variants/astra_io_250hz_unified_obspair/verify_originals.py
```

运行记录里的 control-timing.json 为完整时序配置；unified-runtime.json 记录实际观测周期及预算换算；runner-result.json 记录原生任务步、控制步和奖励更新次数。逐周期图像及动作日志可核对是否覆盖每一个 4 ms 周期。

## 2026-10-21 实跑结果

general_pickup 的 L20、L21、L22 各运行一场，均由模拟器判定成功、100 分，无效动作均为 0。
三场运行相同的 277 个 Harness 文件；26 项离线测试、原代码/历史日志完整性检查及逐周期证据核验均通过。

| 布局 | 得分 | 控制/任务/奖励周期 | 仿真秒 | 模型调用 |
|---|---:|---:|---:|---:|
| L20 | 100 | 1394 | 5.576 | 21 |
| L21 | 100 | 1318 | 5.272 | 14 |
| L22 | 100 | 779 | 3.116 | 8 |

合并数据共 43 条，输入、有效动作标签和图片逐项校验通过，所有 system 均不含旧 25 Hz 时序描述。
[测试汇总](../../runs/general_pickup-io-250hz-unified-obspair-3layouts-20261021-212005/SUMMARY.md) · [完整频率核验](../../runs/general_pickup-io-250hz-unified-obspair-3layouts-20261021-212005/frequency-verification.json) · [新 train.jsonl](../../runs/general_pickup-io-250hz-unified-obspair-3layouts-20261021-212005/swift-dataset/train.jsonl)
