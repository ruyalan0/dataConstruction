# RoboDojo f agent

用于多人协作运行 RoboDojo ARX X5 实验的 f 版本 agent。默认使用 `gpt-6-astra / high`，支持通过参数切换模型。f 的内部版本名沿用 `astra_io_250hz_task_first_e_merged_20261105`，便于关联历史实验。

本仓库包含完整 Harness、任务 Skill、七项任务配置、固定版本 RoboDojo/XPolicyLab 的 Git bundle、离线测试和独立启动入口。运行仿真还需要 NVIDIA GPU、IsaacSim 运行环境、RoboDojo 资产及模型接口凭据，准备方法见 [环境配置](docs/SETUP.zh.md)。

## 安装与离线检查

需要 Linux、Git 和 Python 3.11。从仓库根目录执行：

```bash
git clone https://github.com/ruyalan0/dataConstruction.git
cd dataConstruction
python3.11 -m venv .venv-policy
source .venv-policy/bin/activate
python -m pip install -r requirements-policy.txt

python -B scripts/check_f.py
python -B scripts/restore_f_sources.py
python -B -m unittest discover \
  -s variants/astra_io_250hz_task_first_e_merged_20261105/tests -q
python -B scripts/run_f.py --help
```

源码恢复从仓库内的 bundle 读取，固定 RoboDojo `550059d7`、XPolicyLab `7dd88aea`，不需要访问其远程仓库。依赖目录已存在时会拒绝覆盖。离线检查与测试不需要 GPU、模型 key 或仿真资产。GitHub Actions 会在 push 和 PR 时执行这些检查。

## 配置模型接口

```bash
mkdir -p local
cp examples/api.toml local/api.toml
```

将 `local/api.toml` 的 `base_url` 改为实际支持 Responses API 和原生 function tools 的 HTTPS 网关。凭据可由 `OPENAI_API_KEY` 环境变量提供，也可写入本地 `local/auth.json`：

```json
{"OPENAI_API_KEY": "sk-YOUR-KEY"}
```

凭据文件设为 `chmod 600 local/auth.json`。`local/`、实验输出和依赖工作目录均已被 Git 忽略。

## 运行一场实验

激活策略环境后，指定仿真环境的 Python 和资产目录：

```bash
python -B scripts/run_f.py \
  --task general_pickup --seed 20 --env-gpu 0 \
  --sim-python /absolute/path/to/simulator/bin/python \
  --assets /absolute/path/to/Assets \
  --output runs/alice/general_pickup-L20
```

`--task` 自动选择对应配置。`--seed` 为 group 0 的布局编号，默认使用 L20–L22；其他布局需要附上 `--development-case-reason`。每场使用新的输出目录，已有目录会拒绝覆盖。

| `--task` | 任务 | 当前 f 的关节参考速度上限 |
| --- | --- | --- |
| `general_pickup` | 拾取指定物体 | 3.75 rad/s |
| `play_tic_tac_toe` | 井字棋 | 3.75 rad/s |
| `put_bottles_into_dustbin` | 投瓶 | 3.75 rad/s |
| `stack_bowls` | 叠碗 | 3.75 rad/s |
| `stack_blocks` | 叠方块 | 3.75 rad/s |
| `push_T` | 推 T | **0.6 rad/s** |
| `classify_objects` | 物体分类 | 3.75 rad/s |

例如，用同一套 f 策略比较模型：

```bash
python -B scripts/run_f.py \
  --task stack_bowls --seed 21 --env-gpu 1 \
  --model gpt-6.1-sol --reasoning-effort high \
  --sim-python /absolute/path/to/simulator/bin/python \
  --assets /absolute/path/to/Assets \
  --output runs/bob/sol-stack_bowls-L21
```

保留的实验设置：linear 插值、250 Hz 物理、25 Hz 任务时钟、5 秒 / 4 fps 视觉历史、v7 输入协议、每场最多 400 次有效模型调用。默认 `--episode-timeout 0`，沿用 f 批次使用的无额外整场墙钟超时设置。传输错误按原请求重试，原生任务终止条件保持原实现。速度上限约束关节参考轨迹，实测速度以执行反馈为准。

## 多人分工

按任务、模型、布局分配实验，每人使用独立的 `runs/<姓名>/...` 输出目录。同一台机器上的所有检出目录指定相同的 `--gpu-lock-dir /shared/path/gpu-locks`（也可设置 `ASTRA_GPU_LOCK_DIR`）。默认在空闲 GPU 上启动，并通过文件锁避免协作者重复使用同一卡。

需要复现每卡两场时，双方显式使用 `--allow-shared-gpu`，分别指定 `--gpu-slot 0` 和 `--gpu-slot 1`；每场启动前要求该卡至少剩余 12 GiB 显存。GPU 编号使用本机物理编号。

记录并共享 Git commit、任务、模型、reasoning effort、布局、GPU、资产版本、输出目录及原生分数。修改策略时新建分支，通过 PR 合并；实验数据、视频和凭据保存在各自约定的外部存储。代码修改后执行测试，审阅差异，再运行 `python scripts/check_f.py --write-manifest` 更新便携版清单。

## 输出与数据导出

每场输出包含 `launch-record.json`、`results.json`、源码及布局哈希、`seed-<布局>/` 仿真记录、`policy-<布局>/model/` 实际模型请求与响应，以及 `rendered/rollout.mp4` 视频。视频生成需要 `ffmpeg`、`ffprobe`。启动器返回 0 表示原生有效成功，任务未成功或基础设施异常返回非零；具体原因以 `results.json` 为准。

导出同一任务的已完成轨迹到 ms-swift JSONL：

```bash
python -B variants/astra_io_250hz_task_first_e_merged_20261105/export_swift.py \
  --run runs/alice/general_pickup-L20 \
  --output datasets/general_pickup-L20
```

导出器保留实际输入图像与模型有效参数，并校验原生终止和轨迹完整性。图像路径为绝对路径；迁移数据后需同步调整。处理当前限速的 `push_T` 轨迹时，给导出命令加上 `ASTRA_CONTROLLER_TASK=push_T` 环境变量。

## 版本与来源

- 当前 f 的策略、Skill、Harness 控制代码和模型输入协议保持原字节；便携适配集中于启动器路径和参数，以及一项测试的本地 fixture。
- `general_pickup`、`stack_blocks` 和 `classify_objects` 配置取自已记录的七任务 f 批次，模型恢复为 Astra 基线，运行路径与接口位置替换为公共模板。七项任务的系统提示词哈希均与该批次相同，测试会检查这一点。
- `provenance/` 保存原 f 的 336 个文件哈希、便携版清单和补充配置来源。校验还覆盖 1,931 个冻结父版本文件。其他 `variants/` 目录及 `release/` 为这些来源校验提供完整内容。
- 这是 f。投瓶指尖中点策略属于另一个 f+ 版本。历史 f 的早期 push T 实验曾使用 3.75 rad/s，不能直接记作当前 0.6 rad/s 设置的成绩。
- 原始合并过程见 [合并报告](docs/e-merged-speed-skills-20261105/REPORT.zh.md) 和 [推 T / 投瓶报告](docs/push-t-bottles-skills-20261105/REPORT.zh.md)，第三方来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。

此次发布验证覆盖 CPU 离线测试、来源完整性和启动入口；没有通过新目录重新运行 GPU/付费模型评测。
