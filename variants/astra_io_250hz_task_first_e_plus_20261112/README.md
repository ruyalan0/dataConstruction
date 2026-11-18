# e-plus：当前 e 的抓取逻辑，f 的限速

2026-11-12，从 `../astra_io_250hz_task_first_e` 独立复制。

参考关节速度从 0.6 rad/s 调整为 **3.75 rad/s**，与 f 的瓶子任务一致。控制器其余参数保持 e 的配置；整个默认控制器配置与 f 的默认配置一致。

完整继承当前 e 的所有 skill。`put-bottles-into-dustbin` 对倒下瓶子使用竖直向下、夹指间通道平行瓶身的对齐规则，对立着瓶子保留 e 的瓶身或瓶颈抓取逻辑。分工、交接、运输、完成后双臂复位、输入协议和原生任务时限均继承 e。

运行及导出标识为 `astra_io_250hz_task_first_e_plus_20261112`。`parent-e-sha256.json` 记录派生时当前 e 的全部源文件，`variant-provenance.json` 记录精确差异。

本次评测使用 gpt-6.1-sol / high、linear 插值、400 次模型调用预算，重跑瓶子任务 L23–L25。独立布局优先分散到不同物理 GPU。

从本目录使用 `../../.venv-policy/bin/python -B run_local.py` 启动；通过 `--task`、`--task-config`、`--seed`、`--env-gpu` 指定任务、配置、布局和 GPU。
