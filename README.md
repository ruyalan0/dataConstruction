# Astra-RoboDojo Harness + Skill

全 Joint、原生 function tools 的 Harness 与任务 Skill。提供固定版本源码、离线依赖恢复和 random12 L20 运行入口。

## 离线验收

在仓库根目录使用 Python 3.11 和 uv：

```bash
uv run --offline --no-project --python /usr/bin/python3 python scripts/physevo_release.py verify
uv run --offline --no-project --python /usr/bin/python3 python scripts/check_random_runtime_import.py
uv run --offline --no-project --python /usr/bin/python3 python scripts/check_private_release.py
uv run --offline --no-project --python /usr/bin/python3 python scripts/check_portable_release.py
```

上述检查无需 GPU、API key、HDFS 或原实验目录。它们验证文件清单、显式私有配置、两个 bundle 的离线恢复、12 个 random L20 配置。临时恢复目录自动清理。

## 内容与入口

| 路径 | 内容 |
|---|---|
| `scripts/physevo_release.py` | 校验、恢复依赖、准备配置、单场执行 |
| `release/harness/` | 源自 `6c69fb9` 的发布适配 Harness；已修改路径、接口配置及对应检查，独立封存新哈希 |
| `skills-upstream/` | 最近的 Skill，倒水 Skill 保留 `b04f9b4` 原字节 |
| `release/templates/random12/` | random12 L20 策略参数、校准、Skill 和固定源码版本 |
| `release/bundles/` | RoboDojo/XPolicyLab 固定版本源码、历史和原始许可/作者署名 |
| `release/manifests/` | 外部资源大小、SHA-256 和文件清单；不含真实存储位置 |
| `release/private-config.example.json` | 私有资源和模型接口配置模板，示例地址会被执行入口拒绝 |
| `release/results/random12-L20.json` | 历史成绩：均分 37.92，4/12 原生成功；不是本入口重新评测的结果 |
| [部署说明](release/DEPLOYMENT.md) | worker 配额、私有配置、依赖恢复和运行命令 |

新入口覆盖有完整恢复清单的 random12 L20；不声称其他 42×5 布局已由此入口重测。运行时约 10.1 GB、资产约 4.78 GB，需使用所有者提供的私有位置配置和访问权限，Git 中不包含这些归档。

## 运行约定

GPU、模型和 HDFS 重负载在 Running L20 worker 执行，先比较控制 Pod 与 worker 的实际 cgroup CPU、内存、swap、GPU。模型固定 zzy-astra / gpt-6-astra / high；接口地址和仓库外凭据显式传入。默认不发送通知。

回合与模型请求共享上限均为 5，各调用使用同一共享锁目录，每张 GPU 同时一场。NAS 每 5 秒镜像，HDFS 每 120 秒分片；基础设施故障不记作任务 0 分。

`local/`、`runs/`、`dependencies/` 均被 Git 忽略；其中运行日志和本地配置可能包含实际环境信息，不应加入发布包。根目录旧 `src/dojo_astra`、旧脚本和 [旧 README](LEGACY_README.md) 用于历史追溯；旧脚本须按目标环境配置，当前受验收的执行入口为 `scripts/physevo_release.py`。
