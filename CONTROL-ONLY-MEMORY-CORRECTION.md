# control_only 的 memory 关闭修正（历史记录）

> 2026-10-19：按用户最新要求，已经恢复原实验的回合文字memory、近期动作账本与前一轮模型/工具交互回放；control-only仍然只开放control_arms。以下内容保留10月18日关闭memory时的背景与验证结果，已经不代表当前控制代码。恢复详情见[control-only-memory-restoration-20261019.md](/data/lanruya/robodojo/Astra-Robodojo-new/docs/control-only-memory-restoration-20261019.md)。

2026-10-18 核对发现：后续 control_only 采集只过滤了 API 工具名，没有沿用此前的 memory 关闭配置。memory 仍是 control_arms 的必填参数，上一轮模型响应也参与下一轮请求。这不符合用户只保留 control_arms、关闭 memory 的要求。

当前十任务数据含 1,553 条决策，加入 general pickup 后为 1,579 条；全部输入和输出都有非空 memory。已有数据不能用作无 memory 轨迹。原始决策标签和执行记录保留，两个数据集的 README 与 memory-audit.json 明确记录这一限制。尚未重新采集轨迹。

修正后的 control_only 入口仅提供 control_arms，参数定义、模型输入和原始模型输出不含 memory；同时关闭上一轮模型响应回放，以及 recent_executed_segments 和 stalled_segments_this_controller 历史账本。保留当前/上一步公共观测、最近一次真实动作反馈、机器人状态和任务技能。跨回合经验记忆保持关闭。

封存的策略循环需要 decision['memory']，外层适配器只提供空字符串用于兼容，不接受模型填写，也不将它送入下一次模型请求。空值不是模型记忆。

单场启动器、十任务启动器和 general pickup 启动器均将 explicit_episode_memory、model_history_replay、rgb_memory_enabled 写为 false；采集结束校验会拒绝含 memory 的请求/响应或历史回放。导出支持原始无 memory 参数，保留原始 steps/settle、null 和数值；拒绝把有记忆与无记忆批次混合，或将旧批次改标签冒充无记忆数据。

验证：15 项回归测试通过，覆盖实际发送给模拟 API 的参数、第二次请求不回放历史、额外 memory 字段被拒绝、重试不重复动作，以及有/无 memory 数据导出和混合拒绝。新 general pickup 批次的离线准备检查通过。封存发布文件校验通过。本次没有发起模型请求或 GPU 采集。
