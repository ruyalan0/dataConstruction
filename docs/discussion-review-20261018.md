# 2026-10-18 讨论复盘：逐轮输入、输出与代码定位

> 2026-10-19状态更新：用户已要求恢复原实验行为，当前control-only重新保留文字memory、近期动作记录和前一轮交互回放，仍只开放control_arms。下文涉及“当前修正版本/关闭memory”的描述记录的是10月18日状态；相应代码链接已指向恢复前备份。恢复后的入口见[control-only-memory-restoration-20261019.md](/data/lanruya/robodojo/Astra-Robodojo-new/docs/control-only-memory-restoration-20261019.md)。

依据本机保存的三段项目会话整理，时间采用北京时间（Asia/Shanghai），范围为2026年10月18日18:49—22:44。共38条业务输入、32次完整答复；同一次答复前的连续补充放在同一轮，下面均保留其含义。长篇粘贴内容作摘要，短问题保留原意。

文中的“你的输入/当时输出”指我们之间的问答；机器人与模型之间的实际输入输出，另外逐轮拆解在[模型输入输出结构与11轮抓车实例](/data/lanruya/robodojo/Astra-Robodojo-new/docs/model-io-walkthrough-20261018.md)。

最先需要记住的关系是：**程序把当前情况和图片交给模型；模型提出动作；仿真执行后返回新情况和真实反馈；下一轮重新判断。**

这次重新核对了当前源码和原实验记录。旧实验、当前包装代码、训练导出格式分别说明；原实验采集于10月14—15日，昨天的讨论与数据重新整理发生在10月18日。

## 快速定位

| 想看什么 | 对应轮次 |
|---|---|
| 项目结构与执行入口 | 1—3、9 |
| 长输入、字段层级、content/context | 4—8、12、14、18、23 |
| 旧版与当前版的历史信息 | 11、13、24—26 |
| 真实抓车过程 | 19—22 |
| memory、历史重复与工具调用 | 16—17、20—22、27—32 |

## 逐轮记录

### 第01轮 · 18:49:49｜目录结构

**你的输入：** 讲解当前文件夹结构。

**当时输出与核对结论：** 说明项目由发布版控制框架、实验脚本、仿真依赖、技能和实验产物组成；当前这些实验走 release/harness，src/dojo_astra 是另一套早期实现。

**代码/证据定位：** [README.md:1](/data/lanruya/robodojo/Astra-Robodojo-new/README.md:1)；[run_task_tool_ablation_local.py:245](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/run_task_tool_ablation_local.py:245)；[tool_ablation_policy.py:271](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/tool_ablation_policy.py:271)。

**结构讲解：** release/harness 提供控制能力，scripts 负责启动和包装，dependencies 提供仿真，runs 保存一次次实际运行的记录。

### 第02轮 · 18:58:32｜skill_policy 的输入输出

**你的输入：** 讲解 skill_policy.py，注意说明输入输出。

**当时输出与核对结论：** 把运行过程拆成仿真观测、模型决策、机器人命令、执行反馈四类数据，并解释启动参数 --config、--episode、--trace。

**代码/证据定位：** [skill_policy.py:208](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:208)；[skill_policy.py:263](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:263)；[skill_policy.py:300](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:300)；[skill_policy.py:337](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:337)。

**结构讲解：** 配置和观测进入策略循环；策略整理 context 与图片；模型返回 decision；策略发出命令并等待新的 robot.obs。

### 第03轮 · 19:13:32｜逐模块对照源码

**你的输入：** 对照代码，逐个模块讲解。

**当时输出与核对结论：** 按源码顺序解释导入依赖、控制提示词、动作校验、前后帧整理、初始化、输入组装、模型调用、工具分发和结果落盘。

**代码/证据定位：** [skill_policy.py:25](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:25)；[skill_policy.py:115](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:115)；[skill_policy.py:157](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:157)；[skill_policy.py:208](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:208)；[skill_policy.py:307](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:307)；[skill_policy.py:498](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:498)。

**结构讲解：** skill_policy 组织流程；模型通信在 native_responses；工具参数校验在 native_tools；实际控制由仿真服务及 action_control 执行。

