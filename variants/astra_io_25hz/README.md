# Astra 新输入输出版本：25 Hz 复跑

本目录从已运行的 astra_io_250hz 活跃源码复制，为 25 Hz 对照单独保存。原发布版、250 Hz 版本及其历史记录均不修改。

- general_pickup，默认 group 0 / L20，仅开放 control_arms。
- 动作频率 25 Hz，每步 40 ms；物理频率仍为 250 Hz，每动作推进 10 个物理步。
- 原生任务预算仍为 200 步，对应 8 秒控制阶段物理时间；steps 为 1..20，settle 为 0..2。
- 保留上一新版本的四位小数输出、移除模型 phase、文字 memory 与逐项去重，以及所有执行保护条件。
- render_interval 保留上一新版本的 1，仅相机频率随动作频率恢复到 25 Hz。
- 选卡使用 ISAACSIM_GPU 或 --env-gpu；原 CPU 物理后端保留，CUDA_VISIBLE_DEVICES 不设置。

```bash
ISAACSIM_GPU=4 .venv-policy/bin/python -B variants/astra_io_25hz/run_local.py --seed 20 --env-gpu 4 --episode-timeout 0
```

结果写入新的 runs/general_pickup-io-25hz-<时间>/，保留每次实际请求、原始及规范化响应、工具反馈、完整输入阅读副本和视频。

默认要求所选卡空闲。若明确允许共享，添加 `--allow-shared-gpu`；启动前要求该卡至少有 12 GiB 空闲显存，并将显存与利用率写入 `launch-record.json`。该选项不改变物理步长或控制频率。

```bash
.venv-policy/bin/python -B -m unittest discover -s variants/astra_io_25hz/tests -p 'test_*.py' -v
.venv-policy/bin/python -B variants/astra_io_25hz/verify_originals.py
.venv-policy/bin/python -B variants/astra_io_25hz/audit_run.py runs/general_pickup-io-25hz-<时间>
```

audit/parent-source-sha256.json 保存复跑前的 250 Hz 源码基线；audit/frequency-comparison.patch 记录两个版本的差异。

## 本次复跑结果

2026-10-19，group 0 / L20：成功，100 分；54 个原生动作步，8 次模型调用，0 次无效动作。实际动作 25 Hz，CPU 物理；使用 ISAACSIM_GPU=4 / --env-gpu 4，并显式允许共享 GPU。

[完整运行报告](../../runs/general_pickup-io-25hz-20261019-181733/RUN_REPORT.md)、[输入输出索引](../../runs/general_pickup-io-25hz-20261019-181733/io-index.json)、[视频](../../runs/general_pickup-io-25hz-20261019-181733/rendered/rollout.mp4)。
