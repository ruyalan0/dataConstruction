# lanruya：已部署的原版 general pickup 启动方法

部署日期：2026-10-12。项目根目录：`/data/lanruya/robodojo/Astra-Robodojo-new`。
实际模型策略与仿真服务来自封存的 `release/harness`，不加载新增的经验 memory。

## 单场运行

填好自己的 API 配置和凭据文件后运行（示例为 L20、物理 GPU 0）：

```bash
cd /data/lanruya/robodojo/Astra-Robodojo-new
bash scripts/run_pickup_lanruya.sh \
  --seed 20 --gpu 0 --episode-timeout 1800 \
  --api-config /home/lanruya/.codex/config.toml \
  --api-auth /home/lanruya/.codex/auth.json
```

这两个文件必须属于 lanruya 自己的可用配置；本次部署没有复制张泽宇的密钥。
如使用其他文件位置，替换参数，或设置 `ROBO_API_CONFIG`、`ROBO_API_AUTH`。
`config.toml` 需包含 `model_provider` 及对应的 `model_providers.<名称>.base_url`、
`wire_api="responses"`。`auth.json` 需包含有效的 `OPENAI_API_KEY`（`sk-...` 格式）。
服务需支持原版 `gpt-6-astra` / `high`；原版 SDK 的 `max_retries=0` 保持不变。
不要把 key 放进命令、Git 或结果报告；不要用只包含 OAuth 登录信息的 auth 文件。

`--seed` 是布局编号。L20、L21、L22 可直接使用，每条命令只运行一次。
新版外层脚本已支持 `--gpu`，启动前需要该物理 GPU 空闲；默认选择 GPU 0。
它也支持布局 0..54；20..22 以外需要显式填写 `--development-case-reason`，本次部署验收不用该选项。
这些是外层设备和路径配置，原版 Harness 文件未修改。

## 输出与结果

默认输出为项目的 `runs/general-pickup-original-时间/`，也可用 `--output` 指定一个尚不存在的目录。
每场自动导出 `rendered/rollout.mp4`，没有网页服务。外层定期输出动作步数和模型调用数。

查看 `results.json`：`status=native_end`、有效原生终止和两个进程退出码为 0 代表链路完成；
另外看 `success` 与 `official_observation.score` 判断抓取是否成功。
退出码 1 可能是抓取失败，也可能是 API/启动错误，需要结合日志区分。
详细记录在 `sim-<布局>.log`、`policy-<布局>.log`、`policy-<布局>/model/`。

## 本机路径和跨账号说明

| 内容 | 路径 |
| --- | --- |
| 原版 Harness | `release/harness` |
| 固定任务源码 | `dependencies/RoboDojo`、其中的 `XPolicyLab` |
| 策略解释器 | `.venv-policy/bin/python` |
| 仿真解释器 | `/data/lanruya/robodojo/envs/RoboDojo/bin/python` |
| 仿真环境别名 | `/data/lanruya/robodojo/envs/RoboDojo-private -> RoboDojo` |
| 共享资产入口 | `/data/lanruya/robodojo/assets/modelscope-RoboDojo/Assets` |
| 临时文件和 Isaac 缓存 | `/data/lanruya/robodojo/.runtime/` |

原有顶层 `RoboDojo`、仿真环境、`private-assets`、`private-data` 保留；IsaacLab/cuRobo
解析到 lanruya 顶层 RoboDojo 内的源码。机器人 YAML 的路径适配只写进每场的 `runtime-assets/`。

新项目由 zhangzeyu 代为创建，已通过 ACL 赋予 lanruya 读写权限及新内容的默认权限。
启动脚本会在当前进程范围内信任这三个准确的 Git 仓库路径，解决跨账号的 Git ownership 检查。
如果 lanruya 要在终端手动执行 Git，可按需运行以下配置（只影响她自己的 Git 设置）：

```bash
git config --global --add safe.directory /data/lanruya/robodojo/Astra-Robodojo-new
git config --global --add safe.directory /data/lanruya/robodojo/Astra-Robodojo-new/dependencies/RoboDojo
git config --global --add safe.directory /data/lanruya/robodojo/Astra-Robodojo-new/dependencies/RoboDojo/XPolicyLab
```

验收进展与结果见 `/data/lanruya/robodojo/DEPLOYMENT-RESULT.md`。
旧版复制指南中的“启动脚本只支持 GPU 0”描述对应 10 月 10 日之前的脚本；以本文及实际 `--help` 为准。
