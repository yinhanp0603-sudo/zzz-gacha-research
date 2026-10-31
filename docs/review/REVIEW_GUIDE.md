# 项目审核入口

## 审核对象

研究问题：在《绝区零》这类抽卡动作游戏里，机制锁 λ 与售后专拐净增益 a 怎样影响老角色保值寿命、囤抽与付费心理，以及固定玩家队列的长期现金和留存？

本项目是受耐用品垄断理论启发的反事实情景分析。公开通关样本提供出场与条件成绩参照；固定队伍实验提供模型内的同条件配对；行为层计算给定假设下的现金与留存。三类证据各自解释。

当前统一发布入口：

- [总报告](../../outputs/final_release/report/REPORT.md)
- [离线交互看板](../../outputs/final_release/dashboard/index.html)
- [发布来源清单](../../outputs/final_release/publication_manifest.json)
- [实验目录索引](../../outputs/README.md)
- [当前 V2 设计](../design/research_v2.md)
- [多种子实验协议](../development/research_v2_stability_protocol.md)

## 阅读顺序与权威来源

1. 阅读总报告的研究问题、主要对照、结论与局限。
2. 对照 V2 设计与实验协议，核对公式、分母、配对单位和观察窗口。
3. 检查当前配置、实际运行清单与汇总表中的科学配置身份。
4. 沿下表阅读实现与测试，把具体问题定位到输入、公式、状态转移或统计输出。

| 内容 | 当前来源 |
| --- | --- |
| 科学配置入口 | `config/research_v2/experiment.json` 与该目录下的 `modules/*.json` |
| 千人基准实际配置 | `outputs/final_release/research/config_science.json` |
| 千人主网格运行清单 | `outputs/research_v2_stability_n1000/experiment_manifest.json` |
| 千人原始汇总 | `outputs/research_v2_stability_n1000/tables/` |
| 发布所用汇总副本 | `outputs/final_release/research/{n1000,n200,fixed}/` |
| 最新规则设计 | `docs/design/research_v2.md` |
| 共用模块契约 | `docs/design/modules/`，V2 差异由上述设计与当前实现补充 |
| 初版方案 | `prompt_spec.md`、`config/experiment.json`、`config/modules/`，用于理解 V1 基线 |

当前千人基准 `config_id` 为 `38ac505e61920e78eff1d52043892dc6c48332f53843205867c7dbc87441c7f8`，对应 `λ=1, a=1`。每个候选拥有自身科学配置身份；种子、日志模式和展示产物不属于科学参数。N=200 对照与 V1 基线另有配置身份。

`config/experiment.json` 和通用 CLI 默认配置属于 V1。复核当前研究应显式选择 `config/research_v2/experiment.json`，并使用研究运行工具。初版模块文档中的实施阶段描述不代表当前完成状态。

## 代码与证据地图

| 审核主题 | 实现 | 测试或结果证据 |
| --- | --- | --- |
| 参数读取、版本与运行身份 | `src/zzz_research/config.py`、`versions.py` | `tests/test_m0_foundation.py`、`test_config_v2.py` |
| 防御、窗口与壁垒三通道 | `src/zzz_research/combat/proxy.py`、`benchmark.py` | `tests/test_combat_v2.py`；`outputs/research_v2/tables/l2_reference_*.csv` |
| 支援的数值净增益与机制适配 | `combat/proxy.py`、`config/research_v2/modules/combat.json` | `l2_support_pair_effects.csv`；总报告第 3 节 |
| 逐抽保底与部分获取 | `src/zzz_research/economy/gacha.py` | `tests/test_m2_kernels.py`、`test_m2_regressions.py` |
| 当前卡池期望规划 | `src/zzz_research/economy/planner.py` | `tests/test_m3_regressions.py`、`test_runtime_v2.py` |
| 现金与四来源抽数 | `src/zzz_research/economy/wallet.py`、`storage/writer.py` | `tests/test_m2_kernels.py`、`test_m3_runtime.py`；`run_summary.csv`、`period_outcomes.csv` |
| 购买效用、续订与直购意愿 | `src/zzz_research/agents/decision.py`、`spending.py` | `tests/test_spending_v2.py`、`test_runtime_v2.py`；`fact_spending_decision` |
| 库存、疲劳、停氪与流失 | `src/zzz_research/agents/roster.py`、`lifecycle.py`、`simulation.py` | `tests/test_m3_runtime.py`、`test_runtime_v2.py` |
| 事件索引公共随机数 | `src/zzz_research/rng.py` | `tests/test_rng_contract.py`；倒序玩家和重复运行一致性测试 |
| Layer 1 清洗与观测口径 | `observations/adapter_shiyu.py`、`quality.py`；`tools/build_observed_archetypes.py` | `tests/test_m4_observations.py`、`test_m4_observation_regressions.py`；观测覆盖与排除表 |
| 机制与支援条件成绩 | `tools/empirical_lock.py`、`empirical_support.py` | `outputs/final_release/empirical_knob*.csv` 与 `empirical_calibration_notes.md` |
| 配对区间、排序与现金贡献 | `tools/research_v2_stability_stats.py`、`research_v2_stability_report.py` | `tests/test_stability_stats.py`；`axis_effects.csv`、`axis_cash_decomposition.csv`、`cash_decomposition_seed.csv` |
| 运行计划、前缀与缓存复用 | `tools/run_research_v2.py`、`run_research_v2_stability.py` | `tests/test_stability_runner.py`；`experiment_manifest.json` |
| 报告与看板数据连接 | `src/zzz_research/publication_data.py`、`publication_report.py`、`publication_dashboard.py` | `outputs/final_release/publication_manifest.json` 与发布模块测试 |

