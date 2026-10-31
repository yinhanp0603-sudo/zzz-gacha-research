> 历史只读快照：不作为当前实施规范。当前入口为项目根目录 prompt_spec.md；后续更新只修改模块文档和配置。

# PROMPT_SPEC：绝区零公开样本分析与战斗代理情景研究——完整设计

> 设计规格 v1.0｜2026-10-27｜整合已讨论的 V1–V3 修复要求  
> 状态：可用于分阶段实施的设计基线；当前交付为文档，不表示代码、数据接入或性能已经验证。  
> 本文取代原 v0.2 定位说明及旧版的固定数值、预设胜负、强制生存模型和自动执行要求。

## 0. 设计摘要与实施边界

项目基于公开终局战斗样本，描述角色与队伍的跨期表现；建立参考 ZSim 防御区公式的参数化战斗代理模型，在受控配置下比较环境、伤害类型、支援与投资；最后用显式行为假设探索抽取、资源积累、模拟收入和模拟留存。

三层分别交付、分别标识证据，不能相互冒充：

| 层 | 名称 | 输入与主要产物 | 能回答的问题 |
| --- | --- | --- | --- |
| Layer 1 | 公开样本观测 | ShiyuStats 数据快照；覆盖表、出场、配队、成绩与配装统计 | 所取得样本中发生了什么，比较受哪些限制 |
| Layer 2 | 固定参考队伍面板 | 代理模型、预注册队伍和环境；固定基准、配对差值、适用性轨迹 | 给定模型内，改变指定因素有什么影响 |
| Layer 3 | 模拟玩家行为面板 | 战斗表、逐抽状态机、钱包与行为参数；全库存、支付、存续与情景比较 | 给定行为与供给假设时，哪些结果可能出现、何时反转 |

完整 ZSim 引擎是可选的独立验证适配器，不能把引用少量公式的代理模型输出命名为“ZSim 实战结果”。公开样本相关性、模型内受控效应、现实因果效应必须分开。

首个可交付版本按 L1 → L2 → L3 顺序建设。L1 数据不可得时，允许先验证合成 L2/L3，但 L1 标记为 unavailable，报告不得用合成记录补充公开样本。

核心问题是跨期适用性、环境适配与支援收益；耐用品与延迟购买理论仅作行为动机，不求解科斯均衡。允许没有反弹、支援无明显收益、策略排序不稳定、无法区分原因。没有固定半衰期或某策略必胜的验收要求。

### 0.1 两类运行模式

- `observed_analysis`：只对已下载、通过检查的公开记录做描述统计；范围由文件实际覆盖决定。没有稳定跨期匿名 ID，不建立真实玩家面板。
- `synthetic_scenario`：使用本设计给出的教学配置运行代理与行为模型；时间标签为 P01…P10，禁止映射成“真实 v1.0–v3.3 历史”。
- `historical_scenario`：可选。仅在日期、敌人、天气、角色、配装可用性及事件配置可追溯后启用；无法重建的部分仍标为假设，不能宣称完整历史还原。

### 0.2 明确不涵盖

真实充值、囤抽和留存估计；全体玩家代表性；厂商商业意图判断；完整多队满星优化；全角色全敌人精确还原；跨厂映射；真实利润或全局商业最优。有限期累计模拟支付不命名为完整 LTV。

## 1. 来源、许可与证据登记

### 1.1 已检查的固定源码基线

