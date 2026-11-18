# Astra 原生时限复测（进行中）

当前完整 evidence 的首条已完成结果：align 官方成功，97 原生步、43 响应、733,671 输入 token、无重试；validate_episode 通过。coin、stack、egg、cover 仍运行，尚不能汇总五任务成功率。

用户最新范围：取消 baseline 和配置消融，只评估当前完整 evidence setting。下表为已完成的早期配置结果，不能冒充当前完整配置成绩。正在跑的 baseline 已取消并保留检查点，不计任务失败。

仅修改通用 harness，模型输入无奖励/成功/物体真值/内部时限。回合由 RoboDojo 原生逻辑终止，API 无调用上限，单请求 900 秒、5 次瞬态重试。所有数字来自独立校验后的存档，不根据模型的“完成”声明评分。

|配置/任务|原生步|响应|官方成功|部分分数|累计输入 token|
|---|---:|---:|---|---:|---:|
|vision / align|157|59|是|未提供|2,087,294|
|steps / align|133|33|是|未提供|699,160|
|steps / egg|700|251|否|0|33,358,134|

vision 固定当前三视图并为回查配对自身位姿；steps 再增加自身已执行动作计数，不报告内部总/剩余步数。两条 align 均新开完整回合，无恢复；与旧 recovery-align-v1 的 200 步失败相比有正向信号，但旧回合有恢复、API 随机性，不能作为单因素因果证明。新的 baseline 已按用户要求取消，不再补跑。

装蛋在完整 700 步下仍未成功；231 个完整反馈段中 101 段未到位。更多步数并未自动解决抓取与跟踪问题；长文本累计成本也显著增加。feedback/evidence 对照尚待完成，不能提前宣布有效。

baseline cover 在用户取消时尚未结束；模型后半程持续声明完成并保持。这只作为行为观察，不报告终止分数。

四卡 worker 的首次测试全部在模型动作前被 RTX 驱动检查阻塞，未计入成绩。实装驱动 535.261.03 的 Vulkan 数字版本显示 535.5.3.192，RTX 因此拒绝。已保留失败现场并暂停队列，修复部署环境后用新目录重跑；不调整物理、任务、奖励。

产物：artifacts/native-vision-align-v2、native-steps-align、native-e-steps-fill_egg_holder。三条均通过 validate_episode，原始图像/动作与结果已各自备份 HDFS。汇总脚本与矩阵在 artifacts/harness-plan/。
