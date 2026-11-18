# 部署与依赖恢复

本入口在任意检出路径使用仓库内冻结源码、Skill 和配置，不读取旧 `PhysEvo-*` 或 `Astra-Robodojo-*` 工作目录。相同输入不保证模型随机性或仿真数值结果完全一致。

## 1. 控制端准备

要求 Git、uv、Python 3.11。先运行 README 中的离线检查。

```bash
uv run --offline --no-project --python /usr/bin/python3 python scripts/physevo_release.py resources --output local/controller-resources.json
uv run --offline --no-project --python /usr/bin/python3 python scripts/physevo_release.py sources --destination dependencies/RoboDojo
```

`sources` 只读取随包的两个 Git bundle，在新目录恢复精确提交；已有目录会拒绝覆盖。bundle 包含固定版本运行代码、历史和原始许可/作者署名。此次仅初始化 XPolicyLab 子模块；IsaacLab/curobo 由固定运行时提供。

将检出目录和 `local/controller-resources.json` 放在 worker 可访问的共享 NAS。用平台工具确认 L20 worker 为 Running；原环境可用 `mlx worker list`。恢复脚本再次检查 `ARNOLD_WORKER_ID`、实际 cgroup 配额和 GPU，拒绝在控制 Pod 执行。

## 2. Worker 恢复运行时和资产

先将 `release/private-config.example.json` 复制到 Git 忽略的 `local/release-private.json`，将每个资源 SHA-256 对应的 `locations` 值填为有权读取的真实 HDFS URI，并填写 `provider_base_urls` 中的模型接口。示例 `.invalid` 地址会立即报错；缺少映射不会尝试隐式位置。原使用者的本地真实配置已经单独保留，未纳入 Git。

```bash
mkdir -p local
cp release/private-config.example.json local/release-private.json
chmod 600 local/release-private.json
```

编辑后再运行以下恢复命令。迁移到其他环境时需由资源所有者安全提供真实配置；本仓库不包含 API key，也无法自行获得私有数据访问权限。

需要可用 `hdfs` 命令及所有者提供的资源读取权限。公开清单只有 SHA-256、大小和文件清单；真实 URI 由本地配置按 SHA-256 映射。控制 Pod 快照应在恢复前重新生成；worker CPU/内存配额需大于控制端，并有至少 32 GiB cgroup 可用内存、100 GiB scratch 空间。

根据部署环境的网络配置设置 `DOWNLOAD_PROXY`。以下命令比较直连和指定代理的实际 HDFS 下载耗时，选择成功且较快的路线。没有代理配置时显式使用空字符串。不会改变模型提供商。

```bash
uv run --offline --no-project --python /usr/bin/python3 python scripts/physevo_release.py restore-runtime \
  --private-config local/release-private.json \
  --local-resources local/controller-resources.json --scratch /tmp \
  --output local/runtime-restore --proxy "$DOWNLOAD_PROXY"

uv run --offline --no-project --python /usr/bin/python3 python scripts/physevo_release.py restore-assets \
  --private-config local/release-private.json \
  --local-resources local/controller-resources.json --scratch /tmp \
  --destination /tmp/physevo-assets --workers 4 --proxy "$DOWNLOAD_PROXY"
```

运行时保留安装前缀 `/tmp/robodojo-uv`，不可任意搬动这个预构建环境。支持已记录的 2/4/8 卡 L20 worker；恢复时校验归档 SHA、文件数量、驱动、CUDA、IsaacSim 5.1.0.0 和 GPU 数量。使用没有这些运行时目录的新 worker；中途失败会保留诊断并停止，检查后在新 worker 重试。

资产分为 20 个独立分片，完整 SHA 校验后才写完成回执。同一 worker 上重复 `restore-assets` 会重验已完成分片并接续缺口；不接受损坏文件。worker 消失后从原 HDFS 分片恢复。

## 3. Worker 安装策略 Python 环境

仿真使用 `/tmp/robodojo-uv/bin/python`，模型调用使用独立 uv 环境：

```bash
uv venv --python /usr/bin/python3 /tmp/physevo-policy
uv pip install --python /tmp/physevo-policy/bin/python \
  --index-url https://pypi.org/simple -r release/requirements-policy.txt
```

安装前按网络配置比较直连/代理，使用较快路线。列表固定了恢复实验实际安装的全部 19 个包版本；不包含大体积仿真环境。离线检查不需要安装这些包。

API key 放在仓库外文件中，格式为单独一行 `sk-...`，限制读权限。下文 `/secure/zzy-key.txt` 是需替换的路径；工具不复制、打印或提交文件内容。

## 4. 准备一场 L20

```bash
uv run --offline --no-project --python /usr/bin/python3 python scripts/physevo_release.py prepare \
  --task pour_liquid_into_cup_random --layout 20 \
  --private-config local/release-private.json \
  --robodojo dependencies/RoboDojo \
  --assets /tmp/physevo-assets/Assets --sim-python /tmp/robodojo-uv/bin/python \
  --credentials /secure/zzy-key.txt --output runs/pour-random-l20
```

这一步只校验源码、写固定配置和哈希，不启动 GPU/模型。任务范围见 `prepare --help`；默认使用 random12 当时的冻结 Skill，最多 400 次模型请求、10800 秒策略运行预算，保留原生任务步数预算。

## 5. Worker 执行

结果目录与锁目录应位于可写共享 NAS；通过 `RESULT_HDFS_ROOT` 指定真实 HDFS 输出根目录，每场追加独立 run ID。

```bash
/tmp/physevo-policy/bin/python scripts/physevo_release.py run \
  --campaign runs/pour-random-l20 --gpu 0 --scratch /tmp \
  --local-resources local/controller-resources.json \
  --lock-dir /shared/physevo-provider-locks \
  --hdfs-root "$RESULT_HDFS_ROOT"
```

替换示例锁路径，所有并行调用使用同一共享目录；有历史调度器运行时，使用其现有锁目录以共享模型请求限额。每个新输出目录只运行一场，拒绝覆盖旧场次。不同 GPU 可并行，回合和模型请求均最多 5 路，每张 GPU 同时一场。

运行前检查 worker 配额、源码/配置/资产哈希、凭据存在性及 GPU 显存。保留执行和备份逻辑，将模型接口、凭据、请求锁和资源位置改为显式配置；文件哈希见 `manifest.json`。模拟器、HDFS JVM、模型调用全部放 worker；NAS 每 5 秒镜像、HDFS 每 120 秒分片。

结果见 `runs/pour-random-l20/summary.json`、`cases/*/result.json` 和 HDFS 回执。基础设施失败时 score 为 null，退出码非零；模型主动停止保留原生有效阶段分，不标成原生终局成功。

## 维护与边界

`release/manifest.json` 覆盖交付源码、依赖、模板和 Skill。修改后审阅，再通过 `uv run --offline --no-project --python /usr/bin/python3 python scripts/seal_release.py --write` 显式更新清单，最后运行离线验收。不加 `--write` 时仅校验。Harness 自身的清单记录发布适配源码提交、新哈希及原始来源，不再声明与原始 Harness 字节相同。旧日期化调度和历史轨迹保留在原实验工程，没有迁移到本分支。本次验证为 CPU 离线验收，未通过新入口重新跑 GPU 实验。
