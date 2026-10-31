# 项目设计总入口

> V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐｜更新于2026-10-31  
> 定位：公开样本观测＋参考ZSim防御公式的战斗代理＋显式行为情景。  
> Layer 1 已接入固定公开快照；当前主实验为1000人、25个候选、5组筛选＋3组独立复核种子。当前结果见[总报告](outputs/final_release/report/REPORT.md)和[发布清单](outputs/final_release/publication_manifest.json)。模块公共契约保留1.1.0，V1基线设计与V2增量分别维护。

## 从这里开始

| 需要做什么 | 入口 |
| --- | --- |
| 浏览全部模块 | [设计导航](docs/design/README.md)、[模块索引](docs/design/module_index.md) |
| 理解职责与调用关系 | [模块地图与接口](docs/design/architecture.md) |
| 修改当前研究参数 | [V2配置入口](config/research_v2/experiment.json)、[V2公式与边界](docs/design/research_v2.md) |
| 复核V1基线参数 | [V1配置入口](config/experiment.json)、[V1默认参数视图](docs/design/defaults.md) |
| 修改某个算法或统计口径 | 从[模块索引](docs/design/module_index.md)进入权威模块 |
| 确认修改影响哪些环节 | [变更指南](docs/design/change_guide.md) |
| 阅读模块公共设计与V1参数 | [自动汇总阅读版](docs/design/generated/full_design.md)；V2差异见[V2设计](docs/design/research_v2.md) |
| 查阅修改记录 | [CHANGELOG](docs/design/CHANGELOG.md) |
| 复核M0与运行测试 | [本机验证记录](docs/development/M0_verification.md) |
| 复核M2历史验收 | [M2本机验证记录](docs/development/M2_verification.md) |
| 复核M3历史配置身份 | [M3本机验证记录](docs/development/M3_verification.md) |
| 复核M4/M5与650运行恢复 | [M4/M5本机验证记录](docs/development/M4_M5_verification.md) |
| 复核当前研究与数据口径 | [审核指南](docs/review/REVIEW_GUIDE.md)、[多种子实验协议](docs/development/research_v2_stability_protocol.md) |

## 常改模块

- 战斗、秽盾、对齐、成长和支援：[战斗模块](docs/design/modules/combat.md)。
- 保底、部分获取和目标顺序：[抽卡模块](docs/design/modules/gacha.md)。
- 免费、订阅、现金和预算：[钱包模块](docs/design/modules/wallet.md)。
- 购买效用、疲劳和流失：[行为模块](docs/design/modules/behavior.md)。
- 队列、分母与生存描述：[统计模块](docs/design/modules/statistics.md)。
- 候选网格、种子与敏感性：[实验模块](docs/design/modules/experiments.md)。

## 更新规则

1. 模块文档维护科学规则与边界；模块JSON维护参数数值。
2. 公共类型与单位只在公共契约维护；物理字段只在数据契约维护。
3. 默认参数视图、模块索引和完整阅读版自动生成，不直接编辑。
4. 归档v1.0仅用于历史核对，不是当前实施规范。
5. 修改后同步并检查：

```text
python tools/design_docs.py sync
python tools/design_docs.py check
python tools/design_docs.py impact M-COMBAT
python tools/design_docs.py assemble
```

这些命令只维护设计资料；不下载数据、不运行ZSim或玩家仿真。文档检查不能替代科学模型与工程实现验证。