### 第04轮 · 19:22:24｜每轮标准输入在哪里

**你的输入：** 引用论文中的 state、memory、previous_result、budget 等说明，询问实验每次给模型的输入位置。

**当时输出与核对结论：** 定位到 context 组装及每轮 NNNN.request.json；question 保存序列化后的 context，images 保存本轮图片清单。这里讨论的是模型请求，不是命令行标准输入流。

**代码/证据定位：** [skill_policy.py:263](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:263)；[skill_policy.py:298](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:298)；[native_responses.py:98](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:98)；[真实首轮请求](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model/0001.request.json)。

**结构讲解：** 完整模型请求还包含控制规则、技能、工具定义和图像内容；question 中的长 JSON 只是其中的文本上下文。

### 第05轮 · 19:43:48｜纠正字段层级

**你的输入：** 指出之前列的论文字段来源表与真实 context JSON 对不上。

**当时输出与核对结论：** 纠正之前只列数据来源、没有列实际层级的问题；应使用 state.right_ee_pose、budget.remaining_native_steps 等完整路径对应。

**代码/证据定位：** [skill_policy.py:263](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:263)；[skill_policy.py:298](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:298)；[实际字段层级](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model/0001.request.json)。

**结构讲解：** 先解析请求文件，再解析 request["question"]；state 和 budget 是对象，其子字段不是全部并列在 context 顶层。

### 第06轮 · 19:45:51｜真实输入为何很长

**你的输入：** 粘贴分篮子任务的真实输入，追问代码是否真的发送这么长的内容。

**当时输出与核对结论：** 确认分篮子首轮 context 为 15,355 字符，其中 joint_semantics 为 10,909 字符，约占 71%；主要篇幅来自双臂关节说明和正负小幅转动的运动学预览。

**代码/证据定位：** [skill_policy.py:273](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:273)；[joint_control.py:191](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/joint_control.py:191)；[分篮子首轮请求](/data/lanruya/robodojo/Astra-Robodojo-new/runs/ten-tasks-control-only-20261014-001220/classify_objects_by_language/L20/policy-20/model/0001.request.json)。

**结构讲解：** joint_semantics.arms.left/right 各有六个关节记录，每个含角度、轴、限位和正负 0.05 rad 预览；预览是计算结果，不表示已执行动作。

### 第07轮 · 19:53:33｜content 与 context

**你的输入：** 为什么真实输入有 content，却没有在 skill_policy 中找到？

**当时输出与核对结论：** 解释 context 是机器人情况数据，content 是模型消息的内容列表；它们在不同文件中创建。导出后的 content 又是适配训练格式的一段字符串。

**代码/证据定位：** [skill_policy.py:300](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:300)；[native_responses.py:81](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:81)；[native_responses.py:93](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:93)；[export_astra_original_ms_swift.py:252](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/export_astra_original_ms_swift.py:252)；[export_astra_original_ms_swift.py:262](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/export_astra_original_ms_swift.py:262)。

**结构讲解：** context → JSON 字符串 question → user 消息的 content；线上 content 同时装文本和图片，导出格式把图片另外放入 images。

### 第08轮 · 19:58:02｜是否配置错误导致冗长

**你的输入：** 追问是否实验配置错了，原代码能否对应这段输入。

**当时输出与核对结论：** 指出长关节说明来自 release/harness 的 joint 模式逻辑；旧 control-only 没关 memory 是另外一个问题。二者不能混为一个原因。

**代码/证据定位：** [skill_policy.py:273](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:273)；[joint_control.py:191](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/joint_control.py:191)；[tool_ablation_policy.py:20](/data/lanruya/robodojo/Astra-Robodojo-new/runs/ten-tasks-control-only-20261014-001220/scripts-snapshot/tool_ablation_policy.py:20)；[实验配置](/data/lanruya/robodojo/Astra-Robodojo-new/runs/ten-tasks-control-only-20261014-001220/classify_objects_by_language/L20/config-20.json)。

**结构讲解：** action_interface=joint 决定加入关节说明；工具开放范围与历史记忆机制分别由包装脚本控制。

