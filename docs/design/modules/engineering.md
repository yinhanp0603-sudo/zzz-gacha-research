# M-ENGINEERING｜目录、接口与实施里程碑

> 模块文档版本：1.0.1｜整套设计：1.1.0｜V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。当前研究差异见[V2设计](../research_v2.md)。

[总入口](../../../prompt_spec.md) · [模块地图](../architecture.md) · [变更指南](../change_guide.md)

## 模块契约

| 项目 | 约定 |
| --- | --- |
| 负责 | 组合根与工程执行；科学规则调用所属模块 |
| 输入 | 模块协议和实施阶段 |
| 输出 | 组合入口、CLI、运行与交付约定 |
| 单向依赖 | [M-CONFIG](scenario_config.md)、[M-OBS](observations.md)、[M-RUNTIME](runtime.md)、[M-STORAGE](storage.md)、[M-STATS](statistics.md)、[M-SEARCH](experiments.md)、[M-PRESENTATION](presentation.md) |
| 参数维护位置 | 无独立数值配置；引用所属参数模块 |

数值参数以对应JSON为唯一维护源，[默认参数视图](../defaults.md)是自动生成的只读投影。下面保留的v1.0数值校验例用于解释公式，不覆盖配置；不得把示例复制成另一份全局默认值。

## 详细设计

### 本机启动与恢复

首次使用src布局的CLI，先执行`python -m pip install -e . --no-build-isolation`；pytest的`pythonpath=src`不等于CLI已安装。安装后使用`python -m zzz_research --help`查看命令。

转移数据库可用`tools/restore_benchmark_archives.py`安全解压并按清单回读SHA-256。`tools/rebuild_transferred_search.py`在禁用模拟入口的前提下验证并复用650个200人运行，重建合并库；缺少或不合格缓存会停止，不自动转为新模拟。历史云端汇总在重建前备份，压缩包保留。

物理外键子表索引只改变访问路径，不改变表字段、约束、科学参数或config_id。性能数字须标明实测环境，不能把云端耗时当作本机已复测结果。

以下是后续开发的目标结构，当前文档交付不代表这些文件已经生成：

```text
project/
  prompt_spec.md
  README.md
  pyproject.toml
  config/
    experiment.json           # 已有：模块配置入口
    modules/                  # 已有：各模块唯一参数源
    source_manifest.json      # 待接入数据时生成
    parameter_registry.json   # 待实现：由已解析配置与来源登记产生
  docs/design/                # 已有：模块、维护规则与生成视图
  tools/design_docs.py        # 已有：仅维护设计资料
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
    agents/roster.py
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
| execute_target | 当前状态、目标、预算、事件键 → 新资产/钱包/双池状态、事件日志 | runtime编排支付、单抽、入库并原子提交 |
| step_players | 期初状态与本期事件 → 期末状态、Pre/Post面板、日志 | 严格六步顺序 |
| aggregate_run | 一个run的表 → 对账宽表与统计 | 先对齐粒度，不放大现金 |
| compare_configs | 完整候选运行 → 排序、非支配、敏感性 | 只比较统一协议的配置 |
| render_report | 已完成且已校验结果 → 报告、图表、来源说明 | 不修改模型参数，不制造缺失数值 |

代理模型对角色和玩家维度向量化；允许按时期、卡池和抽数槽循环，不强求把有状态逐抽改成错误的无状态矩阵。可对仍在抽取的玩家用活动掩码推进。

规划DP与战斗基准尽量预计算，允许与仿真核单独计时。只有完全相同配置和输入摘要才可命中缓存；不能只按strategy名称缓存。不同run并发时分别写临时数据库/分片，由主进程验证后合并，避免多个写者抢同一SQLite连接。

### 13.2 计划CLI

```text
python -m zzz_research validate-config --config config/experiment.json
python -m zzz_research prepare-observations --manifest config/source_manifest.json
python -m zzz_research build-benchmarks --config config/experiment.json --engine proxy
python -m zzz_research simulate --config config/experiment.json --replicate 1101 --log-mode full
python -m zzz_research aggregate --run-id <run_id>
python -m zzz_research search --experiment config/experiment.json
python -m zzz_research report --experiment-id <experiment_id>
streamlit run src/zzz_research/app.py
```

这些是待实现接口，不是本次已执行命令。每阶段验证输入状态与摘要；失败写manifest并停止依赖阶段。网络下载只在prepare的显式下载步骤，simulate/report不隐式联网。

### 13.3 落库、日志和回放

- `full`模式：保存所有实际抽取与资源事件、全库存Pre/Post/冻结面板、生命周期和决策。三个参照情景及最终复核案例使用full。
- `summary`模式：搜索仍计算完整库存和状态，持久化玩家期宽表、生命周期、队列统计、保底/钱包终态和manifest；不存每抽与每角色明细。此模式不能提供未落盘的个体KM或详细归因。
- summary的入选/异常run可按同一config和seed重跑full；聚合结果必须一致。日志模式不进入模型决策，不改变随机输入或config的科学参数摘要。
- M3重跑使用独立数据库，已有run_id不在原库覆盖；同库attempt分区和断点续跑留待显式扩展。
- 大表分块写事务；主键和对账通过后提交最终状态。中断只保留failed/incomplete产物，报告不能把部分行当完整结果。
- raw只读；产物写临时路径后发布。失败不能覆盖上一份完整报告。

## 补充协议

| 里程碑 | 交付 | 必须通过的门槛 |
| --- | --- | --- |
| M0 规格与输入 | 本设计、配置、参数来源、schema迁移草案 | 无未声明的决定性参数，合成/观测模式分开 |
| M1 观测链路 | 少量公开快照到覆盖与描述汇总 | 粒度、分母、日期、缺失可核验；不可用状态不造数 |
| M2 战斗基准 | 代理、固定面板、配对、κ对照 | 独立数值检查、防止重复折损、来源标签正确 |
| M3 经济与行为 | DP、逐抽、钱包、全库存、六步与CRN | 小人群端到端对账、部分成果和可复现性 |
| M4 参照与搜索 | 配置指定人群、网格、筛选/复核、敏感性 | 完整候选覆盖、统一分母、随机与假设变化分开 |
| M5 报告与看板 | 可信结果、图表、来源、复现命令 | 配置与图表一致，结论不越证据，阶段耗时真实 |
| 可选扩展 | 完整ZSim适配、Cox、真实更细配置 | 适用条件、额外验证及失败降级明确 |

不必等所有可选模块完成才交付M1/M2；也不能以漂亮看板替代M3对账。每个里程碑保存检查结果和已知限制。

最终工程交付包括：源代码、锁定环境、可复现配置、数据来源与摘要（按许可处理原始数据）、SQLite/汇总CSV、图表、REPORT.md、本地看板、验证结果及分阶段计时。报告可生成求职项目描述，但只能陈述已实际完成与验证的工作。

## 更新与验收

修改本模块时先查看[影响矩阵](../change_guide.md)，更新对应配置/契约版本，并执行 `python tools/design_docs.py sync` 和 `python tools/design_docs.py check`。这两条仅维护设计资料；科学验证仍按[验证模块](validation.md)实施。模块输入输出字段不得在此复制出与[公共契约](contracts.md)或[数据库契约](storage.md)相冲突的定义。