所有相对源码路径均以项目根目录为基准。SQLite 模式见 `src/zzz_research/storage/schema.sql` 和 `analytics_schema.sql`。

## 数据资产

| 资产 | 位置 | 解释 |
| --- | --- | --- |
| 固定原始战斗快照 | `data/raw/shiyu_hf/` | 已下载，不需重复获取 |
| 上游元数据快照 | `data/raw/SRC_SHIYU_meta_d930cec50bfd/` | 赛季映射、角色、文件覆盖与许可；部分测试依赖该小目录 |
| 公开观测数据库 | `outputs/final_release/observations/observations.sqlite` | 4,013,856 条战斗记录；2,876,445 条合格记录 |
| 完整三人队伍清洗 | `outputs/final_release/observed_comp_archetype.csv` | 2,866,763 条合法完整三人记录，56 个战斗赛季，60 名角色 |
| N=200 首轮实验 | `outputs/research_v2/` | L2、模块消融、早期行为对照；包含全量日志 |
| N=200 多种子对照 | `outputs/research_v2_stability/` | 与千人实验使用相同 5+3 种子和候选 |
| N=1000 主网格 | `outputs/research_v2_stability_n1000/` | 200 次 20 期物理运行，以汇总日志保存 |
| 一份完整运行日志 | `outputs/research_v2/runs/h10/L1_A1__combat-v2__spending-v2/Research_seed1101.sqlite` | 可检查逐抽、全库存、资源与状态转移 |
| V1 发布基线 | `outputs/final_release/report/BASELINE_REPORT.md`、`dashboard/baseline.html` | 与当前 V2 总结分开阅读 |

原始数据、处理后观测库和各实验数据库保持在既有位置。各运行清单可能保存本机绝对路径；发布总报告与看板从清单登记的本地汇总读取，不要求重跑整个实验。

## 建议重点核对的问题

- **证据与假设边界。** 通关样本中的非对策/对策得分比、专拐条件成绩提升，是否被正确用作情景参照？是否有文字把条件关联写成机制因果效应、真实付费弹性或官方参数？
- **战斗通道。** `pierce_share` 是否仅绕过防御，窗口锁和机制壁垒是否仍依赖机制匹配？a 是否仅缩放净数值增益，支援机制适配提升是否另行处理？
- **状态时序与守恒。** 购买意愿是否读取决策前可知状态，部分获取是否即时参与抽后评估，四类抽数与现金是否分别守恒，停氪与退坑是否独立？
- **统计单位。** 配对是否按种子进行，五档 λ/a 是否只在种子内求平均，10/20 期是否被当作同一运行前缀，核心留存是否使用初始核心层分母？
- **现金拆解。** 分项是否使用共同的基期总现金分母，分项均值能否加回总变化，总区间是否保留协方差？
- **寿命与恢复。** 首次失效事件是否与后续重回满星分开，未观测到中位失效是否正确右删失，早/晚支援是否按同条件队伍比较？
- **结论与界限。** 是否保留跨零现金区间、0/3 入选及仅三组复核的精度边界，是否避免把有限网格的非支配集合称为总体最优？

审核以可定位的真实问题为依据。每项发现给出对应代码或表格位置、会影响的结果及最小修法；成立的实现可直接确认。项目面向本机协作者，不以安全攻防或不可达的极端输入作为重构理由。

## 本机复核

在项目根目录执行测试，检查受支持路径的公式、状态转移、守恒、事件键和统计口径：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

轻量重绘报告与看板，使用 `outputs/final_release/` 已发布的 CSV、JSON、清洗维表和 SVG 素材；不打开底层观测或运行 SQLite 库，也不需要 Hugging Face 原始快照：

```powershell
.\.venv\Scripts\python.exe -m zzz_research report --dir outputs/final_release
.\.venv\Scripts\python.exe -m zzz_research app --dir outputs/final_release
```

从源实验重提发布素材时，才运行：

```powershell
.\.venv\Scripts\python.exe tools\rebuild_final_release.py
```

该工具依赖 `outputs/research_v2/`、`outputs/research_v2_stability/`、`outputs/research_v2_stability_n1000/` 的导出表、清单与图表，并读取千人基准运行的 SQLite 库提取科学配置。它复用已处理 Layer 1 结果，不依赖原始战斗快照，不重新模拟。

查看千人实验计划而不模拟：

```powershell
.\.venv\Scripts\python.exe tools\run_research_v2_stability.py --config config\research_v2\experiment.json --players 1000 --out outputs\research_v2_stability_n1000 --plan-only
```

全量重新模拟从研究配置生成运行数据库，可复用匹配的已有运行；重新导入 Layer 1 才需要本地 Hugging Face 原始快照。审核展示使用已发布汇总，核对状态明细或重提科学配置时读取对应运行库。

## 已知研究局限

公开样本具有主动提交与仅观测通关的选择性，未提供可验证跨期玩家身份、真实充值或退坑。战斗代理不复现完整操作排轴；商品目录、价格、疲劳与购买函数包含显式假设。当前队列不含获客、回流、竞争游戏与运营成本，规划器只评估当期卡池。

复核只有三组独立种子，t 区间自由度为 2；排序、有限候选的前沿频率与逐项区间不能替代总体最优识别。扩大人口减少部分抽样噪声，不消除参数不确定性，也不保证每个实测区间都会变窄。