### 第09轮 · 20:13:47｜是否调用早期 agent.py

**你的输入：** 现在没有调用 src/dojo_astra/agent.py 吗？

**当时输出与核对结论：** 确认讨论中的实验启动链是 run_task_tool_ablation_local → tool_ablation_policy → egg_harness.skill_policy → native_responses。

**代码/证据定位：** [run_task_tool_ablation_local.py:245](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/run_task_tool_ablation_local.py:245)；[tool_ablation_policy.py:271](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/tool_ablation_policy.py:271)；[skill_policy.py:14](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:14)。

**结构讲解：** 包装脚本把 release/harness 加入模块搜索路径，并在导入 skill_policy 前替换模型类；所以需要对照发布版源码阅读。

### 第10轮 · 20:27:41｜外部 val.jsonl

**你的输入：** 要求查看 /share/workspace/chenxinlong/code/prepare_data/eval_aligned_v5_continuous/output/datasets/RoboDojo/arx_x5/real/val.jsonl。

**当时输出与核对结论：** 当时环境没有可访问的 /share 目录，因此没有读取到该文件，也没有得到其数据结构或内容结论。

**代码/证据定位：** 当时未读到目标文件，没有可引用的已读取代码。

**结构讲解：** 这一轮没有完成文件读取，不能把其他本地 all.jsonl 的格式当成该 val.jsonl 已核验的格式。

### 第11轮 · 20:38:05｜当前代码如何输入历史

**你的输入：** 目前的历史信息输入方式怎样？定位到代码。

**当时输出与核对结论：** 针对修正后的 control-only，说明保留动作前状态、动作前照片及最近执行反馈；删除文字 memory、近期动作账本和上一轮消息回放。

**代码/证据定位：** [skill_policy.py:157](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:157)；[skill_policy.py:264](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:264)；[skill_policy.py:336](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:336)；[tool_ablation_policy.py:50](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/tool_ablation_policy.py:50)；[tool_ablation_policy.py:171](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/tool_ablation_policy.py:171)。

**结构讲解：** 动作执行前保存 previous；下一轮 temporal_inputs 转成 previous_observation 与 previous_* 图片；过滤器只删除指定字段。

### 第12轮 · 20:39:07｜粘贴输入的具体代码

**你的输入：** 给你发的真实输入对应的代码在哪里？

**当时输出与核对结论：** 重新定位长 JSON 初始组装、关节说明追加、模型发送以及训练导出包装四处代码。

**代码/证据定位：** [skill_policy.py:263](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:263)；[skill_policy.py:274](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:274)；[skill_policy.py:300](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:300)；[joint_control.py:191](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/joint_control.py:191)；[export_astra_original_ms_swift.py:252](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/export_astra_original_ms_swift.py:252)。

**结构讲解：** 一条输入经过多处逐步构造；不能只看 context={...} 那一行，就认为其后的字段都不存在。

### 第13轮 · 20:40:21｜旧实验怎样输入历史

**你的输入：** 明确要求定位修改前实验使用的历史信息代码。

**当时输出与核对结论：** 指出旧实验同时使用 context 内的历史字段和外层上一轮完整工具交互回放；旧包装器没有清空 self.previous。

**代码/证据定位：** [tool_ablation_policy.py:20](/data/lanruya/robodojo/Astra-Robodojo-new/runs/ten-tasks-control-only-20261014-001220/scripts-snapshot/tool_ablation_policy.py:20)；[tool_ablation_policy.py:105](/data/lanruya/robodojo/Astra-Robodojo-new/runs/ten-tasks-control-only-20261014-001220/scripts-snapshot/tool_ablation_policy.py:105)；[skill_policy.py:263](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:263)；[native_responses.py:62](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:62)；[native_responses.py:108](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:108)。

**结构讲解：** 内部通道：memory、previous_result、previous_observation 等；外部通道：上一轮用户消息、模型输出与 function_call_output。

### 第14轮 · 20:44:47｜几种输入的包含关系

**你的输入：** 仍不清楚粘贴的真实输入与其他输入是什么关系。

