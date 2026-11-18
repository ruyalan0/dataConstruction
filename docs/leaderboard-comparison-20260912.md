# RoboDojo 官方逐任务最高分与 Astra 开发结果对照

检索日期：2026-09-12。官网数据更新日期：2026-09-10。来源：[官方榜单](https://robodojo-benchmark.com/leaderboard)及其直接加载的[数据脚本](https://robodojo-benchmark.com/assets/index-BQSAx3cX.js)。当前包含 40 个模型、42 个基础任务及 12 个泛化随机配置，共 54 个配置。

## 先区分单次结果和榜单名次

我们当前四条已结束的新版本开发回合分别是排列成功、投币部分分 20、堆叠部分分 15、装蛋部分分 0；盖杯仍未结束。每项只有一个开发布局的新版本回合。官方逐任务列是多回合聚合成绩，因此以下“插入位置”仅把单次分值代入官方已报告均分排序，不能称为实际官方名次，也不能据此确定总榜名次。

排列结果文件没有 observed_progress_score 字段，但 official_ended=true 且 task_success=true。依据官方成功回合得分 1.0、汇总乘 100 的规则，其单回合分数可解释为 100；这不是新增读取出的字段，也不是 150 回合均分。参见[协议核查](leaderboard-protocol-research.md)及[官方评测计分源码](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/ee67a1468510da7624a089164402359f2afc72c8/src/eval_client/eval_env.py#L799)。

## 当前五任务对照

下表按 Score（0–100）比较；投币和堆叠的非零部分分并不代表任务成功。官方最高分与模型名称均来自上述官方数据脚本。

| 任务 | 官方最高平均分 | 最高分模型 | 我们单回合分 | 单次分值插入位置 |
| --- | ---: | --- | ---: | --- |
| 排列积木 / align_blocks | 0.00 | 38 个已报告模型均为 0 | 100（按成功规则解释） | 第 1 |
| 投币 / deposit_coin | 24.67 | Xiaomi-Robotics-1 | 20 | 第 2 |
| 堆叠 / stack_blocks | 49.67 | GalaxeaVLA (G0.5) | 15 | 第 14 |
| 装蛋 / fill_egg_holder | 11.67 | Xiaomi-Robotics-1 | 0 | 并列 第 38 |
| 盖杯 / cover_blocks | 100.00 | DM0.5 | 未结束 | 暂不比较 |

插入位置算法：1 + 官方已报告分数严格高于本次单回合分数的模型数，仅按 Score 计算并列位次；官网还会用 SR 和模型名打破平局，因此这里不是页面实际显示名次。排列有 38 个已报告模型，ACT 与 SmolVLA 的该任务缺数据，不能把缺失值当成实测 0；其余四任务均有 40 个已报告模型。装蛋的 0 与 H-RDT、OpenVLA-OFT、TinyVLA 并列，因此插入后并列第 38。

最值得追加验证的是排列：当前官方所有已报告模型的该项均分都是 0，而我们已有成功开发回合。投币单次 20 高于多数榜单均分，但仍未完成插入；不能将这次局部进展解释成 20% 成功率。堆叠单次 15 对应参考第 14；装蛋仍为 0。

## 总榜为什么还不能定名次

官方完整评测覆盖 54 个配置、3 个 seed，总计 6300 回合，再按五个能力维度聚合；我们尚缺大部分任务、随机布局与重复评测。不能把这四项单次分数的均值与总榜 Average 比较，也不能给未测任务默认填 0 后宣称一个正式名次。评分、成功率和认证条件的逐项一手来源见[协议核查](leaderboard-protocol-research.md)。

官网当前 Score 总榜前三为 DM0.5 24.90、GalaxeaVLA (G0.5) 20.23、Xiaomi-Robotics-1 20.07。此处仅记录参照，不与四个任务的平均值混算。

## 42 个基础任务的最高分

“最高 SR”是独立取所有模型成功率的最大值，可能由不同于最高 Score 的模型取得；CSV 同时保留两项各自的全部并列模型。所有条目来自官方数据脚本的标准任务键。

| 任务 | 最高平均 Score | 最高 Score 模型 | 最高 SR |
| --- | ---: | --- | ---: |
| align_blocks | 0.00 | 38 个已报告模型并列 0 | 0.00% |
| arrange_largest_number | 19.80 | Xiaomi-Robotics-1 | 9.33% |
| build_tower | 82.93 | GalaxeaVLA (G0.5) | 78.67% |
| classify_objects | 26.83 | DM0.5 | 16.67% |
| classify_objects_by_language | 2.47 | Meituan-Robotics-0 | 0.67% |
| cover_blocks | 100.00 | DM0.5 | 100.00% |
| deposit_coin | 24.67 | Xiaomi-Robotics-1 | 19.33% |
| fasten_screws | 40.20 | DM0.5 | 12.67% |
| fill_egg_holder | 11.67 | Xiaomi-Robotics-1 | 0.67% |
| fill_pen_holder | 44.73 | Xiaomi-Robotics-1 | 22.00% |
| fold_clothes | 88.27 | OpenWAM-α | 86.67% |
| general_pickup | 35.33 | MolmoAct2 | 35.33% |
| hang_mugs | 29.40 | OpenWAM-α | 10.67% |
| imitate_sorting_sequence | 2.90 | OpenWAM-α | 0.67% |
| insert_key | 14.90 | GalaxeaVLA (G0.5) | 0.00% |
| insert_tubes | 71.73 | DM0.5 | 59.33% |
| make_kong | 90.00 | GalaxeaVLA (G0.5) | 90.00% |
| make_toast | 22.33 | Xiaomi-Robotics-1 | 10.67% |
| match_and_pick_from_conveyor | 70.67 | DM0.5 | 70.67% |
| organize_table | 62.50 | OpenWAM-α | 14.67% |
| pack_objects_into_box | 30.67 | OpenWAM-α | 9.33% |
| pick_from_conveyor_by_image | 1.33 | Pi-05 | 1.33% |
| play_Xylophone | 6.67 | EventVLA / X-WAM | 6.67% |
| play_stacking_toy | 1.20 | DM0.5 | 0.00% |
| play_tic_tac_toe | 65.23 | GalaxeaVLA (G0.5) | 58.67% |
| plug_in_charger | 4.00 | OpenWAM-α / Xiaomi-Robotics-1 | 4.00% |
| pour_balls_into_vase | 46.00 | Xiaomi-Robotics-1 | 46.00% |
| pour_by_language | 0.00 | 38 个已报告模型并列 0 | 0.00% |
| pour_liquid_into_cup | 64.00 | Xiaomi-Robotics-1 | 64.00% |
| press_by_number | 95.33 | DM0.5 | 95.33% |
| push_T | 1.33 | Xiaomi-Robotics-1 | 1.33% |
| put_bottles_into_dustbin | 97.70 | Xiaomi-Robotics-1 | 96.67% |
| solve_equation | 0.00 | 38 个已报告模型并列 0 | 0.00% |
| sort_nesting_dolls_by_size | 25.33 | Xiaomi-Robotics-1 | 25.33% |
| stack_blocks | 49.67 | GalaxeaVLA (G0.5) | 42.67% |
| stack_blocks_by_language | 4.80 | DM0.5 | 2.67% |
| stack_bowls | 89.60 | OpenWAM-α | 88.00% |
| store_laptop_and_headphones | 44.27 | OpenWAM-α | 28.00% |
| store_tools_in_toolbox | 0.33 | OpenWAM-α | 0.00% |
| swap_T | 0.00 | 40 个已报告模型并列 0 | 0.00% |
| swap_blocks | 18.67 | DM0.5 | 18.67% |
| sweep_blocks | 6.67 | DM0.5 | 6.67% |

## 12 个随机配置的最高分

| 配置 | 最高平均 Score | 最高 Score 模型 | 最高 SR |
| --- | ---: | --- | ---: |
| arrange_largest_number_random | 2.07 | OpenWAM-α | 0.00% |
| fold_clothes_random | 29.87 | MolmoAct2 | 25.33% |
| hang_mugs_random | 6.60 | Xiaomi-Robotics-1 | 0.00% |
| make_toast_random | 10.67 | GalaxeaVLA (G0.5) | 1.33% |
| pack_objects_into_box_random | 15.40 | Pi-05 | 5.33% |
| pour_liquid_into_cup_random | 30.67 | DM0.5 | 30.67% |
| push_T_random | 0.00 | 40 个已报告模型并列 0 | 0.00% |
| sort_nesting_dolls_by_size_random | 1.33 | Xiaomi-Robotics-1 | 1.33% |
| stack_blocks_random | 3.60 | Xiaomi-Robotics-1 | 0.00% |
| stack_bowls_random | 24.40 | Xiaomi-Robotics-1 | 16.00% |
| store_laptop_and_headphones_random | 45.33 | Xiaomi-Robotics-1 | 17.33% |
| sweep_blocks_random | 1.33 | GalaxeaVLA (G0.5) / DM0.5 | 1.33% |

## 数据与复核记录

- [完整最高分 CSV](data/robodojo-task-maxima-20260912.csv)：54 行，包含最高 Score / SR、各自全部并列模型和已报告模型数量。
- 原始官方数据脚本 SHA256：`364aadb9777d369084348b4eca462d77f7f86712f02dbb1eba63b430eb4eea20`。
- 原始 HTML、脚本、40 模型逐任务 JSON、完整比较结果与下载测速记录位于 `artifacts/leaderboard-20260912/`。
- 解析校验：40 个显示名称与 40 个数据模型一一对应；基础任务 42、随机配置 12；已报告任务缺失值单独保留。
- 本地结果复查时间：2026-09-12T17:51:21.945391+08:00；装蛋新结束回合已通过 validate_episode。
- 本次不修改运行策略，不追加仿真，不把离线榜单信息传入当前模型会话。
