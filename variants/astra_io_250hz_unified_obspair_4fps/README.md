本版本组合“全部250Hz＋4fps历史图像＋双观测”。运行入口和输出标识为 `astra_io_250hz_unified_obspair_4fps`，由统一250Hz版本和已有4fps采样方案组合而成。

物理、关节控制、相机、RGB观测、任务计数和奖励检查均为250Hz，每周期4ms，`render_interval=1`。general_pickup沿用8秒仿真预算，即2000个控制周期；模型按动作段决策。

每次请求提供最近5秒、4fps的三路历史RGB，以及上一决策点和当前决策点的图像与实测状态。记忆位于 `observation.memory`，不回放旧工具调用或结果。历史采样按仿真时间0、250、500、750ms等时刻，向后取最近可达控制周期，因此实际时刻为0、252、500、752ms等，误差最多2ms且不累积。

历史图像只有RGB，两端保留实测关节状态。上一决策点超出5秒窗口时仍保留；与历史采样同刻时合并角色。最多20组历史加2组端点，共66张图片；首轮只有当前3张。窗口随仿真时间滚动，模型等待不会推进窗口。250Hz逐周期图像继续记录，4fps只控制输入模型的历史图像采样。

在仓库根目录运行单布局：

```bash
.venv-policy/bin/python -B variants/astra_io_250hz_unified_obspair_4fps/run_local.py \
  --seed 20 --env-gpu 4 --allow-shared-gpu
```

运行L20/L21/L22并导出Swift数据，输出目录必须为新目录：

```bash
.venv-policy/bin/python -B variants/astra_io_250hz_unified_obspair_4fps/run_batch.py \
  --env-gpu 4 --layouts 20 21 22 \
  --output runs/general_pickup-io-250hz-unified-obspair-4fps-3layouts-NEW
```

离线检查：

```bash
.venv-policy/bin/python -B -m unittest discover \
  -s variants/astra_io_250hz_unified_obspair_4fps/tests -p 'test_*.py' -v
.venv-policy/bin/python -B variants/astra_io_250hz_unified_obspair_4fps/verify_originals.py
```

实跑后检查每个4ms周期、4fps采样记录、预算与250FPS视频：

```bash
.venv-policy/bin/python -B variants/astra_io_250hz_unified_obspair_4fps/validate_run.py \
  --run /absolute/path/to/run/general_pickup/L20
```

独立导出或复核数据：

```bash
.venv-policy/bin/python -B variants/astra_io_250hz_unified_obspair_4fps/export_swift.py \
  --run /absolute/path/to/run/general_pickup/L20 --output /absolute/path/to/new-dataset
.venv-policy/bin/python -B variants/astra_io_250hz_unified_obspair_4fps/export_swift.py \
  --validate-only --output /absolute/path/to/new-dataset
```

Swift导出核对实际API图像顺序、两个决策点、4fps采样记录、memory和有效动作标签，时序配置采用 `timed_joint_250hz_unified_v1`，输入协议采用 `visual_history_5s_4fps_v1`。旧频率或其他实验版本的数据会被拒绝。

测试使用模型替身及确定性物理环境，覆盖逐周期观测/奖励、8秒预算、长动作中的历史采样、超过5秒后的裁剪、66图请求、零运动拒绝、保护中断、memory传递及Swift导出与篡改检查。真实API与Isaac抓取实验尚未运行。
