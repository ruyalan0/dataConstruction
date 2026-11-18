# General pickup 几何辅助对照实验

2026-10-12：按用户要求准备。实跑启动被自动审批拦截，目前尚未产生对照实验结果。

## 实验范围

同样的 group 0 L20/L21/L22，各一次，GPU 0 串行运行。模型仍为 gpt-6-astra/high，
策略超时 1800 秒，模型调用和原生动作预算沿用原配置。旧基线均为 100 分。

关闭 project_pixels、triangulate_point、triangulate_temporal_point，并从模型输入移除
joint_semantics 中的正负 0.05 弧度运动预测和相应几何工具提示。
这些工具从实际 function schema 和本地允许调用列表同时移除。

保留 control_arms、review_frames、verify_completion、stop_episode，保留四份原版技能、
三相机 RGB、当前/前次机器人测量状态、关节作用/轴/限制、手臂姿态和指尖几何估计、
本回合记忆、动作插值与跟踪监测。不加载新增的跨回合经验 memory。

这是几何定位工具与关节运动预测的联合消融。即使失败，也不能单独归因于某一个关闭项；
即使成功，也不能据此称为无 Harness 或无技能辅助。每布局一次仅提供初步对照证据。

## 实现与验证

release/harness 的磁盘文件保持不变。独立外层脚本在策略进程中安装内存过滤，
模型请求日志记录的是过滤后的实际输入与工具。实际仿真及动作执行复用封存版本。
每场配置、启动记录和结果都包含 ablation 描述与外层脚本哈希。

离线核验已覆盖旧基线全部 27 份请求：几何工具与运动预测均被移除，
旧基线 24 次 control_arms 调用仍通过原有校验；三个关闭工具的调用被本地拒绝。

## 启动命令

当前配置的模型服务地址是 https://api.dudulu12138.xyz/v1。
实跑会向该服务发送仿真 RGB、机器人状态和提示；自动审批要求用户明确授权该数据传输。

```bash
cd /data/lanruya/robodojo/Astra-Robodojo-new
.venv-policy/bin/python -u -B scripts/run_pickup_geometry_ablation_batch.py --gpu 0
```

默认输出 runs/ablation-no-geometry-时间/，含 batch.json、launcher-L20.log 等批次文件，
以及 L20/L21/L22 各自的 results.json、policy 日志、model 请求/响应和 rendered/rollout.mp4。
批次脚本因基础设施错误停止时，尚未运行的布局不会计为抓取失败。
