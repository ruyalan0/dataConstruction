# Astra 输入输出新版本：250 Hz、四位小数、去掉 phase、重复信息保留一份

本目录是独立的新版本，原 `scripts/`、`release/harness/`、RoboDojo 源码和历史运行记录均保留原文件。`harness/` 从原发布版复制，修改只发生在本目录。该副本是实验版本，不是原封存发布版。

先核对历史重复，再实现代码。核对使用十任务 control-only 与 general_pickup L20/L21/L22 共 13 场实验、1,579 轮模型请求。结构化证据见 [audit/duplicates.json](audit/duplicates.json)。

| 比较内容 | 检查次数 | 完全一致 |
|---|---:|---:|
| 上轮 state / 本轮 previous_observation.state | 1,566 | 1,566 |
| 上轮 arm_posture / 本轮 previous_arm_posture | 1,566 | 1,566 |
| 上轮头部、活动手腕图 / 本轮 previous_* 图 | 2,983 | 2,983 |
| 上轮输出 memory / 本轮输入 memory | 1,566 | 1,566 |
| 上轮工具执行反馈.feedback / 本轮 previous_result | 1,566 | 1,566 |

图片同时检查记录的 SHA-256 和实际文件字节；工具调用 ID、反馈步数也与相邻请求对应。工具结果完整外壳还有 step、ended，重复的是其中的 feedback 正文。

## 新行为

- **实际动作间隔为 250 Hz，即 4 ms。** 原物理配置本来是 dt=0.004、250 Hz；原 collect_freq=25 使每个动作扩展为 10 个物理步。新 serve 在内存中设 collect_freq=250、相机频率=250、render_interval=1，并校验实际 collect_interval=1。RoboDojo 原 YAML 没有修改。这是仿真动作时间间隔，不是每秒请求模型 250 次，也不保证每秒墙钟时间完成 250 次渲染。
- **Astra 的数值动作输出最多四位小数。** 提示词要求按四位小数输出；本地先校验原始参数合法性，再按 ROUND_HALF_UP 四舍五入，执行与下一轮回放使用同一份处理后的数值。steps、settle 保持整数，null 保持 null；机器人输入测量值不作四舍五入，文字 assessment/plan/memory 不作数字替换。无需补齐本来不足四位的小数尾零。
- **模型输出移除 phase。** control_arms schema、模型指令和保存的 decision 均不要求 phase。旧执行接口仍使用内部分类：steps≤6 时为 fine，否则为 transit；它由程序生成，不是 Astra 输出。此版本只开放 joint 模式的 control_arms，不能用于原 TCP 工具分支。
- **保留上一轮完整交互，删本轮相等副本。** 状态、姿态、RGB 留在 self.previous 的用户消息；memory 留在上一轮 function_call.arguments；反馈留在 function_call_output。本轮移除相等的 previous_observation.state、previous_arm_posture、previous_* 图片、memory、previous_result。previous_observation 的 step/active_arm 元信息保留，history_references 指向保留来源。首轮/断线重连时若没有上一轮交互，全部保留；任一内容不相等时也保留并记入审计。

steps 仍为 1..20，settle 仍为 0..2，单位现在都是 250 Hz 动作间隔。例如 steps=10 从原来的 40 ms×10=400 ms 变为 4 ms×10=40 ms。没有把旧步数自动乘十；原每步关节变化上限和原生任务步数预算继续按动作步计数。因此新版本改变了动作时长及每步速度含义，需要实际运行后才能评价抓取效果。历史任务成绩不代表新频率的成绩。

## 看处理后的完整一局样例

[examples/general_pickup_L20/manifest.json](examples/general_pickup_L20/manifest.json) 索引了全部 11 轮。每轮分别提供：

- `0002.context.json`：处理后的本轮情况，便于直接阅读。
- `0002.input.json`：系统指令、完整工具 schema、上一轮交互和本轮消息的阅读副本。图片用 SHA-256 与原路径表示，加密推理用占位说明表示。
- `0002.output.json`：去掉 phase、数值四舍五入后的动作参数及执行决策。

这些例子是对 10 月 15 日 25 Hz 日志的**离线格式转换**。它们没有请求模型或执行机器人，图片、状态、反馈仍来自原实验，不能用作新频率的成功轨迹或新训练标签。第二轮原本是上轮 3 图 + 本轮 5 图，转换后为上轮 3 图 + 本轮 3 图。

## 运行与验证

在项目根目录运行新的单场入口，默认 general_pickup、layout 20：