**当时输出与核对结论：** 用包含关系解释：完整模型请求包含系统规则、工具定义和消息；本轮消息里才包含粘贴的 context 与配套图片。

**代码/证据定位：** [native_responses.py:81](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:81)；[native_responses.py:93](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:93)；[native_responses.py:106](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:106)；[export_astra_original_ms_swift.py:262](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/export_astra_original_ms_swift.py:262)。

**结构讲解：** 本轮 context 是完整请求的一个组成部分；旧请求另外带上一轮交互；导出样本则取本轮 context、图片和本轮工具参数作为训练样本。

### 第15轮 · 21:04:15｜改用入门方式解释

**你的输入：** 说明自己刚入门，希望回答能先让自己理解，避免术语堆砌。

**当时输出与核对结论：** 把闭环改述为拍照 → 整理情况 → AI 决定 → 机器人执行 → 再拍照，并用分篮子任务解释情况说明的用途。

**代码/证据定位：** [skill_policy.py:257](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:257)；[skill_policy.py:300](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:300)；[skill_policy.py:337](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:337)。

**结构讲解：** AI 提议下一步，程序负责传递与校验，机器人产生实际结果；三者各有职责。

### 第16轮 · 21:13:49｜memory 字段和工具是否相同

**你的输入：** 确认输入里的 memory 是否不是 memory 工具，并继续粘贴长输入询问其归属。

**当时输出与核对结论：** 确认该 memory 是文字便条；同时纠正“整段输入就是 previous_result”的理解，previous_result 只是整份 context 的一个字段。

**代码/证据定位：** [skill_policy.py:239](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:239)；[skill_policy.py:263](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:263)；[skill_policy.py:303](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:303)；[native_tools.py:24](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_tools.py:24)。

**结构讲解：** context 是一组栏目；memory 是字符串栏目；previous_result 是执行反馈栏目；工具名则由工具清单另行定义。

### 第17轮 · 21:15:21｜memory 的专业名称

**你的输入：** 确认粘贴的是输入信息，并要求用专业术语解释 memory。

**当时输出与核对结论：** 将其解释为显式文本工作记忆：模型生成文字摘要，程序保存，再放入下一轮提示词。

**代码/证据定位：** [skill_policy.py:263](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:263)；[skill_policy.py:303](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:303)；[native_tools.py:24](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_tools.py:24)。

**结构讲解：** memory_t 来自上一轮模型输出；其内容是模型判断，不自动等同于环境测量或成功证明。

### 第18轮 · 21:18:17｜为何只对上 context 的一部分

**你的输入：** 先说真实输入与 skill_policy 对不上，随后贴出 context={...} 的那一行。

**当时输出与核对结论：** 解释那一行只初始化第一批字段；后续又追加 previous_observation、joint_semantics、姿态、budget 和公开任务要求。

**代码/证据定位：** [skill_policy.py:263](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:263)；[skill_policy.py:264](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:264)；[skill_policy.py:274](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:274)；[skill_policy.py:277](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:277)；[skill_policy.py:298](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:298)。

**结构讲解：** 源码里的变量在运行时替换为具体数字；json.dumps 再把整个字典变成字符串，所以真实输入比单行初始化更完整。

### 第19轮 · 21:20:14｜general_pickup 输入输出实例

**你的输入：** 要求找 general_pickup 的输入输出，先讲清楚再讲术语。

**当时输出与核对结论：** 使用白色玩具车 L20：11 次模型决策、64 个原生动作步，最终仿真 success=true、score=100；展示输入、模型输出和执行反馈的区别。

**代码/证据定位：** [第1轮输入](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model/0001.request.json)；[第1轮输出](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model/0001.response.json)；[第1轮执行结果](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model/0001.tool-result.json)；[最终结果](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/results.json)；[skill_policy.py:333](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:333)。

**结构讲解：** 一轮模型决策可执行多个原生步；成功来自环境最终判定。11 轮完整拆解见配套输入输出文档。

### 第20轮 · 21:37:55｜为什么 memory 外还要历史状态

**你的输入：** 输入里有 memory 还不够吗，为什么需要历史状态？

