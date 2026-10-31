# 实验与发布目录

[当前总报告](final_release/report/REPORT.md) · [当前看板](final_release/dashboard/index.html) · [审核入口](../docs/review/REVIEW_GUIDE.md)

| 目录 | 角色 | 规模与口径 | 主要入口 |
| --- | --- | --- | --- |
| `final_release/` | 面向阅读和展示的统一发布目录 | Layer 1 公开样本 + V2 固定队伍实验 + 千人主网格汇总 | `report/REPORT.md`、`dashboard/index.html`、`publication_manifest.json` |
| `research_v2/` | 首轮 V2 实验与完整日志证据 | N=200，1 组筛选 + 1 组复核；含固定队伍实验、战斗/行为 2×2 消融 | `REPORT.md`、`tables/`、`runs/` |
| `research_v2_stability/` | 同模型的中等规模多种子对照 | N=200，5 组筛选 + 3 组复核，25 候选 × 20 期 | `REPORT.md`、`tables/`、`experiment_manifest.json` |
| `research_v2_stability_n1000/` | 当前主网格实验与运行数据库 | N=1000，5 组筛选 + 3 组复核，200 次 20 期物理运行；10 期取前缀 | `REPORT.md`、`tables/`、`experiment_manifest.json`、`runs/` |

## final_release 内部结构

| 路径 | 用途 |
| --- | --- |
| `report/REPORT.md`、根层 `REPORT.md` | 当前总报告，内容相同、相对链接适配各自位置 |
| `report/figures/v2_*.svg` | 当前 V2 图表 |
| `dashboard/index.html` | 当前离线交互看板 |
| `research/n1000/` | 千人主网格采用的配置身份、实验清单与表格副本 |
| `research/n200/` | 相同种子组下的 N=200 对照汇总 |
| `research/fixed/` | 固定参考队伍与首轮研究的表格副本 |
| `research/config_science.json` | 当前千人基准运行的科学配置快照 |
| `publication_manifest.json` | 当前发布使用的数据路径与实验角色 |
| `observations/` | 已处理公开样本库和观测汇总；保留原始导入结果 |
| `dim_observed_archetype.csv`、`observed_comp_archetype.csv` | 角色标签与完整三人配队清洗维表 |
| `empirical_knob*.csv`、`empirical_calibration_*.json/md` | 两个旋钮的公开样本条件成绩参照与说明 |
| `dim_deadly_assault_bosses.csv`、`boss_mechanism_*` | 首领机制维表与适配统计 |
| `report/BASELINE_REPORT.md`、`dashboard/baseline.html` | V1 发布基线 |
| `benchmarks/`、`reference/`、`search/`、`tables/`、`config_snapshot/` | V1 基线实验资产，供历史对照使用 |

运行数据库保留在所属实验目录，避免改动清单中的原始运行路径。当前阅读入口只使用 `publication_manifest.json` 登记的发布汇总；旧 `pipeline_manifest.json`、`validate.json` 和基线缓存仍属于 V1。

## 维护

- 轻量重绘：在项目根目录使用 `.\.venv\Scripts\python.exe -m zzz_research report --dir outputs/final_release` 或 `app --dir outputs/final_release`，仅读取已发布的 CSV、JSON、清洗维表和 SVG 素材；不需要 SQLite 库或原始快照。
- 重提发布素材：运行 `.\.venv\Scripts\python.exe tools\rebuild_final_release.py`，需要三个源实验目录的导出表、清单、图表与千人基准运行 SQLite 库，用于提取实际科学配置；该操作不重新模拟，不读取原始战斗快照。
- 改模型：使用 `config/research_v2/experiment.json` 并建立匹配科学配置身份的新实验；不要把 V1 与 V2 缓存混合解释。
- 原始数据位于 `data/raw/`，处理后的大型观测库位于 `final_release/observations/`；重新导入 Layer 1 才需要原始快照，重绘与素材重提均不触发下载或观测导入。
- 仓库只收录各目录的报告、图表、汇总表与清单；SQLite 库、`runs/` 和体积最大的明细表留在本机，见根目录 README 的“仓库内容”。