| source_id | 来源 | 固定基线 / 用途 |
| --- | --- | --- |
| SRC_SHIYU | [ShiyuStats](https://github.com/LvlUrArti/ShiyuStats) | `d930cec50bfde460d1a6ebd9acef8b240a676000`；数据口径与公开快照入口 |
| SRC_ZSIM | [ZSim](https://github.com/ZSim-Dev/ZSim) | `e248e9f149a6b889290579d8e673e132be9bde31`；防御公式与可选完整引擎 |
| SRC_SHIELD | [社区机制导论，第 5.3 节](https://news.17173.com/content/07112025/114804086.shtml) | 社区条件推导的来源线索；不是官方参数或独立实测验证 |
| SRC_SURVIVAL | [lifelines 时变模型文档](https://lifelines.readthedocs.io/en/latest/Time%20varying%20survival%20regression.html) | 可选生存分析的数据格式与解释边界 |

代码、数据、公式来源分别登记。`source_manifest` 保存 URL、修订、获取时间、SHA256、文件大小、许可、覆盖、获取状态与验证说明。原始快照只读保存；外部更新建立新快照，不覆盖旧输入。

`parameter_registry` 对每个参数保存：`parameter_name, value_json, unit, source_kind, source_ref, applicability, rationale, sensitivity_set, status`。来源类型为 `source_code / observed / community_derived / scenario_assumed`。来源存在不等于数值在全部场景成立。

ShiyuStats 代码 MIT，保留版权与许可，分析发布时按 README 署名；数据许可按实际数据卡核对。ZSim 为 GPL-3.0，复制、修改与分发按其许可处理；完整引擎使用独立环境。数据准备可下载固定快照，日常计算与展示离线运行，不采集账号凭证。

### 1.2 ShiyuStats 的可用边界

当前公开 [hash.py](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/scripts/hash.py) 按采集期重新编号。公开 UID 仅限同一快照组内关联，不以同号推定跨期同一玩家。

现成 [角色统计](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/scripts/char_usage.py) 包含有效通关筛选；投资档位分类不等于实际付费身份。展示配装缺失不等于未持有。首版不计算无法证明完整的持有率。

[文件清单](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/data/repo_files.csv) 在本次检查时列至 3.2.3，这不证明每个战斗赛季均有文件。[采集期—战斗期映射](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/data/versions/config.json) 和 [评分配置](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/scripts/comp_rates_config.py) 必须入库：采集期不能直接当战斗版本，时间制与分数制不直接拼接。

## 2. 时间、身份与通用约定

### 2.1 时间协议

统一保存 `period_start_day, period_end_day, elapsed_days, evaluation_day`；有真实日历时另保存 ISO 日期和来源时区。`delta_span = elapsed_days / 42.0`，`cum_ver_span = (evaluation_day - origin_day) / 42.0`。42 天只是标准展示单位。

合成基线：10 个区间，t=1…10，起点 `42(t-1)`，终点与评估日 `42t`；没有真实日期和实际版本号。角色获取、抽取与 Pre/Post 评估发生在区间末端；订阅购买与权益到账按事件日期记录。疲劳用本区间末表现近似该区间感受，是离散行为假设。

旧角色在评估日前按实际已过天数养成，新获取角色从获取日开始计龄。变间隔实验同步改变资源累计、养成、失败持续量与流失暴露，不能只改横轴。真实时间轴跳过的区间不能被默认为没有抽取或没有资源；缺失供给事件时禁止称完整历史行为回放。

### 2.2 运行身份

- `experiment_id`：固定人群生成协议、时间轴、目标与指标的一组比较。
- `config_id`：规范化科学配置内容的 SHA256，含供给、战斗、行为、价格、概率曲线、输入摘要，以及 proxy/gacha/planner/behavior/rng 和配置规范化算法版本摘要；不含种子、策略显示名称、日志模式、输出目录与运行时间。改变决定结果的算法，必须生成新配置身份并使旧缓存失效。
- `replicate_id`：独立随机重复，绑定 `master_seed`；同一次重复中所有候选共享相同基础随机输入。
- `run_id`：逻辑运行，对应 `(experiment_id, config_id, replicate_id)`。
- `attempt_id`：同一逻辑运行的重跑尝试；重跑不能当成新的独立样本。
- `benchmark_id`：固定战斗模型、场景与队伍清单的摘要，可由多个行为运行引用。

规范化规则 `json_canonical_v1`：展开默认值和派生科学参数、按稳定ID排序清单、JSON键排序、UTF-8、无多余空白、禁止NaN/Infinity；不把相同科学参数的显示别名视为不同策略。浮点序列化与Python版本一起锁定。

一个输出目录只对应一个 run 或一项明确的跨 run 汇总。所有汇总保存输入 run 列表与摘要。

### 2.3 数值与缺失

金额用整数分；钱包用整数 `credit_minor`，160 单位可兑换一次抽取。角色影画为 0…6，武器数量为非负整数；本版不模拟精炼。概率在 [0,1]，支出与库存非负。

数值计算保存双精度、只在展示时四舍五入。NULL 表示未知/不可评估，0 表示已定义的零；不相互替代。缺失场景和空风险集不补 0。比较同值时按稳定 ID 排序，禁止依赖字典遍历或并发完成顺序。

资源来源消耗的“抽数”是 `消耗credit/160`，可为小数；实际抽取总数、直购抽数、pity与资产数量仍为整数。没有可行队伍且原因已知时可定义能力贡献为0并记 `no_feasible_team`；缺失模型、unsupported或计算错误的CI为NULL，默认停止依赖该结果的规划与报告，不把错误当作失败战力。

## 3. Layer 1：公开样本分析

### 3.1 接入流程

1. 下载固定数据修订并登记源文件；不能从不稳定的网页预览表直接推断全量 schema。
2. 建立字段映射：采集期、快照内匿名 ID、实际赛季、模式、关卡、队伍、角色、成绩、配装和有效通关标记。
3. 验证粒度、重复、同一期关联范围、资料覆盖及评分制度；无法识别的字段留空并记录。
4. 输出覆盖矩阵：每期文件数、原始/可用记录、独立样本人数、配装已知率、缺期及排除原因。
5. 在可比关卡、评分制度与投资档位内汇总；原始记录、排除记录和清洗规则均可追溯。

默认不沿用未经核验的 F2P/Whale 标签；用“无已知限定影画/有已知限定影画”“专武资料已知/未知”等可核对描述。缺失资料不参与零投资组。

### 3.2 核心指标

- `sample_players`：指定采集快照、赛季、模式和关卡范围内，至少有一条合格记录的唯一快照 ID 数。
- `appearance_player_share(u)`：合格样本中至少一次使用 u 的唯一人数 / sample_players；同一人多关出场去重。
- `appearance_per_clear(u)`：含 u 的合格队伍记录数 / 合格队伍记录数。与前项分开命名，不能平均或互换。
- 成绩：分别输出时间或分数的 n、中位数、P25/P75；算术平均为辅助项。样本不足 20 时保留原值并标 small_sample，不作主排名。
- 配队结构：合格队伍中的构成频率；公开重试次数不完整时不称玩家尝试偏好。
- 投资与支援关联：按资料覆盖筛选并披露，称关联比较，不称实际购买或支援因果收益。

已经筛选成功战斗的样本不报告“全部尝试者满星成功率”。评分制度变更两侧分面展示；不创造未经验证的跨制度换算。需要跨期图时，保留样本量和缺期，不将缺期连成连续观测。

### 3.3 Layer 1 与其他层的连接

只有角色 ID、模式、敌人及配置映射有依据时才建立连接。公开样本可以用于选择队伍、描述配装范围和检查代理结果是否明显不合理；不能据此识别支付意愿、疲劳权重或流失概率。校准与检查使用不同的赛季/队伍切片；同一数据拟合后回看一致不称独立验证。

## 4. Layer 2：参数化战斗代理

### 4.1 公式来源和输出定义

本模型只参考固定 ZSim 提交中的 [防御计算](https://github.com/ZSim-Dev/ZSim/blob/e248e9f149a6b889290579d8e673e132be9bde31/zsim/sim_progress/ScheduledEvent/Calculator.py#L815) 和 [敌人配置](https://github.com/ZSim-Dev/ZSim/blob/e248e9f149a6b889290579d8e673e132be9bde31/zsim/sim_progress/Enemy/__init__.py#L97)。K=794 为 60 级攻击方等级系数；952.8 仅作教学参考敌人的基础防御。贯穿分支跳过防御，不等于免除所有机制。

每个固定队伍模板保存 `B_ref`：在参考无盾敌人和标准时窗下的总伤害指数。它可以来自经登记的引擎基准，或明确的教学赋值，不能仅由 K 推导。参考 HP=100，标准时窗 T_limit=120 秒均为合成场景单位，不冒充真实关卡规则。

令：
- D 为本场景无盾基础防御；盾内防御增加比例 b。
- p 为穿透率，r 为百分比减防，f 为固定减防，z 为固定穿透。
- s∈{0,1} 为无盾/有盾。
- 减防和穿透参数满足已登记的有效域，本版默认均为 0。

\[
D^{eff}_s=\max\{0,\,[D(1+bs)(1-r)-f](1-p)-z\},\quad
m_s=K/(K+D^{eff}_s)
\]

设 m_ref 是该基础 build 在参考敌人上、未新增候选支援时的防御倍率；`pierce_share`=v 表示参考窗口中贯穿伤害占比。当前状态相对参考输出：

\[
Q_s=(1-v)m_s/m_{ref}+v
\]

这是固定伤害构成的代理；不模拟逐段属性与异常积蓄。不同角色可有不同 v；不因“虚狩”叙事标签自动获得固定减罚。

参数有效域：K、HP和m_ref>0，B_ref≥0；r、p、v、θ、d_shield和uptime均在[0,1]；κ>0；b、f、z及支援增益非负。默认κ仅使用三档，网格强度不越出有效域。任何输入为非有限数或引用缺失时验证失败，不在公式内部静默修复。

### 4.2 秽盾、爆发对齐与防止重复折损

本版教学假设 b=0.8、盾内额外减伤 d_shield=0.25。由社区条件推导：

\[
r_{normal}=
\frac{794/(794+1.8\cdot952.8)}{794/(794+952.8)}(1-0.25)
=0.5221518987
\]

0.5222 是上述条件下的合并相对保留率，包含防御提升；本实现采用分项计算，**不把 0.5222 再乘一次**。贯穿部分仍承受独立减伤；若完全贯穿且没有其他变化，相对保留率为 0.75。存在减防/穿透时按实际分项重算。

θ 是盾的时间覆盖率。κ 表示盾内/盾外未减伤潜在输出速率之比：

\[
q=\frac{\kappa\theta}{1-\theta+\kappa\theta}
\]

κ∈{0.7,1.0,1.3} 对应避盾、均匀、盾内集中。保持未减伤总伤害不变，仅重新分配时序；κ 不直接乘总 DPS。θ=0 时 q=0，θ=1 时 q=1。θ 路径、κ 档位及盾参数均为情景假设，不称引擎已实现的真实排轴。

### 4.3 装备、影画、专武与支援

角色 u 在日 d 的养成倍率：

\[
G_u(d)=1+0.16\{1-\exp[-\max(0,d-d_{acquire})/84]\}
\]

范围 [1,1.16]，新角色为 1；84 天时间常数和 1.16 上限均为教学假设。此倍率只用于代理模型；完整 ZSim 适配器使用实际装备参数，不叠加该倍率。装备可跨角色预先准备等行为暂不模拟，作为局限披露。

教学影画倍率 `C(0)=1, C(1)=1.12, C(2)=1.25`；示例只抽到 2 影。对应专武倍率 `W=1.15`，未持有为 1。均是代理假设，不代表真实角色。

队伍为一个锚定主输出、一个固定中性辅助、一个可替换位置。可替换位置为中性角色或一个支援；不叠加两个支援。初始中性角色始终可用。主输出不能被另一个更强主输出“带过关”来伪造自身适用性。

支援 S_A 的伤害增益 g=0.35，S_B 为 0.45；支援缩放 a 作用于增益：`support_factor=1+a*g`，不是把整体倍率再乘 a。本版支援默认不额外减防；若启用减防支援，必须改变对应参数，并避免在总增伤中重复计算。

### 4.4 有效伤害与通关代理

\[
D_{window}=B_{ref}\,G_u\,C(c)\,W\,support\_factor\,
uptime_t\{(1-q)Q_0+q(1-d_{shield})Q_1\}
\]

`uptime_t` 是独立的可攻击窗口代理，不重复包含盾的防御和减伤。本版不另设失衡增伤；如将失衡锁定近似放入 uptime，必须注明，不再新增同一损失的惩罚。

\[
ClearIndex=D_{window}/HP_t
\]

`CI≥1` 定义为**模型达标**，展示可注明“模拟满星代理”，不能宣称真实多队满星。`0.95` 仅为舒适偏好线；不改变达标判定。没有强制“退环境=0.85”标签，默认输出连续缺口 `gap=max(0,1-CI)`。

公式只定义固定窗口伤害。不得据此假造首杀时间。可选完整 ZSim 运行单独输出 first_kill_seconds 与 time_limit/first_kill_seconds；未击杀保留超时/删失，不用伤害比例反推真实击杀时刻。

### 4.5 固定面板、配对和完整引擎检查

预注册每个锚定输出的 0+0、0+1、1+0、1+1、2+0、2+1 配置；支援 None/S_A/S_B，κ 三档。分别记录固定装备 G=1 与明确给定的养成路径，分组标签在实验开始前固定。

配对时只改变指定项，其余队伍、日期、HP、配装、动作假设和预算定义保持不变。支援本身有获取成本：纯机制对照可只改变支援，但须标“增加支援资产”；等成本比较需配置明确的替代投资，不能冒称已经等成本。

所有基准由 `fact_fixed_benchmark_panel` 保存；低于达标线如实记录。只有少量完全相同模板，没有有效异质性时，不为画平滑曲线复制成成千独立样本。

可选 ZSim 检查只选已确认支持的队伍，保存提交、角色/敌人数据、APL、完整配置和输出。对不支持的盾机制不假装已验证；比较适用场景中的输出比例及排序差异。可接受误差在运行前登记，超差先查原因，不调到预期结论。


## 5. 可运行的合成基线配置

所有本节数值均登记为 `scenario_assumed`，用于实现验证，不能写入“真实版本实证”。修改任何值均生成新 config_id。

### 5.1 人群与初始库存

N_PLAYERS=3000，10 个区间；每个 replicate 生成同一批初始玩家，所有候选复用。

| player_tier | 占比 / 人数 | 纯免费抽/42天 | 额外直购预算上限，抽/42天 | 教学订阅 | α_tier | h 初值 |
| --- | --- | --- | --- | --- | --- | --- |
| Budget_0 | 40% / 1200 | 85 | 0 | 无 | -3.0 | 0.50 |
| Budget_40 | 40% / 1200 | 85 | 40 | 有 | -2.5 | 0.40 |
| Budget_160 | 15% / 450 | 85 | 160 | 有 | -2.0 | 0.30 |
| Budget_500 | 5% / 150 | 85 | 500 | 有 | -1.0 | 0.05 |

这是模拟预算层，不能与公开样本的实际付费身份对应。核心留存主指标固定使用初始 Budget_40 队列，分母始终 1200。

所有人初始持有 starter_dps（B_ref=110、v=0、影画0、无专武）及中性配队角色neutral_stun、neutral_flex（role=neutral，不单独贡献代理伤害）；获取日=0、初始G=1。钱包=0、两池pity=0、guaranteed=false，F=0、失败持续量=0、活跃=true。固定人群不补新客、不回流。

订阅教学套餐：每 42 天购买一次，现金 98 元、购买时立即发放 40 抽等价 credit。它不是实际月卡或城募的还原；订阅支出不占额外直购额度。逐抽直购统一教学价 16 元/抽，无首充、折扣、平台分成或税费模型；结果为模拟现金收入，不是利润。

### 5.2 场景、供给与产品强度

基础无盾防御 D=952.8，各期相同；不根据卡池或角色上市自动赋予免罚。参考环境：

| t_idx | period_id | 供给 | 角色ID / 类型 | θ_reference | uptime |
| --- | --- | --- | --- | --- | --- |
| 1 | P01 | 新主输出 | DPS_01 / normal | 0.00 | 1.00 |
| 2 | P02 | 新主输出 | DPS_02 / normal | 0.05 | 0.98 |
| 3 | P03 | 新主输出 | DPS_03 / piercing | 0.10 | 0.95 |
| 4 | P04 | 新支援 | S_A / support | 0.15 | 0.95 |
| 5 | P05 | 新主输出 | DPS_05 / piercing | 0.45 | 0.90 |
| 6 | P06 | 新主输出 | DPS_06 / normal | 0.40 | 0.90 |
| 7 | P07 | 新支援 | S_B / support | 0.45 | 0.90 |
| 8 | P08 | 新主输出 | DPS_08 / piercing | 0.40 | 0.88 |
| 9 | P09 | 新主输出 | DPS_09 / normal | 0.45 | 0.88 |
| 10 | P10 | 新主输出 | DPS_10 / normal | 0.40 | 0.88 |

normal 的 v=0，piercing 的 v=1；以后允许混合伤害，但必须写明参考占比。新主输出统一 `B_ref=125*1.12^(debut_t-1)`，所有策略采用同一商品表；不根据各策略 HP 反向生成新品强度。支援的 B_ref=NULL，由实际搭配体现收益。

DPS 默认目标：Budget_0/40 为 0+1，Budget_160/500 为 2+1；支援只抽本体，不抽影画或专武。目标只是愿意尝试时的停止目标，不保证成功。已有目标部件跳过；复刻需另有 banner 事件，角色不在当前池时不能自动继续上期未完成目标。

每期默认一个 banner；接口支持明确顺序的多个 banner，事件 ID 在配置之间保持相同。主输出/支援分类决定候选资产，是否下池一律走相同期望效用规则，不规定某类角色必抽。

### 5.3 三个命名参照情景

| strategy_id | HP_t | θ_t | 盾内额外防御 b / 减伤 d | 支援增益缩放 a |
| --- | --- | --- | --- | --- |
| Ref_HP | 100*1.18^(t-1) | 各期 0.10 | 0.8 / 0.25 | 1.00 |
| Ref_Mechanism | 100*1.12^(t-1) | 上表路径 | 0.8 / 0.25 | 1.00 |
| Ref_Support | 100*1.12^(t-1) | 上表路径*0.75 | 0.8 / 0.25 | 1.15 |

三者为组合情景，不用它们的差值单独归因于某一参数。uptime、商品、价格、初始玩家及时间轴一致。κ 默认 1，L2 必须同时展示 0.7/1/1.3。无预设优胜顺序。

## 6. 抽卡状态机

### 6.1 显式经验概率曲线

`pity`=当前连续未出 S 的抽数，下一抽 `n=pity+1`。本版曲线名 `assumed_empirical_v1`；完整曲线包括基础值与坡度均作为已登记的经验情景假设，未核验为官方逐抽表：

\[
p_c(n)=
\begin{cases}
0.006,&1\le n\le73\\
\min(1,0.006+0.06(n-73)),&74\le n\le89\\
1,&n=90
\end{cases}
\quad
p_w(n)=
\begin{cases}
0.010,&1\le n\le64\\
\min(1,0.010+0.06(n-64)),&65\le n\le79\\
1,&n=80
\end{cases}
\]

角色池目标条件概率 0.5，音擎池 0.75；保证状态为真时均为 1。限定在常规限定角色/音擎频段的抽象规则；特殊重映、福利保底、定制、兑换及返还货币不纳入本版。

每名玩家保存角色池 `(char_pity∈0…89,char_guaranteed)` 与音擎池 `(wep_pity∈0…79,wep_guaranteed)`。保底共享由 `pity_group` 显式指定，同一类组跨 banner 继承；角色与音擎不共享。

### 6.2 每次实际抽取的转移

1. 从第 7 节规则支付一抽；无法支付则停止，不读取未来抽次来决定充值。
2. 按该池 n 对应概率与固定随机槽判断是否出 S。
3. 非 S：pity 加 1，guaranteed 不变。
4. 出 S：pity 重置为 0；若原 guaranteed=true，则获得当前 UP；否则按本池条件概率判断。
5. 获得 UP：guaranteed=false，资产立即入库；非 UP：guaranteed=true，记录非 UP 资产。
6. 检查当前目标阶段是否完成，更新后继续下一阶段，直至目标完成或资源/额外预算耗尽。

不出 S 的极端路径必须在硬保底点终止，因此不会产生非法 pity=90/80。零抽时两池状态不变化。保证状态不意味着每抽必出 S，只意味着下一次 S 必为目标。

本版非S统一记录为NON_S，不进一步抽样区分A/B，也不模拟A级保底；非UP的S按角色池/音擎池记录为OFFRATE_S_CHAR/OFFRATE_S_WEAPON非战斗库存类别，不展开其战斗与返还收益。类别ID固定并累计实际结果数量。这是有损简化，不能声称完整抽卡经济还原。后续扩展真实歪卡roster时同步修改规划DP与实际抽取，避免两者不一致。

### 6.3 目标顺序与部分获取

- 0+0：本体。
- 0+1：本体 → 专武。
- 2+1：本体 → 专武 → 1影 → 2影。
- 支援：只抽本体。

逐步检查已持有部件并跳过；不能重复支付已完成目标。预算耗尽前获得的本体、影画、专武全部保留并参与当期 PostEval。未完成目标不等于零出货。

从全新保底出发，标准抽象规则下：本体最多180抽、专武最多160抽，0+1最多340抽、2+1最多700抽；实际剩余上界依初始状态和已有部件缩短。均值由所选曲线计算，不强制为93.5/148/335。

独立DP复算的校验参考：本节经验曲线在零水位、无保证状态下，目标角色均值约93.445998抽、目标专武约62.366232抽；0+1约155.812230抽，2+1约342.704226抽。它们是当前假设曲线的数学结果，不是真实抽卡均值；改变曲线版本后必须重算，而非反向调整曲线迎合这些数。

## 7. 钱包、资源与现金账

### 7.1 来源账户与消耗顺序

永久来源为 `free / subscription / topup`；free 再按 `earned_period` 区分本期与历史。当前免费与历史免费是同一来源的不同批次，不重复记为两份资产。

固定消耗顺序：历史免费 FIFO → 本期免费 → 订阅权益 FIFO → 按需逐抽直购。来源顺序影响占比归属，必须登记。订阅未消耗权益滚存，不能混入免费库存。

每抽扣160 credit；现金以分记录。直购在所有可用库存耗尽后，只购买当前这一抽并立刻使用，不存在待用直购余额。若以后允许礼包预充值，需新增余额与购买事件，不能沿用本版零余额假设。

更精确地说，已有免费与订阅credit合计至少160时，可按上述来源顺序分摊支付同一抽，记录多条整数credit扣账。合计不足160时保留零碎余额，若允许直购则整抽购买160并整抽使用，不扣这些零碎余额；此处“库存耗尽”指不足支付一整抽。四来源等价抽数可为小数，但合计必须为整数总抽数。

输出四类抽数：`curr_free_used, hoarded_free_used, sub_pulls_used, topup_pulls_used`；它们之和等于实际总抽数。所有来源消费均保留批次记录。

### 7.2 发放、订阅与预算

纯免费按活跃天数累计。合成默认每日速率为13600/42 credit；区间到账量为 `floor(13600*end_day/42)-floor(13600*start_day/42)`，避免分数抽在多区间重复四舍五入。只在本期开始活跃时发放，实际发放事件时间不晚于本期评估。

订阅每42天续购，事件归属区间 `[start_day,end_day)`，购买时到账6400 credit；本期末流失在下一期续购判断前生效。没有领取、未购买或已经流失的日期不凭空发放资源。付过的款不自动退款。

额外直购额度按每42天上限 B_tier 缩放：
`paid_cap_t=floor(B_tier*end_day/42)-floor(B_tier*start_day/42)`。
同一期所有 banner 共用额度；未用额度不滚存。订阅费用另计，不从该额度扣除。

### 7.3 现金收入与守恒

\[
revenue_{cny}=sub\_revenue_{cny}+topup\_revenue_{cny}
\]
\[
topup\_revenue_{cny}=16\cdot topup\_pulls\_used
\]

现金按购买发生期记录。消耗上期订阅库存不会在当期再产生一次订阅收入。抽数占比、金额占比与时间口径分别标注；历史免费抽消耗不计收入，也不从本期现金收入中再次扣减。

每个来源/批次验证：期初余额 + 本期到账 − 本期消耗 = 期末余额。总金额从 cash_event 聚合，不从角色面板反推。流失后余额保留但不再领取、消费、续订或退款。

## 8. 行为决策、六步时序与流失

### 8.1 每期执行顺序

1. 固定期初活跃状态 `active_start`，推进日期；对活跃旧角色更新养成，按事件发放资源并记录订阅现金。
2. PreEval：基于实际库存，求各已持有锚定主输出的最佳可行队伍，再选全库存最佳 CI。
3. 按 banner 顺序做一次下池决策；决定尝试后执行逐抽过程，可停在任一部分阶段。
4. 全部实际成果即时入库，更新钱包与保底；下一 banner 使用更新后的库存和本期剩余额度。
5. PostEval：重评所有持有主输出，保存最佳队伍与实际部署。
6. 仅更新一次囤卡记忆、疲劳、失败持续量，判断本期末流失，再保存期末与冻结快照。

本期末流失者本期仍参与并可能产生现金，下一期不再活跃。禁止用期末流失状态回填本期的实际参与分母。前一时期已流失者不执行1–5，只保留余额与反事实冻结评估。

### 8.2 平滑收益与购买概率

`CI0` 为当前库存最佳表现，不是待售单卡的表现。令：

\[
s_\tau(x)=1/(1+e^{-x/\tau}),\ \tau=0.03,\quad
G(c)=\beta_g c+\beta_s s_\tau(c-0.95)
\]

规划必须覆盖预算截断后的部分获取。设 K 为完成的目标阶段数、`CI_K` 为对应库存最佳表现：

\[
U_i=\alpha_{tier}-\beta_h h_i+
\mathbb E[G(CI_K)]-G(CI_0)
-\beta_r\frac{\mathbb E[N_{spent}]}{B}
-\beta_p\frac{\mathbb E[N_{topup}]}{\max(P,1)}
\]
\[
p_{enter}=\sigma(U_i)=1/(1+e^{-U_i})
\]

F=`floor((可花免费credit+可花订阅credit)/160)`，P为本期剩余直购抽数额度，H为当前剩余目标的最坏所需抽数，整数预算B=`min(F+P,H)`。B=0或目标全部持有时直接跳过，不计算购买概率。P=0时E[N_topup]=0。结果不必达到1.0才有收益；0.95只是平滑偏好中心。

教学默认：β_g=4、β_s=2、β_h=1、β_r=1、β_p=2，α与h初值见5.1。不存在“零收益默认50%”的隐藏假设；剩余少量购买概率来自显式偏好截距。拒绝只对当前事件生效，不形成永久不抽状态。

### 8.3 决策期望的确定性计算

禁止读取实际抽取 CRN 推知未来出货成本。对每种 `(概率曲线版本,pity,guaranteed,pool_kind)`，以有限状态 DP 预计算“下一目标 UP 所需抽数”的离散分布。每池首次访问未完成阶段使用其当前真实状态；获得目标UP后该池回到 pity=0、guaranteed=false，后续同池阶段使用此重置状态，不能反复套入最初的垫抽。不同池基础随机流独立。

依目标路径卷积得到前缀成本 C_j。预算上限 B 下：
`P(K=j)=P(C_j≤B)-P(C_(j+1)≤B)`，末阶段为 `P(C_L≤B)`，C_0=0。
`E[N_spent]=sum(n=0…B-1) P(C_L>n)`。
F为8.2定义的现有整数可花抽数时：
`E[N_topup]=sum(n=F…B-1) P(C_L>n)`，空和为0。

CI_K 使用当前日、实际既有库存及各阶段潜在资产计算；新角色 G=1。由于本版歪卡不参与战斗，阶段分布足以描述规划战斗结果；扩展歪卡能力时必须扩展状态。

缓存概率分布、前缀CDF和预算截断结果；战斗部分按持有离散配置查表或向量计算。采用有损近似时必须另标 approximation，并报告与精确DP的误差，不能悄悄换成均值成本。

### 8.4 囤卡记忆、疲劳与随机流失

令 d=elapsed_days/42，g=max(0,1−CI_post)。失败持续量 `s'=0` 若达标，否则 `s'=s+d`。预算使用率 `b=topup_pulls_used/paid_cap_t`，额度0时b=0。

\[
h'=\operatorname{clip}(0.85^d h+0.25dg-0.10dI(CI_{post}\ge1),0,1)
\]
\[
F'=\operatorname{clip}\{e^{-0.25d}F+
d[0.8g+0.15\min(s',3)+0.2b-0.3I(CI_{post}\ge1)],0,5\}
\]
\[
p_{churn}=1-\exp[-0.0025d\exp(0.8F')]
\]

用独立流失随机槽判定。没有策略标签、Boss机制系数或“虚狩持有”直接进入疲劳/流失公式；影响通过结果和投入传导。相同结果同权只是中立基准，不是真实心理规律。h可恢复，疲劳不为负，策略A未达标也可积累疲劳。

所有系数均为假设。低于门槛不是确定退坑；满星也有很小基线流失。公开数据不用于伪称已估计这些参数。

## 9. CRN：事件索引公共随机数

统一主键使用稳定字符串/整数编码，不使用进程随机化的内置 hash：

- 人群静态属性：`(master_seed,player_id,attribute_type)`。
- 下池决策：`(master_seed,player_id,t_idx,banner_id,decision_slot,"decision")`。
- 抽取：`(master_seed,player_id,t_idx,banner_id,pool_kind,draw_slot,rng_type)`，其中rng_type区分S判定与UP判定。
- 流失：`(master_seed,player_id,t_idx,"churn")`。

config_id/strategy_id 不进入公共随机键。角色与音擎独立流；即便保证命中无需使用UP随机值，也不把后续槽位前移。同一期多banner有独立稳定ID。draw_slot为本事件内该池第几次抽取，不是pity；切换目标影画阶段不重置同池draw_slot。

默认 `hash_counter_v1`：以域标签 `zzz_research_rng_v1` 和规范化JSON数组形式的完整事件键组成UTF-8字节串，计算SHA256，取摘要前8字节的大端无符号整数x，令U=`(x >> 11)/2^53`。U在[0,1)，按 `U < p` 判定，因此p=1必定命中。预生成/缓存张量只改变计算时机，不改变事件映射；优化更换PRNG时建立新rng_version和config_id。资源/结果分支不得决定后续随机值的身份。

容量上界由已有资源、剩余预算和剩余目标最坏保底抽数共同确定。极端囤抽不能造成静默截断；需要扩容时按同一索引补块，不改变原槽值。改变任务执行顺序、线程数或块大小，同一配置和种子的结果必须一致。

同样seed只保证对应事件受到同一基础随机冲击，不保证不同状态的玩家产生同样结果；不作“不同动作全都严格等价”的额外主张。


## 10. SQLite 数据契约

数据库建议名 `zzz_research.db`。开启 foreign_keys，批量事务写入；索引按查询需要建立，不用逐行commit。观测、基准和行为数据分表，禁止以相同player_id暗示跨层对应。

字段约定：ID为TEXT；t/计数/布尔为INTEGER；金额为INTEGER分；日期为ISO TEXT；天数和指数为REAL；结构化配置为JSON TEXT。布尔 CHECK IN(0,1)，概率 CHECK在[0,1]。所有PK字段NOT NULL；无支援的已知状态使用维度值 `NONE`，未知资料用NULL。

下表列出完整的逻辑字段组；字段组展开后进入迁移文件，不能以一个不透明JSON代替关键分析列。JSON仅用于原配置、可变元数据或冻结状态。

### 10.1 来源、维度与运行表

| 表 | 粒度 / 主键 | 必需字段 |
| --- | --- | --- |
| source_manifest | 一个源文件快照；source_file_id | source_id,url,revision,retrieved_at,sha256,byte_size,license,coverage_json,status |
| parameter_registry | 一项配置参数；(config_id,parameter_name) | value_json,unit,source_kind,source_ref,applicability,rationale,sensitivity_json,status |
| run_manifest | 一个逻辑运行；run_id；唯一(experiment_id,config_id,replicate_id) | master_seed,rng_version,algorithm_versions_json,config_json,input_manifest_hash,code_revision,environment_json,status |
| run_attempt | 一次实际执行；(run_id,attempt_id) | log_mode,started_at,ended_at,status,error_code,output_hash |
| stage_timing | 一阶段耗时；(run_id,attempt_id,stage) | wall_seconds,row_count,peak_memory_mb,measurement_method |
| dim_period | 一个时间区间；(timeline_id,period_id) | t_idx,start_day,end_day,evaluation_day,elapsed_days,actual_start_date,actual_end_date,calendar_mode |
| dim_unit | 一个商品角色；(catalog_id,unit_id) | role,debut_period,debut_day,B_ref,pierce_share,weapon_id,base_support_gain,combat_eligible |
| dim_banner | 一个稳定供给事件；(catalog_id,banner_id) | period_id,event_order,target_unit_id,target_weapon_id,char_pity_group,wep_pity_group,allowed_target_json |
| dim_combat_scenario | 一个时期的战斗配置；scenario_id | combat_config_id,period_id,hp,base_def,shield_def_bonus,shield_dr,theta,kappa,uptime,support_gain_scale,source_kind |
| dim_reference_subject | 一条预注册参考轨迹；reference_subject_id | benchmark_id,anchor_unit_id,investment_plan,gear_policy,support_policy,origin_day,budget_description |
| dim_team_snapshot | 一份不可变实际队伍配置；team_id | anchor_unit_id,neutral_unit_id,support_id,component_builds_json,build_hash；team_id为规范化配置摘要 |
| dim_search_candidate | 一个网格位置；(experiment_id,candidate_id) | lambda_value,support_gain_scale |
| dim_perturbation | 一组假设扰动；(experiment_id,perturbation_id) | override_json,label；基线固定为P0 |
| search_run_map | 一个候选/扰动/重复的运行映射；(experiment_id,candidate_id,perturbation_id,split,replicate_id) | config_id,run_id |

角色与武器资产ID使用不同前缀；catalog_id与timeline_id都进入config。reference_subject固定的是干预/配置方案，不能在结果出来后按“最终有支援”重新分组。

candidate_id只表示固定(λ,a)网格位置；扰动改变config_id但不改变candidate_id。映射与汇总必须同时保留这两个身份，不能按config_id误把一次扰动当作新的网格位置。

### 10.2 公开样本表

| 表 | 粒度 / 主键 | 必需字段 |
| --- | --- | --- |
| obs_battle | 一条源战斗记录；battle_id | source_file_id,source_row_locator,snapshot_id,local_uid,collection_period,combat_season,mode,stage_id,scoring_regime,score_value,clear_time_seconds,valid_clear,is_eligible,exclusion_reason |
| obs_team_member | 一个队伍槽位；(battle_id,team_slot) | unit_id,known_cinema,known_weapon,build_record_id,build_missing_reason |
| obs_build | 一份展示配装；build_record_id | source_file_id,snapshot_id,local_uid,unit_id,build_json,coverage_status |
| obs_coverage | 一期/模式/关卡覆盖；(source_snapshot_id,combat_season,mode,stage_id) | raw_records,eligible_records,unique_players,known_build_players,scoring_regime,status |

raw来源没有稳定battle_id时，用源文件摘要+原行定位生成，不将重复上报自动当重试样本。不同快照重叠的无法去重记录要另标，不盲目合并分母。

### 10.3 固定基准与模拟明细

| 表 | 粒度 / 主键 | 必需字段 |
| --- | --- | --- |
| fact_fixed_benchmark_panel | 一参考主体一期一场景；(benchmark_id,reference_subject_id,period_id,scenario_id) | engine_kind,anchor_unit_id,team_id,build_hash,support_id,cinema,has_weapon,disc_mult,B_ref,shield_damage_share,effective_damage,clear_index,is_full_star,eval_status,first_kill_seconds,coverage_status |
| fact_asset_event | 一次资产变动；(run_id,asset_event_id) | player_id,period_id,event_day,banner_id,pull_event_id,asset_id,event_kind,copies_delta |
| fact_ownership | 一玩家一期末一资产；(run_id,player_id,t_idx,asset_id) | first_acquired_day,copies,cinema,has_weapon,disc_actual,is_combat_eligible |
| fact_player_roster_panel | 一玩家一期一主输出一评估阶段；(run_id,player_id,t_idx,unit_id,eval_phase) | scenario_id,team_id,support_id,cinema,has_weapon,disc_actual,build_hash,clear_index,is_full_star,is_deployed_main,has_active_support,active_start,eval_status |
| fact_decision_log | 一次下池决策；(run_id,player_id,t_idx,banner_id,decision_slot) | target_path,CI_pre,available_pulls,remaining_paid_cap,expected_gain,expected_pulls,expected_topup_pulls,utility,p_enter,did_enter,skip_reason,planner_version |
| fact_pull_event | 一次实际抽取；(run_id,player_id,t_idx,banner_id,pool_kind,draw_slot) | pull_event_id,pity_group,target_stage,p_S,is_S,is_UP,result_asset_id,pity_before,pity_after,guaranteed_before,guaranteed_after,paid_cash_event_id |
| fact_resource_event | 一次钱包记账；(run_id,resource_event_id) | player_id,t_idx,event_day,provenance,earned_period,event_kind,delta_minor,pull_event_id,cash_event_id |
| fact_wallet_snapshot | 一来源批次余额；(run_id,player_id,t_idx,phase,provenance,earned_period) | balance_minor |
| fact_cash_event | 一次现金购买；(run_id,cash_event_id) | player_id,t_idx,event_day,purchase_kind,amount_fen,granted_credit_minor,pull_event_id |
| fact_pity_snapshot | 一共享保底组快照；(run_id,player_id,t_idx,pity_group,phase) | pity,guaranteed |
| fact_player_lifecycle | 一玩家一期；(run_id,player_id,t_idx) | player_tier,active_start,active_end,churn_this_period,CI_pre,CI_post,is_full_star_post,h_before,h_after,F_before,F_after,failure_span,paid_cap,paid_used,budget_utilization_rate,p_churn |
| fact_frozen_state | 一玩家退出时冻结；(run_id,player_id) | churn_period,freeze_day,freeze_build_hash,frozen_state_json |
| fact_unit_cohort | 首次获取队列成员；(run_id,unit_id,acquisition_period,player_id) | acquisition_day,cohort_id |
| agg_player_period | 一玩家一期对账宽表；(run_id,player_id,t_idx) | player_tier,active_start,active_end,CI_pre,CI_post,sub_revenue_fen,topup_revenue_fen,total_pulls,curr_free_used,hoarded_free_used,sub_pulls_used,topup_pulls_used,ending_free_minor,ending_sub_minor |

eval_phase枚举 `pre / post / frozen_post`。每期对所有已持有主输出计算，不只写出战者；支援在ownership及队伍字段体现，不伪造支援单独通关指数。`is_deployed_main`只对实际全库存最佳主输出为1。

活跃玩家的pre只含决策前持有角色，新出货出现在post。非活跃持有者的post保留CI=NULL与eval_status=inactive；冻结情景另写frozen_post，不覆盖实际状态。计算失败记error或unsupported，不悄悄当低战力。

fact_pull_event另设 `UNIQUE(run_id,pull_event_id)`；资产、资源、现金表中的抽取引用统一使用这两个字段作为外键。其他事件同理以(run_id,event_id)引用，不能只用局部事件号。所有带run_id的表引用run_manifest；观测表的source_file_id引用source_manifest。

每个主输出锚定后分别求最佳可行支援搭配；这些最优队伍是潜在表现，不能声称可在多关同时复用同一支援。

### 10.4 汇总表

- `agg_obs_character_period`：快照/实际赛季/模式/关卡/投资档位/角色，保存分子分母和成绩分布。
- `agg_benchmark_pairs`：预注册配对ID、时期、改变因素、基线CI、干预CI、绝对/相对差值与成本差。
- `agg_unit_cohort_availability`：run、角色、首次获取期、评估期的三口径率、分母、退出占比、新增获取数。
- `agg_revenue_structure`：run、时期、banner的直购现金与四来源抽数；订阅现金单列时期项，不强行归属一个banner。
- `agg_cohort_cash_retention`：run、初始预算层、时期，期初/期末留存、固定分母累计人均现金、库存和消费。
- `agg_search_results`：主键(experiment_id,candidate_id,perturbation_id,split,replicate_id)，保留config_id/run_id、两目标及相对基准差值。
- `agg_seed_robustness`：候选/扰动/split粒度的均值、标准差、范围和非支配入选频率。
- `agg_rank_stability`：experiment/perturbation/比较协议粒度的rho_rev、rho_ret；相关系数基于整个25点集合，不能当候选自身的统计。
- `agg_parameter_sensitivity`：痛感等扰动、稳定candidate_id、复核目标及相对变化；不与随机误差混表。

现金、抽取、生命周期分别先汇总到(run,player,t)，再关联。禁止直接把现金表与多行角色面板关联后SUM。若按角色归因现金，另建权重表且权重和为1；默认不做这种归因。

SQL可在上述粒度使用CTE、LAG和累计SUM/滚动AVG；所有窗口PARTITION必须含run与实际业务分组，不能跨种子、跨配置累计。增长率基数为0时记NULL并给原因，不用无穷大制造增长。

## 11. 统计方案与指标分母

### 11.1 固定获取队列的三个可用率

每个run内，对角色u的首次获取期g固定成员集合C，N=|C|；不加入未来获取者。各配置的实际获取队列可能不同，因此队列间差异只作描述；策略总留存仍用相同的初始人群比较。

A(p,t)=本期开始活跃；Y(p,u,t)=本期PostEval的模型达标标记。流失后实际Y未知，不直接记战力失败。

\[
avail\_rate\_active=
\frac{\sum_C A\,Y}{\sum_C A}
\]
\[
cohort\_active\_clear\_share=
\frac{\sum_C A\,Y}{N}
\]
\[
avail\_rate\_counterfactual\_frozen=
\frac{\sum_C[A\,Y+(1-A)Y^{F}]}{N}
\]
\[
churned\_holder\_share=\frac{\sum_C(1-A)}{N}
\]

Y^F是在退坑最后PostEval时冻结Box、影数、专武、支援、装备及实际Disc值，停止养成与获取，仅更新敌人环境得到的达标情况。它是“停止投入的配置情景”，不是未退坑预测。

第二项不叫ITT；流失贡献0表示退出生态，并非战斗失败。当活跃分母为0时active率=NULL；若N>0且全部退出，第二项为0。已定义且无缺失时应满足：
`cohort_active_clear_share = avail_rate_active*(1-churned_holder_share)`。
有error/unsupported记录时单列unknown_share；任何比率的分母成员中若存在该口径所需的未知结局，该比率为NULL而非悄悄把未知当0或从分母删除。默认正式行为run遇到此类错误即失败；仅诊断报告可展示已知部分及未知占比。

双/三率用于解释样本构成，不能单独证明支援造成真实反弹。额外给出新增获取数、同一获取队列和相同场景的L2配对结果。

### 11.2 固定基准首次观测失效与动态可用率

KM默认只用于L2预注册参考轨迹。观测单位为reference_subject，不把相同轨迹复制成许多玩家增加“证据量”。基线分组为预先确定的投资/支援方案：从不支援，或按预定可用日加入支援等；不能按最终成功购得支援重分组。

- 时间原点=参考角色出道日；横轴age_days，辅助显示age_days/42。
- 事件=首次评估CI<1，包括首次观测即失败（age=0的事件，按实际评估时点记录）。
- 首次事件后退出风险集；后续恢复仍留在动态可用率，但不撤销事件。
- 观察结束仍未失败，在最后实际评估日右删失；新出道角色随访短，不补未来。
- 中位时间是首个S(t)≤0.5的观测时点；全程>0.5才记MedianNotReached。
- 不对离散曲线插值制造1.5版本寿命；仅声称“首次观测失效”，不识别缺失节点间的精确时刻。

默认KM不包含初始starter；新供给角色在出道评估日有age=0观测。若扩展主体首次评估晚于时间原点，应保存entry_age_days并按延迟入组处理，或单独展示，不假定此前一直成功。

L2固定轨迹是有限情景清单，KM主要为描述性汇总，不把常规个体独立置信区间解释为真实游戏不确定性。轨迹过少时直接展示逐轨迹矩阵与事件时间，不强制KM。

玩家行为面板可另输出首次失效，但受获取、支援和流失选择影响，不与固定基准寿命混合。公开Shiyu样本未经跨期ID验证，不生成此类玩家KM。

### 11.3 可选时变Cox

仅作探索性关联，不作为支援因果延寿证明。输入至少：
`subject_id,start_day,stop_day,event_fail,has_support_t,unit_age,investment_t,tier`。
主体为玩家×锚定角色；区间不重叠，首次事件后停止贡献风险时间。

支援实际获得之后才记暴露；不能把期末支援回填整个此前区间。只有版本快照时，使用上一期状态解释本期失效，明确为滞后关联；不把同一时点PostEval与刚购买支援伪造为连续时间已识别效应。

记录事件数、暴露变异、收敛、比例风险/模型适用检查及重复主体依赖。事件不足、完全分离、无暴露变化或不收敛时跳过并记录原因。购买内生性与信息性流失仍是限制；不为满足图表要求强行拟合。

### 11.4 留存与现金指标

- `retention_start(t)=active_start人数/初始人数`，`retention_end(t)=active_end人数/初始人数`，不得混名。
- 累计人均现金=截至t全部订阅及直购现金/初始队列人数，不除以幸存者人数。
- 主商业图：全观察期模拟累计现金 vs 期末Budget_40模拟留存率。
- 免费/订阅/直购消耗占比的分母为总消耗抽数；分母0时NULL。
- 历史免费占比不是总免费占比；库存下降不是当期现金减少的独立因果证明。

所有Layer3图标题或相邻说明包含“模拟”“假设情景”；未知真实价格/行为不被小数位精度掩盖。

## 12. 参数搜索与稳健性

### 12.1 25个候选配置

在Ref_Mechanism的HP、商品、θ路径、uptime、价格与时间轴上固定其他条件，扫描：

- 机制强度 λ ∈ {0,0.5,1.0,1.5,2.0}；
- 支援增益缩放 a ∈ {0.8,1.0,1.2,1.4,1.6}。

设置 `shield_def_bonus=0.8λ`，`shield_dr=0.25λ`，θ不变；λ轴是打包机制干预，不分别识别防御与减伤效应。两者仍按分项公式计算，不固定套0.5222。a只缩放支援的额外增益。默认κ=1，价格和商品表完全一致。

三个命名参照情景另作参照；前沿标题明确是这25个网格候选，不把不同集合悄悄混入。κ的3档首先用于L2；如扩展至行为搜索，单列结构敏感性实验。

### 12.2 筛选与独立复核

- 筛选种子：[1101,1102,1103,1104,1105]。
- 独立复核种子：[9101,9102,9103]。
- 先冻结配置与指标，再跑5组；3组复核不用于重新调参数。使用复核调参后必须另建实验与新复核种子。
- 每个split均完整运行25点；每个候选从相同初始人群和各自完整路径重跑，不复用其他配置的抽取与流失决策。

每点保存收入与留存的均值、标准差、最小/最大及相对同扰动、同seed下(λ=1,a=1)基准的配对差。3组复核只是初步稳定性检查，不宣称足够确定总体最佳。Ref_Mechanism与该基线是同一科学配置，可复用run，只保留不同显示标签。

### 12.3 非支配集合与两个秩相关

候选j支配i，当两个目标均不差且至少一个严格更好。按未四舍五入的结果计算；收入使用整数分、留存使用人数比。数值完全相同的候选均保留，不任意只留一个。

每种子独立计算非支配集合。`inclusion_frequency_validation=复核入选次数/3`，只能描述这3次结果，不称真实最优概率。

分别计算25点筛选均值与复核均值排序的 `rho_rev` 和 `rho_ret`；并列平均秩，完全同值导致相关无定义时NULL。ρ不替代前沿稳定性；低ρ、候选互换及无法稳定区分均为合法结果。

### 12.4 假设参数扰动单列

固定同一组复核种子，分别将疲劳的缺口权重0.8、连续失败权重0.15、预算权重0.2乘{0.75,1.0,1.25}，采用单因素扰动：基线1组＋每个系数上下各1组，共7组参数设定。再按需要检查β_gain、β_s、盾减伤、成长时间常数等，不默认全部做笛卡尔积。

随机种子波动表与参数扰动表分开。每一扰动设定下重新运行行为路径；结论明确哪些假设会改变排序。没有人为规定ρ阈值作为通过线；差异小于随机波动时报告无法区分，并按实际需要增加种子。

默认执行7组设定×25点×3复核种子=525个复核run，其中P0的75个已在主网格中；故新增450个run，主网格与痛感敏感性共650个独立逻辑run。按search_run_map去重计时，不能把复用结果重复当独立样本。


## 13. 工程目录、接口与执行协议

以下是后续开发的目标结构，当前文档交付不代表这些文件已经生成：

```text
project/
  prompt_spec.md
  README.md
  pyproject.toml
  config/
    demo.yaml                 # 本文教学默认配置
    experiments.yaml          # 三参照、25点、筛选/复核与敏感性
    source_manifest.json
    parameter_registry.yaml
    catalogs/units.yaml
    catalogs/banners.yaml
    timelines/synthetic.yaml
  src/zzz_research/
    config.py
    provenance.py
    rng.py
    observations/adapter_shiyu.py
    observations/quality.py
    combat/proxy.py
    combat/benchmark.py
    combat/zsim_adapter.py    # 可选、独立依赖环境
    economy/wallet.py
    economy/gacha.py
    economy/planner.py
    agents/decision.py
    agents/lifecycle.py
    simulation.py
    storage/schema.sql
    storage/writer.py
    analytics/observed.py
    analytics/cohorts.py
    analytics/survival.py     # KM可选、Cox默认关闭
    analytics/search.py
    report.py
    app.py
    __main__.py
  sql/
  tests/
  data/raw/<source_file_id>/
  data/processed/<snapshot_id>/
  outputs/<experiment_id>/<run_id>/
```

默认开发目标Python3.12。核心依赖NumPy、Pandas、SciPy、Matplotlib及轻量配置读取工具，SQLite使用标准库；Streamlit、lifelines与完整ZSim为分组可选依赖。实施时锁定实际可运行版本和环境，不把“最新版”当复现依据。

### 13.1 核心接口

| 接口 | 输入 → 输出 | 责任边界 |
| --- | --- | --- |
| validate_config | 配置与manifest → 规范化配置、ID、校验结果 | 公式有效域、供给时间、引用完整性、参数来源 |
| prepare_observations | 固定快照 → 观测表、排除表、覆盖报告 | 不建立未经验证的跨期玩家关联 |
| evaluate_proxy | 队伍/build/场景批次 → CI、伤害分解、状态 | 纯函数，无钱包或随机购买副作用 |
| build_benchmarks | 参考主体与场景 → 固定面板、配对结果 | 与玩家活跃和持有独立 |
| forecast_target | 当前库存、双池状态、目标、预算 → 阶段分布与期望效用输入 | 确定性DP，只依赖决策前信息 |
| execute_target | 当前状态、目标、预算、事件键 → 新资产/钱包/双池状态、事件日志 | 实际逐抽，保持部分成果 |
| step_players | 期初状态与本期事件 → 期末状态、Pre/Post面板、日志 | 严格六步顺序 |
| aggregate_run | 一个run的表 → 对账宽表与统计 | 先对齐粒度，不放大现金 |
| compare_configs | 完整候选运行 → 排序、非支配、敏感性 | 只比较统一协议的配置 |
| render_report | 已完成且已校验结果 → 报告、图表、来源说明 | 不修改模型参数，不制造缺失数值 |

代理模型对角色和玩家维度向量化；允许按时期、卡池和抽数槽循环，不强求把有状态逐抽改成错误的无状态矩阵。可对仍在抽取的玩家用活动掩码推进。

规划DP与战斗基准尽量预计算，允许与仿真核单独计时。只有完全相同配置和输入摘要才可命中缓存；不能只按strategy名称缓存。不同run并发时分别写临时数据库/分片，由主进程验证后合并，避免多个写者抢同一SQLite连接。

### 13.2 计划CLI

```text
python -m zzz_research validate-config --config config/demo.yaml
python -m zzz_research prepare-observations --manifest config/source_manifest.json
python -m zzz_research build-benchmarks --config config/demo.yaml --engine proxy
python -m zzz_research simulate --config config/demo.yaml --replicate 1101 --log-mode full
python -m zzz_research aggregate --run-id <run_id>
python -m zzz_research search --experiment config/experiments.yaml
python -m zzz_research report --experiment-id <experiment_id>
streamlit run src/zzz_research/app.py
```

这些是待实现接口，不是本次已执行命令。每阶段验证输入状态与摘要；失败写manifest并停止依赖阶段。网络下载只在prepare的显式下载步骤，simulate/report不隐式联网。

### 13.3 落库、日志和回放

- `full`模式：保存所有实际抽取与资源事件、全库存Pre/Post/冻结面板、生命周期和决策。三个参照情景及最终复核案例使用full。
- `summary`模式：搜索仍计算完整库存和状态，持久化玩家期宽表、生命周期、队列统计、保底/钱包终态和manifest；不存每抽与每角色明细。此模式不能提供未落盘的个体KM或详细归因。
- summary的入选/异常run可按同一config和seed重跑full；聚合结果必须一致。日志模式不进入模型决策，不改变随机输入或config的科学参数摘要。
- 大表分块写事务；主键和对账通过后提交最终状态。中断只保留failed/incomplete产物，报告不能把部分行当完整结果。
- raw只读；产物写临时路径后发布。失败不能覆盖上一份完整报告。

## 14. 报告、图表与本地看板

### 14.1 默认报告结构

1. 问题、模式与本次实际覆盖。
2. 公开样本发现：分母、样本量、评分制度、资料缺失及描述性结论。
3. 代理模型：公式、参数来源、假设、固定配对与κ敏感性。
4. 模拟行为：供给、概率、资源、购买与流失协议，三个参照情景结果。
5. 固定队列三口径，动态适用性与可选首次失效描述。
6. 25点候选集合，独立复核、两目标排序与假设敏感性。
7. 可支持的解释、替代解释、局限、复现信息与下一步。

报告按实际执行阶段裁剪；L3未运行时不展示空造的商业结果，L1不可用时明确标注。发现与建议必须绑定输入和结果，不以“流水崩塌”“虚假繁荣”等预设标题替代分析。

### 14.2 核心图表

| 图 | 内容 | 必须显示 |
| --- | --- | --- |
| 覆盖矩阵 | 实际赛季×来源/模式 | 缺期、样本量、资料覆盖 |
| 样本趋势 | 出场、配队、时间或分数 | 分子分母、制度分面、小样本提示 |
| 战斗配对图 | 同条件支援/机制改变前后CI | 改变项、成本差、假设标签 |
| 参考适用性矩阵 | 角色×日期×配置的CI与达标 | 实际日期/标准单位、无效场景 |
| κ敏感性图 | 0.7/1/1.3的结果差 | 同一未减伤输出总量 |
| 钱包与现金图 | 四来源抽数与两类现金 | 抽与元分面，不同单位不堆叠 |
| 固定队列图 | 三可用率及退出占比 | 固定队列、期初参与口径、未知率 |
| 候选集合图 | 模拟收入×Budget_40期末留存 | 25点范围、复核波动、入选频率 |
| 稳健性表/图 | 种子变化、参数扰动 | 两张表分开，允许无法区分 |

KM仅在数据适合时追加，不为凑固定图数强制产出。科研导出用300DPI PNG及SVG，中文字体记录实际路径；无中文字体可用明确英文。图中阈值1.0与舒适线0.95使用不同标记。

### 14.3 看板四个Tab

- 数据与口径：来源覆盖、缺失、过滤与样本内趋势。
- 战斗对照：队伍、投资、支援、盾覆盖/对齐情景及配对结果。
- 行为情景：预算层、钱包、模拟现金、活跃与队列三口径。
- 候选与稳健性：25点、筛选/复核、种子波动和参数扰动。

调整战斗参数可重算代理基准；要显示新的行为收入/留存必须触发完整行为重跑。滑块改变时，旧结果保留原配置标签或标记待重算，不能让新滑块和旧图混为同一运行。来源与假设说明可查看，但主要操作用队伍、预算和场景等业务语言。

## 15. 验收与验证计划

下列是待开发代码的必要验证，不代表本次文档已经跑过仿真。

### 15.1 独立数值与边界

1. K=794、D=952.8、无减防穿透时无盾防御倍率≈0.4545454545；盾防御1.8倍时≈0.3164556962；再考虑25%减伤的相对保留率≈0.5221518987。使用独立标量计算对照，不仅重跑同一helper。
2. 贯穿防御倍率为1，但独立减伤保留；θ=0/1，κ=1以及三档单调关系正确；κ不改变未减伤总量。
3. 养成新获取=1、上界≤1.16，非活跃冻结后实际倍率不变。支援增益缩放作用于增益部分。
4. 各概率点在[0,1]，角色n=73/74/89/90及音擎64/65/79/80边界正确；guaranteed=true仅保证下一S，不保证下一抽S。
5. 非UP与UP正确更新保证状态；角色/音擎独立且同组跨banner继承。零抽状态不变。
6. 手工控制随机槽构造预算耗尽在本体、专武、1影后的案例，资产保留并改变PostEval；目标完整时不再抽。
7. DP成本分布归一化、目标成本硬上界正确；用一个缩小的玩具概率系统穷举树独立验证阶段概率与预算截断，再用多次抽样检查默认曲线的分布，不以均值一致替代分布检查。
8. 用0、1、硬上界附近预算检查规划；改变CRN未来槽而不改当前信息，规划期望不能变化。

### 15.2 账务与时序

- 每来源/批次钱包守恒；四来源等价抽数合计为总抽数，现金仅记购买一次。
- 直购≤本期总额度，多个banner不各自重置额度；Budget_0没有订阅或直购收入。
- 现金订阅在购买期入账，消费历史订阅权益不重记现金；流失后没有新增领取/支出/养成。
- 阶段顺序、仅一次疲劳更新、PostEval使用实际部分库存，期末退出不抹去本期参与。
- 所有已持有输出角色包括未部署者均参与评估；没有支援资产时不能使用支援。
- 固定基准与购买/退出状态无关；未知/unsupported不自动变成低CI。
- 汇总现金在明细、玩家期宽表、时期表、全盘表之间一致；角色面板关联不放大收入。

### 15.3 统计与复现

- 同一获取队列人数不漂移；分母0为NULL；无未知时三口径恒等式成立。
- 流失后的实际CI=NULL、冻结配置指纹不变；冻结输出变化只来自环境。
- 每KM主体至多一个事件，曲线不增，中位数达到0.5的边界正确；反弹不撤销首次事件。
- Cox未开启不阻断交付；开启时无未来暴露回填、事件后区间为空，跳过原因可追溯。
- 同config/seed重跑、任务调序、块大小改变，科学结果和聚合摘要一致；run时间等非科学元数据可不同。
- CRN键无碰撞；角色/音擎与不同banner不误复用；扩容不改变已有槽位。
- 人工构造支配、并列和完全同值集合检查非支配与Spearman；25点均有5筛选+3复核，不遗漏失败run。
- 不对方向性业务结论写assert；没有赢家、低相关、低留存都可通过计算正确性验收。

### 15.4 性能、规模与计时

参考情景的生命周期为 `3000×10×3=90,000` 行。全库存面板实际规模为各run各期持有主输出数之和，Pre/Post与冻结行分别计数；不固定承诺45万。

25点×8种子主网格共有200个行为run，生命周期为6,000,000行；若平均每人期持有5个输出角色，仅post即约30,000,000行，尚未计Pre、冻结和逐抽事件。加入默认痛感敏感性后是650个独立网格run、19,500,000条生命周期；同一假设下post约97,500,000行。另两个非重复参照情景若各跑8种子，再加16个run；Ref_Mechanism复用网格基线。因此搜索默认summary，选定run再full回放。扩大N先估计实际内存和日志量，禁止以30万生命周期代替全流程规模。

独立记录 `Data_Prepare, Benchmark_Build, Planner_Precompute, Sim_Core, SQLite_Write, SQL_Query, Grid_Search, Plot_Report`。Sim_Core可包含热缓存查询，但不能隐瞒为本次配置发生的预计算；冷启动和热运行均披露。

“单run热仿真核<5秒”仅作为3000玩家、10期、单配置、规划缓存已准备的本机优化目标。记录硬件、环境、缓存条件和日志模式。未达目标先profiling；不能削减资产、取消部分获取或跳过日志而不披露。全网格和端到端不承诺5秒。

## 16. 实施里程碑与完成定义

| 里程碑 | 交付 | 必须通过的门槛 |
| --- | --- | --- |
| M0 规格与输入 | 本设计、配置、参数来源、schema迁移草案 | 无未声明的决定性参数，合成/观测模式分开 |
| M1 观测链路 | 少量公开快照到覆盖与描述汇总 | 粒度、分母、日期、缺失可核验；不可用状态不造数 |
| M2 战斗基准 | 代理、固定面板、配对、κ对照 | 独立数值检查、防止重复折损、来源标签正确 |
| M3 经济与行为 | DP、逐抽、钱包、全库存、六步与CRN | 小人群端到端对账、部分成果和可复现性 |
| M4 参照与搜索 | 3000人参照、25点筛选/复核、敏感性 | 完整候选覆盖、统一分母、随机与假设变化分开 |
| M5 报告与看板 | 可信结果、图表、来源、复现命令 | 配置与图表一致，结论不越证据，阶段耗时真实 |
| 可选扩展 | 完整ZSim适配、Cox、真实更细配置 | 适用条件、额外验证及失败降级明确 |

不必等所有可选模块完成才交付M1/M2；也不能以漂亮看板替代M3对账。每个里程碑保存检查结果和已知限制。

最终工程交付包括：源代码、锁定环境、可复现配置、数据来源与摘要（按许可处理原始数据）、SQLite/汇总CSV、图表、REPORT.md、本地看板、验证结果及分阶段计时。报告可生成求职项目描述，但只能陈述已实际完成与验证的工作。

## 17. 全局措辞与退出规则

- “参考ZSim公式的代理结果”“完整ZSim运行结果”“公开样本观测”三种标签不可互换。
- “模型内受控效应”不升级为真实游戏主因果证据；Cox关联不写因果延寿。
- “固定期累计模拟现金”“模拟期末留存”“25个候选的非支配集合”不升级为真实LTV、真实留存或全局最优。
- 参数来源缺失则标assumed；支持不足则unsupported；必要数据缺失则unavailable。不通过填目标数字、伪造记录或删除不利结果完成交付。
- 本文是实施设计，当前任务仅交付完整文档。后续开发以实际授权范围分阶段开展，不执行旧版内嵌的自动构建指令。