**当时输出与核对结论：** 将 memory 比作实验笔记，历史状态比作原始测量，照片用于观察物体外观与相对运动。随后几轮把“可能有用”和“已证明必需”明确区分。

**代码/证据定位：** [skill_policy.py:157](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:157)；[skill_policy.py:263](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:263)；[skill_policy.py:279](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:279)。

**结构讲解：** 摘要会选择性记录信息；状态保留数值；照片保留外观。三者可能有重合，也各自丢失不同信息。

### 第21轮 · 21:40:05｜是否真的用了历史状态

**你的输入：** 追问刚才的抓车例子是否用到了历史状态。

**当时输出与核对结论：** 确认第9轮输入含第48步与第52步的状态，手腕从约0.9230m升到0.9497m，模型说升高26.7mm；但 memory 也记录了相同基线，因此不能确定模型具体依赖哪条通道。

**代码/证据定位：** [第9轮输入](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model/0009.request.json)；[第9轮输出](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model/0009.response.json)；[skill_policy.py:264](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:264)。

**结构讲解：** 记录可以证明信息被提供、回答与其相符；没有消融对照，就不能证明某一个字段不可替代。

### 第22轮 · 21:41:14｜memory 能否单独替代历史状态

**你的输入：** 先要求找其他结果判断历史字段作用，随后追问能否删历史状态只保留 memory。

**当时输出与核对结论：** 确认作用部分重合；另有关闭文字 memory、保留其他历史的三次抓取成功记录，但没有找到相反方向的“仅用 memory 保留历史”对照。

**代码/证据定位：** [无文字memory抓取报告](/data/lanruya/robodojo/Astra-Robodojo-new/runs/pickup-no-geometry-memory-20261013-132932/RESULT.md)；[skill_policy.py:264](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:264)；[native_responses.py:108](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:108)。

**结构讲解：** 不使用 memory 能成功，不能推出不使用历史状态也能成功；旧消息回放也携带过去状态，验证替代性时需要一并考虑。

### 第23轮 · 21:50:08｜再次确认粘贴输入的范围

**你的输入：** 再次问之前粘贴的输入究竟是哪部分。

**当时输出与核对结论：** 明确它是本轮情况说明，包含任务、当前状态、关节说明、memory、previous_result 等，不是其中任意单个栏目。

**代码/证据定位：** [skill_policy.py:263](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:263)；[native_responses.py:81](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:81)；[export_astra_original_ms_swift.py:254](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/export_astra_original_ms_swift.py:254)。

**结构讲解：** 整份情况说明包含多个字段；第一轮 previous_result=null 仅表示没有先前反馈，不表示整份输入都是历史。

### 第24轮 · 21:53:04｜当前历史机制逐行对照

**你的输入：** 再次要求说明目前历史信息输入方式，并定位代码。

**当时输出与核对结论：** 按先保存动作前观测、执行、取反馈、下轮加入历史、包装器过滤的顺序说明；强调修正后仍有历史状态与照片。

**代码/证据定位：** [skill_policy.py:336](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:336)；[skill_policy.py:338](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:338)；[skill_policy.py:157](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:157)；[tool_ablation_policy.py:50](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/tool_ablation_policy.py:50)；[tool_ablation_policy.py:175](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/tool_ablation_policy.py:175)。

**结构讲解：** 关闭 memory 和消息回放，只是减少历史来源；previous_observation 与 previous_result 仍然进入每轮 context。

### 第25轮 · 21:55:51｜修改前后输入对比

**你的输入：** 连续澄清要看修改前原版，并要求比较修改前后输入。

**当时输出与核对结论：** 使用同一个旧场景离线组装：第二轮 context 从23,554降至19,483字符；本轮配套图片均为5张，完整请求图片出现次数从8降至5。该对比不是重跑任务成绩。