```bash
ISAACSIM_GPU=4 .venv-policy/bin/python -B variants/astra_io_250hz/run_local.py --seed 20
# 等价：显式指定原始设备编号
.venv-policy/bin/python -B variants/astra_io_250hz/run_local.py --seed 20 --env-gpu 4
```

入口沿用原本的环境、资产、API 配置读取和 GPU 空闲检查，启动本目录的 serve、策略及控制代码；新结果默认写入新的 `runs/general_pickup-io-250hz-<时间>/`。可用 `--api-config`、`--api-auth`、`--output` 显式指定路径。新策略拒绝连接非 250 Hz 的服务。

选卡使用 ISAACSIM_GPU 或 --env-gpu，AppLauncher 的 device、渲染设备及 RoboDojo device_id 使用同一个原始 GPU 编号。不会通过 CUDA_VISIBLE_DEVICES 映射设备编号；旧的 --gpu 参数只是 --env-gpu 的兼容别名。物理后端保留原 RoboDojo 配置（当前为 CPU），不因选择渲染卡而切换到 CUDA/Fabric。实际后端和选卡方式另记录在 seed-20/gpu-selection.json。

运行时的 `NNNN.response.raw.json` 保留服务端原始响应；`NNNN.response.json` 保存实际执行和回放的规范化动作参数；`NNNN.output-normalization.json` 记录是否发生数值修正；`NNNN.api-input.readable.json` 是实际发送消息的阅读副本；`NNNN.request.json` 记录本轮删去及保留的字段。实际完整 input 仍包含原图字节，加密推理也按原协议回放。

```bash
.venv-policy/bin/python -B -m unittest discover -s variants/astra_io_250hz/tests -p 'test_*.py' -v
.venv-policy/bin/python -B variants/astra_io_250hz/verify_originals.py
```

离线测试验证完整三轮模型消息、仅开放 control_arms、输入不降精度、输出规范化后执行与回放一致、去掉 phase、差异内容保留，以及调用 RoboDojo 原始插值方法时每动作从 10 个物理步变为 1 个。2026-10-19 已完成一次新版本真实运行：general_pickup L20，21 次 Astra 请求、200 个原生步，环境正常结束，得分 0。详见[实跑报告](../../runs/general_pickup-io-250hz-20261019-164450/RUN_REPORT.md)。

重新生成证据/预览时须指定一个不存在的新文件或目录，避免覆盖已有交付：

```bash
.venv-policy/bin/python -B variants/astra_io_250hz/audit_duplicates.py --output /tmp/astra-duplicates-new.json
.venv-policy/bin/python -B variants/astra_io_250hz/preview.py --output /tmp/astra-preview-new
```

## 主要代码

| 新文件 | 用途 |
|---|---|
| [run_local.py](run_local.py) | 独立启动入口，选择新的 harness 和策略 |
| [device_routing.py](device_routing.py) | ISAACSIM_GPU / --env-gpu 选卡，保留原物理后端 |
| [policy.py](policy.py) | control-only 工具过滤、模型提示词、原传输重试 |
| [harness/egg_harness/io_contract.py](harness/egg_harness/io_contract.py) | 四位小数处理、逐项相等检查、去重与阅读副本 |
| [harness/egg_harness/native_tools.py](harness/egg_harness/native_tools.py) | 无 phase 的 joint 动作 schema 与校验 |
| [harness/egg_harness/native_responses.py](harness/egg_harness/native_responses.py) | 实际发送前去重、原始/处理后响应分别保存 |
| [harness/egg_harness/skill_policy.py](harness/egg_harness/skill_policy.py) | 内部生成执行分类，检查服务端频率 |
| [harness/egg_harness/control_timing.py](harness/egg_harness/control_timing.py) | 动作、相机和渲染的实际 250 Hz 配置与校验 |
| [harness/egg_harness/client.py](harness/egg_harness/client.py) | 接受新频率观测，拒绝误连旧服务 |
| [harness/scripts/serve.py](harness/scripts/serve.py) | 应用新频率，不写 RoboDojo 原配置文件 |
| [harness/scripts/server_loop.py](harness/scripts/server_loop.py) | 将实际频率记录到观测和来源信息 |

原文件 SHA-256 基线见 [audit/original-code-sha256.json](audit/original-code-sha256.json)，原始历史请求、响应和反馈的 SHA-256 见重复审计。可使用 verify_originals.py 再次检查原文件没有改变。

首次离线交付的文件快照保存在 audit/new-files-sha256.json；选卡修正及实跑后的当前源码清单见 [audit/active-source-sha256.json](audit/active-source-sha256.json)。
