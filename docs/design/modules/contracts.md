# M-CONTRACT｜公共类型、时间与运行身份

> 模块文档版本：1.1.0｜公共契约：1.1.0｜整套设计：1.1.0｜V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。当前研究差异见[V2设计](../research_v2.md)。

[总入口](../../../prompt_spec.md) · [模块地图](../architecture.md) · [变更指南](../change_guide.md)

## 模块契约

| 项目 | 约定 |
| --- | --- |
| 负责 | 跨模块字段语义、TargetPlan、事件身份；不实现科学算法 |
| 输入 | 规范化配置、时间轴、事件及状态 |
| 输出 | 公共DTO、身份、单位、缺失与版本约定 |
| 单向依赖 | 无模块依赖 |
| 参数维护位置 | 无独立数值配置；引用所属参数模块 |

数值参数以对应JSON为唯一维护源，[默认参数视图](../defaults.md)是自动生成的只读投影。下面保留的v1.0数值校验例用于解释公式，不覆盖配置；不得把示例复制成另一份全局默认值。

## 详细设计

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

本版契约固定standard_span_days=42、credit_minor_per_pull=160、模型达标阈值=1。JSON显式携带这几项用于自描述，检查器要求与契约一致；它们不是普通调参项。改变单位或判定含义须升级契约、迁移相关字段并更新检查器。实际区间天数、订阅周期、舒适偏好阈值仍是各所属模块的可配参数。

数值计算保存双精度、只在展示时四舍五入。NULL 表示未知/不可评估，0 表示已定义的零；不相互替代。缺失场景和空风险集不补 0。比较同值时按稳定 ID 排序，禁止依赖字典遍历或并发完成顺序。

资源来源消耗的“抽数”是 `消耗credit/160`，可为小数；实际抽取总数、直购抽数、pity与资产数量仍为整数。没有可行队伍且原因已知时可定义能力贡献为0并记 `no_feasible_team`；缺失模型、unsupported或计算错误的CI为NULL，默认停止依赖该结果的规划与报告，不把错误当作失败战力。

## 更新与验收

修改本模块时先查看[影响矩阵](../change_guide.md)，更新对应配置/契约版本，并执行 `python tools/design_docs.py sync` 和 `python tools/design_docs.py check`。这两条仅维护设计资料；科学验证仍按[验证模块](validation.md)实施。模块输入输出字段不得在此复制出与[公共契约](contracts.md)或[数据库契约](storage.md)相冲突的定义。



## 公共输入输出类型（唯一语义定义）

M0/M2已实现这些DTO/Protocol的基础类型，后续计算与编排按本契约接入。schema_version、算法版本与契约版本分开；SQLite列投影仍以[storage.md](storage.md)为权威。

| 类型 | 核心语义 | 状态权威拥有者 |
| --- | --- | --- |
| ResolvedConfig | 解析后只读科学配置、输入摘要、算法版本、来源元数据 | config装配后不可变 |
| TargetPlan / TargetStage | 目标部件、固定顺序、跳过条件、stage_id、所属池；规划和执行共用 | contracts定义；配置提供路径 |
| PityState | pity_group、连续未出S次数、保证状态；不包含钱包 | gacha |
| WalletState / BudgetView | 来源批次credit、现金事件、本期直购额度与只读整数F/P/B | wallet |
| RosterState / RosterView | 角色/武器数量、非战斗类别计数、首次获取日、实际装备值与冻结标记 | roster |
| CombatRequest | 锚定队伍、投资、实际G、场景、模型版本和缺失状态 | 调用者构造只读快照 |
| CombatResult | CI、达标、伤害分解、eval_status、配置摘要 | combat纯函数产生 |
| Forecast | 阶段达成概率、各阶段潜在库存/CI、期望抽数/直购及planner_version | planner；不含实际未来随机结果 |
| ActionIntent | 是否尝试、TargetPlan、可花预算上限及决策日志 | behavior产生；runtime执行 |
| PullOutcome | 本次是否S/UP、结果资产、下一保底状态及资产变更意图 | gacha纯转移产生 |
| AssetDelta | 获取/初始化资产与数量、事件ID；重复应用可检测 | roster应用 |
| ResourceDelta / CashEvent | 带来源批次的credit变动/现金购买，及本次抽取关联ID | wallet产生 |
| LifecycleState | 活跃期初/期末、h/F、失败持续量、退出时间与原因 | behavior计算，runtime提交 |
| EventBundle | 同一次单抽或时期提交的状态前后与相关事件集合 | runtime原子组装 |
| PeriodResult | 完整一期状态、Pre/Post/冻结评估、统计与持久化事件 | runtime |

同一个字段只能由其权威模块更新。其他模块持有快照或变更意图，不直接修改另一个模块的内部对象。类型里不复制第二份价格/概率/伤害算法。

### M2向后兼容扩展（公共契约1.1.0）

- `CharacterHolding.gear_value=None`保存尚未评估或已经计算的实际装备倍率；`RosterState.categories=()`与`frozen=False`新增非战斗计数和整体冻结状态。`CategoryCount`及`AssetKind.CATEGORY`承载歪S和影画溢出的数量，不生成战斗角色。
- 专武资产ID统一由`signature_weapon_id(unit_id)`生成，规则为`unit_id + "#signature"`。抽取、库存装备标记和目标跳过判断共用同一规则；外部数据适配器须把ID映射到这一命名空间。
- 以上新增能力与带默认值字段属于MINOR升级。当前实现将契约版本纳入科学身份，因此1.0.0升至1.1.0会改变`config_id`；旧记录保留旧ID，不覆盖改写。RNG事件键不含该ID，单独升级契约版本不改变随机映射。

### 接口升级约定

- PATCH：不改变字段含义或科学结果的修正。
- MINOR：向后兼容新增可选字段/能力；旧调用方仍可运行。
- MAJOR：删除字段、变更单位/事件顺序/状态语义，或新增必填字段。
- 科学算法虽可保持API兼容，只要改变结果就升级算法版本并改变config_id；不以API PATCH掩盖科学变化。
- 读写schema升级单独迁移，旧run保留其schema与算法版本，不静默重算覆盖。

M3的规划、编排与情景解析实现版本只在[versions.py](../../../src/zzz_research/versions.py)维护，各实现引用该登记；配置解析在哈希前收集实际版本，Runtime启动时检查一致性。不能仅在运行结束后向清单补写版本。存储实现版本与物理schema版本属于产物协议，分别登记，不代替科学版本。

