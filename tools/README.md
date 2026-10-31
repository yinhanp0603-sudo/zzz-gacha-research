# 工具导航

所有命令在项目根目录运行。当前研究入口为 `config/research_v2/experiment.json`；[总报告](../outputs/final_release/report/REPORT.md)、[审核说明](../docs/review/REVIEW_GUIDE.md)与[实验目录](../outputs/README.md)提供阅读入口。

## 当前发布

| 脚本 | 用途 |
| --- | --- |
| `rebuild_final_release.py` | 从三个源实验目录重提导出表、清单与图表，读取千人基准运行 SQLite 库中的科学配置，并更新统一报告和看板；复用已处理 Layer 1 结果，不模拟、不下载、不重导入 |

**轻量视图重绘**只读取 `outputs/final_release/` 已发布的 CSV、JSON、清洗维表与 SVG 素材，不依赖底层 SQLite 库或 Hugging Face 原始快照：

```powershell
.\.venv\Scripts\python.exe -m zzz_research report --dir outputs/final_release
.\.venv\Scripts\python.exe -m zzz_research app --dir outputs/final_release
```

**基准素材重提**需要 `outputs/research_v2/`、`outputs/research_v2_stability/`、`outputs/research_v2_stability_n1000/` 的导出资产，以及千人清单指向的基准运行 SQLite 库：

```powershell
.\.venv\Scripts\python.exe tools\rebuild_final_release.py
```

该操作读取真实运行记录中的科学配置，不需要原始战斗快照。完整模拟从配置生成运行库；重新导入 Layer 1 则需要原始快照。

## V2 实验与统计

| 脚本 | 用途 |
| --- | --- |
| `run_research_v2.py` | 首轮 V2 固定队伍、主网格和战斗/行为模块消融；不同套件通过 CLI 选择 |
| `run_research_v2_stability.py` | 25 候选 × 5 筛选/3 复核种子的主网格；20 期运行同时提供 10/20 期前缀，支持匹配缓存复用 |
| `research_v2_stability_stats.py` | 配对区间、轴向效应、共同分母现金贡献与非支配集合等统计函数 |
| `research_v2_stability_report.py` | 从完成的研究汇总生成专题报告、排序与配对图表 |

千人主网格入口：

```powershell
.\.venv\Scripts\python.exe tools\run_research_v2_stability.py --config config\research_v2\experiment.json --players 1000 --workers 8 --out outputs\research_v2_stability_n1000
```

添加 `--plan-only` 仅查看计划。统计协议见 [research_v2_stability_protocol.md](../docs/development/research_v2_stability_protocol.md)。

## Layer 1 清洗与条件成绩参照

| 脚本或资产 | 用途 |
| --- | --- |
| `observed_roster_discovery.py` | 从已导入观测库提取角色生命周期、高频三人队伍与双人共现 |
| `build_observed_archetypes.py` | 按主核与机制标签构建角色、配队清洗维表 |
| `observed_archetype_rules.json`、`observed_unit_names_zh.json` | 主核/插件规则与中文名称维护源 |
| `build_archetype_evolution.py`、`plot_archetype_evolution.py` | 按战斗赛季汇总流派比例并绘制 SVG |
| `build_deadly_assault_boss_dimension.py`、`deadly_assault_boss_catalog.json` | 危局首领机制维表及其维护源 |
| `build_boss_mechanism_stats.py` | 关联首领、机制与流派，输出覆盖与适配统计 |
| `build_empirical_calibration.py` | 汇总机制锁和支援条件成绩参照 |
| `empirical_lock.py`、`empirical_support.py`、`empirical_common.py` | 机制与支援的分层、匹配和公共统计函数 |
| `empirical_boss_regimes.json`、`empirical_support_pairs.json` | 实证比较窗口与主核—支援配对定义 |

以上工具读取已处理数据；日常发布直接复用它们的既有结果。各脚本的输入与输出选项以 `--help` 为准。

## 原始快照与导入维护

| 脚本 | 用途 |
| --- | --- |
| `fetch_shiyu_snapshot.py` | 获取固定修订的 Hugging Face 原始快照 |
| `audit_shiyu_raw.py` | 检查原始文件覆盖与行数 |
| `finalize_existing_observations.py` | 针对已完整入库的固定快照恢复索引与汇总；要求写入进程已停止 |
| `add_observation_lookup_index.py` | 维护观测查询索引 |
| `verify_shiyu_import.py` | 检查导入与原始样本的对应关系 |

本地原始快照和 401 万条处理结果已经保存。下载与导入维护独立于发布刷新。

## V1 基线与历史恢复

| 脚本 | 用途 |
| --- | --- |
| `rebuild_calibrated_benchmark.py` | 按 V1 标定配置构建基准与参照实验 |
| `restore_benchmark_archives.py` | 恢复原 650 次实验的分卷数据库 |
| `rebuild_transferred_search.py` | 复用已转移数据库重建旧搜索汇总 |
| `verify_benchmark_exports.py`、`verify_calibrated_lineage.py` | V1 导出与配置来源核对 |
| `refresh_layer1_report.py` | 原 Layer 1 导入后的 V1 报告刷新流程 |

这些脚本保留原实验路径约定，其中部分指向 `outputs/benchmark_n200/`。当前基线资产在 `outputs/final_release/`，当前发布使用上方 `rebuild_final_release.py`；历史恢复工具仅在复原对应实验时按其输入路径使用。

## 设计资料维护

`design_docs.py` 同步模块索引、V1 参数视图和完整阅读版，设计导航见 [docs/design/README.md](../docs/design/README.md)。该工具不运行仿真或代替 pytest。
