# control-only原版memory恢复记录

恢复时间：2026-10-19T15:08:52.362868+08:00。按用户最新要求恢复原实验版本的行为。

## 恢复后的行为

- 模型输入context保留memory、recent_executed_segments、stalled_segments_this_controller。
- control_arms参数继续使用原版JointArms，memory是必填字符串；模型返回的memory保存并传入下一轮。
- observe保存上一轮观测、模型原始输出和真实工具反馈；下一次请求通过input=[*self.previous, observation]回放。
- 当前/动作前状态、照片、姿态说明与最近执行反馈保留。
- 工具集合仍为control_arms；跨回合RGB经验记忆沿用原实验的关闭状态。

## 代码定位

- [策略输入过滤](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/tool_ablation_policy.py:20)：恢复为只过滤几何/回查提示，不移除memory或动作账本。
- [模型提示词](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/tool_ablation_policy.py:48)：要求保留回合文字记忆。
- [模型调用](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/tool_ablation_policy.py:105)：直接使用原通信逻辑，保留上一轮交互。
- [反馈回传](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/tool_ablation_policy.py:169)：使用原版observe。
- [原版memory字段](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_tools.py:24)与[双臂参数](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_tools.py:114)。
- [单场启动配置](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/run_task_tool_ablation_local.py:170)、[批量启动配置](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/run_tool_ablation_batch.py:114)、[general_pickup配置](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/run_general_pickup_control_only.py:93)：为新control-only运行记录启用回合文字记忆与模型历史回放，确保配置与实际行为相符。
- [general_pickup结果检查](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/run_general_pickup_control_only.py:56)：接受并检查恢复后的memory参数、输入、输出及历史回放。

核心策略scripts/tool_ablation_policy.py与[10月14日原实验快照](/data/lanruya/robodojo/Astra-Robodojo-new/runs/ten-tasks-control-only-20261014-001220/scripts-snapshot/tool_ablation_policy.py)逐字节一致。两者SHA-256均为：

```text
32f45c1c1105a2bd84020deeea2c7d1c391407dbc9a35ba1797c7f1675b2edf7
```

启动脚本另外保留了准确记录memory启用状态的配置与审计字段；并非将整个仓库或所有实验脚本回退到某次Git提交。封存的release/harness及历史实验结果保留。

## 验证

- `.venv-policy/bin/python -B -m unittest discover -s scripts -p 'test_*.py' -v`：14项测试通过。覆盖memory字段保留及必填、两轮交互回放、工具集合、瞬态重试和有/无memory旧数据导出。
- 三个启动入口导入与--help通过。
- 临时目录准备general_pickup配置：配置、batch.json、summary.json均正确记录explicit_episode_memory=true、model_history_replay=true、rgb_memory_enabled=false。
- 用恢复后的结果检查重新读取原general_pickup L20/L21/L22记录：11、9、6次请求均通过；未重新校验已有视频。
- 封存发布文件清单与哈希校验通过。

上述验证均为离线检查与模拟API测试，未据此产生新的机器人任务成绩。

## 恢复前备份

修改前文件、SHA-256和验证记录保存在[restoration.json](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/restoration.json)；源码备份位于同目录before/。10月18日讲解文档中指向关闭memory版本的链接已改为对应备份，便于继续核对历史。
