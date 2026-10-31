# 统一发布目录

[总报告](report/REPORT.md) · [交互看板](dashboard/index.html) · [发布来源清单](publication_manifest.json) · [审核入口](../../docs/review/REVIEW_GUIDE.md)

当前发布合并 Layer 1 公开通关观测、V2 固定队伍实验和 N=1000 主网格结果。报告的根层副本 `REPORT.md` 与 `report/REPORT.md` 内容一致，相对链接适配各自位置。

| 路径 | 内容 |
| --- | --- |
| `report/`、`dashboard/` | 当前报告、SVG 图表与离线交互页面 |
| `research/n1000/` | 当前千人主网格的清单与统计汇总 |
| `research/n200/` | 同模型、同种子组的中等规模对照 |
| `research/fixed/` | 固定队伍实验与首轮研究汇总 |
| `research/config_science.json` | 当前千人基准运行的科学配置快照 |
| `observations/` | 已处理公开样本数据库、覆盖与出场统计 |
| 根层 `dim_*.csv`、`observed_*.csv/md`、`empirical_*.csv/json/md`、`boss_*.csv/json` | 配队、机制维表与条件成绩参照 |
| `report/BASELINE_REPORT.md`、`dashboard/baseline.html` | V1 发布基线 |
| `benchmarks/`、`reference/`、`search/`、`tables/`、`config_snapshot/` | V1 实验与配置资产 |

`publication_manifest.json` 登记当前报告的数据来源；`pipeline_manifest.json`、`validate.json` 以及旧表格目录继续描述 V1 基线。各实验的原始数据库保存在所属研究目录，详见[实验目录索引](../README.md)。

轻量重绘在项目根目录运行 `.\.venv\Scripts\python.exe -m zzz_research report --dir outputs/final_release` 或 `app --dir outputs/final_release`。保留本目录已发布的 CSV、JSON、清洗维表及 SVG 素材即可，不依赖底层 SQLite 库或原始快照。

重提基准素材运行 `.\.venv\Scripts\python.exe tools\rebuild_final_release.py`。它需要三个源研究目录的导出表、清单与图表，以及千人基准运行 SQLite 库，用于提取实际科学配置；复用现有 Layer 1 结果，不重新模拟。完整重算从配置建立运行库；重新导入 Layer 1 才需要 Hugging Face 原始快照。
