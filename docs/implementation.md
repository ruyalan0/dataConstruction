# 第一阶段实现与验收

## 当前范围

单环境 ARX X5 / EE 适配器，世界或末端 link6 坐标系的平移与旋转、夹爪命令、原图存档和回查、实际运动反馈、每次只返回一个原生动作。当前 Chat Completions 运行模式显式设置 reasoning_effort=none；这只是已验证的接口基线，不能代表计划中的推理增强 Astra。

## 已核实的本地接口合同

- RoboDojo 源码：8f2ad9de613e841a33e23d96b5385b15e9d5fa3c；XPolicyLab：724e4bb73a1a70df3570406e469144d07da55d1a。现有目录为用户开发分支，不能直接标注官方原版成绩。
- `obs_manager.py` 输出 v1.0；RGB 在 vision/camera/color；state 的左右末端位姿为环境原点相对位置、世界朝向，米和 wxyz。
- 末端为 link6，不是夹持中心；gripper_bias=0.145，第一版不擅自把此常数当作经验证 TCP 转换。
- 左右机器人基座有旋转，因此 world 不称为 base；tool 指观测 link6 的局部轴。
- `solve_ik` 默认 world，不应用 relative 分支的 delta_matrix；输入保持观测位姿。
- 夹爪归一化 0/1 对应 close/open；state 的 ee_joint_state 来自 prev_control，不代表实测宽度。
- arx_x5 默认关闭 approximate_depth/depth/intrinsics/extrinsics；适配器只复制 RGB、末端位姿、夹爪命令回显和任务指令。

## API 实测（2026-09-11）

凭据仅从用户指定示例的 AST 读取，不执行示例、不复制密钥到仓库。模型返回 gpt-6-astra-2026-09-03。

1. Chat Completions 默认推理 + function tools：400，要求 Responses 或 reasoning_effort=none。
2. Azure SDK 默认 Responses 路径：404。
3. 网关 crawl/v1/responses 路径：404。
4. Chat Completions + reasoning_effort=none：工具调用成功。
5. RGB + act/recall + tool result 多轮 + episode reset：真实 API 测试通过，4 次请求；合成图像，不是机器人成功率。

每轮保存完整 assistant 消息（包括附加字段）及 tool call/result 关联。API 不自动重试；异常终止，不发重复动作。当前会话保留全部消息，尚未实现图像历史裁剪和长上下文预算管理。

## 验收顺序

1. CPU 合同测试：米/毫米、wxyz、世界与工具旋转、保持另一臂、权限字段过滤、拒绝非法动作、队列不累积、原生边界中断。
2. 真实 API 小请求验证（已完成）。
3. worker 官方环境控制冒烟：保持、小幅 z 平移、反馈与原图落盘。只验证接口，不计榜单成绩。
4. 同接口人工操作与至少五个能力维度的代表任务；固定开发 seed 与独立验证 seed。
5. 恢复推理模式后运行 A–D 递进对照，再决定是否实现 E。正式全榜前核实版本和闭源 API 准入。

完整总榜和模块收益当前均未验证，不以本地源码或单次 API 成功替代这些验收。

## 运行资源与协议核对

L20 worker 0000000 实测 22 CPU / 115 GiB RAM / 46068 MiB 显存，启动前 GPU 空闲，无 swap 设备；cgroup memsw 为无限制哨兵值，不作为可用内存。本机为 8 CPU / 32 GiB / 无 GPU。

官方 quick-evaluation 页面本次已读取，确认 42 基础任务、54 runnable 配置、三个 seed 与 native 次数。6300 episode 总量和 verified 闭源 API 条件仍待进一步核验。来源：https://robodojo-benchmark.com/doc/usage/quick-evaluation/ 。Responses 参考：https://developers.openai.com/api/reference/responses/create 。

模拟器初轮失败暴露 worker 驱动链接损坏与 X5 curobo.yml 未生成。匹配内核 535.183.06 的驱动/JIT 库在 /tmp/astra-driver-535.183.06 单独链接，CUDA cuInit=0、设备数=1。运行资产通过 prepare_assets.py 创建独立视图，仅按官方 curobo_tmp.yml 替换路径，不改外部资产或物理参数。

Isaac 的 app.close 可能覆盖失败退出码，验证必须检查 result.json 和 exit-status.json，不能仅依赖 shell 状态。

v2/v3 仍因 Isaac bundled Warp 1.8.2 看不到 CUDA 设备，CuRobo 在 warp.from_torch 内抛出 IndexError。显式预加载驱动和提前 torch.cuda.init 也未通过。运行环境原 pip Warp=1.11.0，实际 Kit 装载的是 1.8.2；版本装载冲突是下一步诊断方向，尚未证明为唯一根因。没有原生动作或任务成功率产物。

## 后续验证：阻塞已推进（2026-09-11）

- CUDA 根因已证实为同进程混载 libcuda 535.183.06 与 560.35.05。使用 `LD_LIBRARY_PATH=/usr/local/cuda-12.6/compat:/tmp/astra-driver-535.183.06` 并取消旧 LD_PRELOAD 后，原 Warp 1.8.2 与 Torch 互操作通过，完整模拟器初始化通过。无需更换 Warp 版本。
- 缺失的 cube / triangular_prism / camera_stand 按 HF commit cfb06d1dadcdf03ccc923102b0e3f3b0a68dfc43 恢复，共 35 文件；LFS 文件逐一校验 SHA256 和大小。
- v6 暴露探针缺少官方 run_reward 注册。已修复并核对每个动作的官方计数；v7 校准通过：保持误差小于 0.1 mm；左手 +z 5 mm 实测 4.99779 mm，右手漂移 0.02819 mm，末端姿态误差约 0.0173°。仅为单布局控制验收。
- RGB JSON high 两轮与 reset 通过；真实场景三相机回放返回合法动作。首次回放非法 30° 旋转保留为失败证据，并将 15° 约束加入模型可见 schema。
- JSON high 没有原生工具关联，用本地 operation_id 和 JSON 结果消息续接，非法返回立即失败，不是 Responses 替代实现。high 参数接受不等于额外推理生效，当前 usage.reasoning_tokens=0。
- v8 真实端到端：独立 API 进程接收白名单 RGB/状态，模型请求 +x 30 mm / 6 步；仅执行首原生步，反馈位移 x=14.0971、y=4.2211、z=0.0084 mm。后观测已保存。首步存在横向偏差，剩余段未执行，不能声称到达目标；尚未评测连续 episode。

## 连续闭环与产物验证（2026-09-11）

- 持久策略进程隔离 Isaac 与 API 依赖，同一 episode 连续运行、严格请求序号、每原生步存白名单观测。异常立即退出，不自动重试 API 或动作。
- 独立验证器核对退出状态、单会话身份、动作/观测/API计数、原图存在性和预算终止语义。20项CPU测试通过，包括缺失后观测和误报任务成功的拒绝测试。
- 实际连续回合首次出现非零 reasoning tokens，修正早期全零探测的局限；尚无 high/none 成功率对照。
- 新请求记录开始/完成及墙钟耗时，用于后续预算分析。完整历史当前导致第1至7次输入由1858增长到11972 token；图像上下文窗口尚未实现。
- 每步NAS检查点能保留已发生的证据，但不是物理世界快照；worker丢失后只能将当前episode标失败，不能从该文件恢复模拟器。后续批量任务应按独立episode分片恢复。
