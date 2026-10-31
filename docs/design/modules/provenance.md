# M-SOURCE｜外部来源与许可

> 模块文档版本：1.0.0｜整套设计：1.1.0｜V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。当前研究差异见[V2设计](../research_v2.md)。

[总入口](../../../prompt_spec.md) · [模块地图](../architecture.md) · [变更指南](../change_guide.md)

## 模块契约

| 项目 | 约定 |
| --- | --- |
| 负责 | 来源、快照和数据可用边界；不推定真实消费或跨期身份 |
| 输入 | 仓库/数据快照及其元数据 |
| 输出 | source_manifest、来源检查、可用性与许可记录 |
| 单向依赖 | [M-CONTRACT](contracts.md) |
| 参数维护位置 | 无独立数值配置；引用所属参数模块 |

数值参数以对应JSON为唯一维护源，[默认参数视图](../defaults.md)是自动生成的只读投影。下面保留的v1.0数值校验例用于解释公式，不覆盖配置；不得把示例复制成另一份全局默认值。

## 详细设计

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

### 1.2 L3 外部参照：卡池流水与行为文献

以下来源只为 L3 的商品收入结构与状态机设计提供外部锚点，不进入任何参数估计。逐项 URL、读取日期、覆盖与限制登记在[外部来源清单](../../../outputs/final_release/external_source_manifest.json)。

| source_id | 来源 | 用途与边界 |
| --- | --- | --- |
| SRC_REVENUE_TRACKER | [Gacha Revenue Tracker](https://derkein.github.io/gacha-tracker-/) | 读取其数据文件：40 个卡池的 game-i 日本区日元估算、七麦中国 iPhone 逐日流水按卡池窗口加总与中国 iPhone 畅销总榜逐日名次（2026-10-30 更新），快照存于 `data/external/gacha_tracker_zzz_2026-10-31.json` |
| SRC_GAMEI / SRC_QIMAI | [game-i](https://game-i.daa.jp/)、[七麦数据](https://www.qimai.cn/) | 上表两个市场的原始口径；两者独立估算，用于交叉核对专拐池与主C池的相对量级 |
| SRC_SENSORTOWER / SRC_GACHAREVENUE | [Sensor Tower](https://app.sensortower.com/overview/6626a822a9cba85698402d6b/1606356401?country=WW)、[GachaRevenue](https://revenue.ennead.cc/revenue) | 月度汇总，仅作交叉索引 |
| SRC_RNGMOE | [rng.moe 全服调频统计](https://zzz.rng.moe/en/tracker/global) | 公开接口的 26 个限定角色池与 26 个音擎池聚合统计（2024-07 至 2026-01）：人数、总抽数、小保底胜负、逐抽出金率；快照存于 `data/external/rngmoe_zzz_global_2026-10-31.json` |
| SRC_STARDB | [StarDB Signal Tracker](https://stardb.gg/en/zzz/signal-tracker) | 仅展示登录用户自己导入的记录，无全服聚合；已核查、未采用 |
| SRC_LIT_TS_CHURN | [arXiv:2003.10287](https://arxiv.org/abs/2003.10287) | 登录流失与付费流失分为两条序列建模，协变量响应不同；支持停氪／退坑双状态结构 |
| SRC_LIT_F2P_CHURN | [Applied Sciences 12(6):2795](https://www.mdpi.com/2076-3417/12/6/2795) | 免费手游登录流失预测；只涉及退坑一侧 |
| SRC_LIT_GACHA_SPENDING | [Information 14(7):399](https://www.mdpi.com/2078-2489/14/7/399) | 713 名抽卡玩家的付费动机调查：解锁角色／武器 77.7%，更快推进 20.5%，竞争优势 5.8%；用于判断缺口驱动（M0）与追新驱动（M2）哪种结构更贴近自报动机 |
| SRC_KAGGLE_RETENTION / SRC_FIGSHARE_SEARCH | [Kaggle 留存与流失数据集](https://www.kaggle.com/datasets/rajatsurana979/mobile-game-retention-and-churn-analysis)、Figshare 检索 | 已评估、未采用：前者无付费字段与采集来源；后者没有受访者级的抽卡付费动机数据 |

比较结果由 `tools/build_external_benchmarks.py` 生成 `external_sim_benchmarks.csv` 等表，结论见总报告第 6 节。

第三方流水由 App Store／Google Play 畅销榜排名与日度权重反推，不含官方 PC 端与支付中心直充，双 UP 卡池同开期按规则切分；只用于校验卡池品类间的相对量级，不作为单个玩家充值因果的微观真值。

### 1.3 ShiyuStats 的可用边界

当前公开 [hash.py](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/scripts/hash.py) 按采集期重新编号。公开 UID 仅限同一快照组内关联，不以同号推定跨期同一玩家。

现成 [角色统计](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/scripts/char_usage.py) 包含有效通关筛选；投资档位分类不等于实际付费身份。展示配装缺失不等于未持有。首版不计算无法证明完整的持有率。

[文件清单](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/data/repo_files.csv) 在本次检查时列至 3.2.3，这不证明每个战斗赛季均有文件。[采集期—战斗期映射](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/data/versions/config.json) 和 [评分配置](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/scripts/comp_rates_config.py) 必须入库：采集期不能直接当战斗版本，时间制与分数制不直接拼接。

## 更新与验收

修改本模块时先查看[影响矩阵](../change_guide.md)，更新对应配置/契约版本，并执行 `python tools/design_docs.py sync` 和 `python tools/design_docs.py check`。这两条仅维护设计资料；科学验证仍按[验证模块](validation.md)实施。模块输入输出字段不得在此复制出与[公共契约](contracts.md)或[数据库契约](storage.md)相冲突的定义。