**代码/证据定位：** [对比说明](/data/lanruya/robodojo/Astra-Robodojo-new/runs/before-after-input-comparison-20261018/README.md)；[修改前context](/data/lanruya/robodojo/Astra-Robodojo-new/runs/before-after-input-comparison-20261018/before/02-context.json)；[修改后context](/data/lanruya/robodojo/Astra-Robodojo-new/runs/before-after-input-comparison-20261018/after/02-context.json)；[tool_ablation_policy.py:105](/data/lanruya/robodojo/Astra-Robodojo-new/runs/ten-tasks-control-only-20261014-001220/scripts-snapshot/tool_ablation_policy.py:105)；[tool_ablation_policy.py:171](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/tool_ablation_policy.py:171)。

**结构讲解：** 删除的是 memory、近期/停滞账本、外层消息回放；保留的是当前信息、上一动作前状态/照片和实际反馈。

### 第26轮 · 22:02:34｜all.jsonl 来自哪个版本

**你的输入：** 要求确认整理好的 all.jsonl 对应哪个版本。

**当时输出与核对结论：** 确认13场、1,579条样本来自修改前采集；输入和输出都含非空memory，1,566条存在历史与回放。10月18日重新导出没有改变原实验采集行为。

**代码/证据定位：** [导出清单](/data/lanruya/robodojo/Astra-Robodojo-new/runs/ms-swift-control-only-with-general-pickup-20261015-155448/manifest.json)；[原有memory审计](/data/lanruya/robodojo/Astra-Robodojo-new/runs/ms-swift-control-only-with-general-pickup-20261015-155448/memory-audit.json)；[tool_ablation_policy.py:105](/data/lanruya/robodojo/Astra-Robodojo-new/runs/ten-tasks-control-only-20261014-001220/scripts-snapshot/tool_ablation_policy.py:105)；[export_astra_original_ms_swift.py:262](/data/lanruya/robodojo/Astra-Robodojo-new/scripts/export_astra_original_ms_swift.py:262)。

**结构讲解：** 采集版本决定机器人当时看到什么；导出版本决定如何把已有记录排成训练样本，两者需要分别记录。

### 第27轮 · 22:06:10｜各历史模块的数据结构与重复

**你的输入：** 列举memory、动作/停滞记录、历史状态/姿态、反馈、图片和整轮回放，追问作用重复及数据结构；补充要求定位代码。

**当时输出与核对结论：** 解释字符串、对象和列表；指出previous_result会与最近动作记录中的feedback重合，上一轮内容还会与本轮历史字段重合；这批1,579条的stalls字段始终为空。

**代码/证据定位：** [skill_policy.py:263](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:263)；[skill_policy.py:279](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:279)；[skill_policy.py:343](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:343)；[skill_policy.py:495](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:495)；[native_responses.py:62](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:62)。

**结构讲解：** recent最多取8条，stalls最多取12条；control_arms分支写recent但不追加stalls，因此空stalls不能证明没有受阻。

### 第28轮 · 22:14:03｜同一历史为何出现两次

**你的输入：** 追问“上一轮作为旧消息重发，又放进本轮历史字段”是什么意思。

**当时输出与核对结论：** 用手腕高度0.9230m举例：它先是旧用户消息的state，又是新消息里的previous_observation.state。重复的是一部分过去信息。

**代码/证据定位：** [skill_policy.py:264](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:264)；[native_responses.py:62](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:62)；[native_responses.py:108](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:108)；[包含旧高度的新context](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model/0009.request.json)。

**结构讲解：** self.previous与observation是不同的容器；容器内容有交集，并不表示两个容器完全一样。

### 第29轮 · 22:15:31｜重发由哪行控制

**你的输入：** 为什么重发上一轮，对应控制代码在哪里？

**当时输出与核对结论：** 定位self.previous的准备与发送：observe覆盖保存最近一次完整工具交互，ask用input=[*self.previous, observation]拼接；当前control-only发送前清空它。

**代码/证据定位：** [native_responses.py:62](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:62)；[native_responses.py:108](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:108)；[tool_ablation_policy.py:175](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/tool_ablation_policy.py:175)。

**结构讲解：** self.previous是客户端变量，并非API中名为self.previous的字段；API收到的是它展开后构成的input列表。该赋值直接回放最近一轮，不会逐轮追加全部旧消息。

### 第30轮 · 22:16:36｜只保留 memory 的设计条件

**你的输入：** 再次询问本轮历史与memory是否重合，以及能否只用memory解决。

**当时输出与核对结论：** 指出模型在机器人执行前写memory，而真实执行反馈在动作后才生成；所以现有memory不会自动包含刚发生的中断原因。仅用memory保留历史需要新的执行后摘要机制与对照验证。

**代码/证据定位：** [skill_policy.py:303](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:303)；[skill_policy.py:337](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:337)；[skill_policy.py:338](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:338)；[native_responses.py:108](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:108)。

**结构讲解：** 时间顺序是写便条 → 执行动作 → 得到反馈；下一轮反馈与便条可能描述不同时间点。

### 第31轮 · 22:26:20｜memory 工具有没有被调用

**你的输入：** 问检测成功轨迹的memory工具是否使用过，并明确不是输入memory字段。

**当时输出与核对结论：** 就这13场而言，1,579次原始工具调用全部是control_arms，名为memory的工具调用为0。成功由仿真标志、终止与稳定性共同判断；尚未定位到你所说的另一种memory工具实现。

**代码/证据定位：** [实际工具定义](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model/native-tools.json)；[tool_ablation_policy.py:15](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/tool_ablation_policy.py:15)；[server_loop.py:95](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/scripts/server_loop.py:95)；[本次全量核对](/data/lanruya/robodojo/Astra-Robodojo-new/docs/data/discussion-review-20261018-audit.json)。

**结构讲解：** 工具清单、输入字段和环境成功判定是三个不同位置；没有memory调用不能直接推广为整个项目不存在任何其他记忆模块。

### 第32轮 · 22:44:07｜用实验核实历史重复

**你的输入：** 要求从结果记录检查self.previous与observation是否真的重合，重点核实previous_observation。

**当时输出与核对结论：** 确认有重合：十任务1,543组相邻请求的历史状态全部等于前一轮state，2,937次历史图片引用哈希全部匹配。当前control-only清空消息回放后消除了这条重复来源。

**代码/证据定位：** [抓车第1轮](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model/0001.request.json)；[抓车第2轮](/data/lanruya/robodojo/Astra-Robodojo-new/runs/general-pickup-control-only-20261015-155448/general_pickup/L20/policy-20/model/0002.request.json)；[skill_policy.py:157](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/skill_policy.py:157)；[native_responses.py:62](/data/lanruya/robodojo/Astra-Robodojo-new/release/harness/egg_harness/native_responses.py:62)；[tool_ablation_policy.py:175](/data/lanruya/robodojo/Astra-Robodojo-new/runs/control-only-memory-restore-20261019-150505/before/scripts/tool_ablation_policy.py:175)；[本次复核统计](/data/lanruya/robodojo/Astra-Robodojo-new/docs/data/discussion-review-20261018-audit.json)。

**结构讲解：** 历史状态、照片、反馈在旧请求两条通道中重复；本轮当前状态依然是执行后的新数据，不与上一轮完全相同。

## 已核实的共同结论

| 结论 | 依据与适用范围 |
|---|---|
| 旧数据来自修改前采集 | 13场实验、1,579条样本均有输入/输出memory；1,566次回放 |
| 旧请求确有重复 | 全部1,566组相邻请求，历史state、memory、姿态和反馈分别与上一轮来源一致 |
| 1,543与1,566两个数字并不矛盾 | 前者仅十任务；再加3场general_pickup的23组，合计1,566组 |
| 当前control-only仍有历史 | 保留previous_observation、previous_arm_posture、previous_result和动作前图片 |
| memory字段不等于memory工具 | 该数据集1,579次工具调用全部为control_arms |
| 不能据此认定memory可完全替代历史 | 没有“仅memory保留历史”的对应对照；也不能把模型说法当作环境真值 |

本次复核详情见[结构化审计结果](/data/lanruya/robodojo/Astra-Robodojo-new/docs/data/discussion-review-20261018-audit.json)，白色玩具车每轮实际参数见[11轮结构化输入输出](/data/lanruya/robodojo/Astra-Robodojo-new/docs/data/general-pickup-20261018-io.json)。本次交付为文档与离线核对，没有新增模型调用或仿真回合。
