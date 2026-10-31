# 模块化设计导航

> V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。

当前实现采用独立配置、模块契约、逐抽状态机和分层统计。V2 研究的公式差异从 [research_v2.md](research_v2.md) 进入；统一研究结论见[总报告](../../outputs/final_release/report/REPORT.md)，证据定位见[审核入口](../review/REVIEW_GUIDE.md)。

## 当前研究与公共契约

- [V2 机制与行为设计](research_v2.md)：防御/窗口/壁垒、主核—支援配对和动态付费意愿。
- [V2 当前配置](../../config/research_v2/experiment.json)：本次研究的数值维护入口。
- [多种子实验协议](../development/research_v2_stability_protocol.md)：固定队列、10/20 期前缀、现金拆解与配对误差。
- [首轮 V2 协议](../development/research_v2_protocol.md)：固定队伍与战斗/行为 2×2 消融。
- [模块索引](module_index.md)：19 个职责模块、对应配置与旧章节映射。
- [模块地图](architecture.md)：依赖、状态所有权和接口。
- [公共契约](modules/contracts.md)：单位、ID、数据类型、目标计划与版本。
- [数据契约](modules/storage.md)：SQLite 粒度、主键与事件关系。
- [变更指南](change_guide.md)：按职责定位维护源和相关回归。

公共模块契约继续描述共享行为。V2 新增及变更规则由 `research_v2.md` 与 `config/research_v2/` 定义；实现状态与实际接口以当前源码、数据库模式和 pytest 为准。

## V1 参数视图与历史资料

- [默认参数视图](defaults.md)：从根配置 `config/experiment.json` 及 `config/modules/` 生成，属于 V1 基线。
- [完整阅读版](generated/full_design.md)：拼接模块文档与上述参数视图，便于整体阅读公共设计。
- [历史快照](archive/full_spec_v1.0.md)：只读初版设计归档。
- [根目录设计入口](../../prompt_spec.md)：当前状态、模块导航与历史验证入口，V2 科学差异由独立设计维护。

数值以所选入口解析后的配置为准。生成的 V1 参数视图不覆盖 V2 参数；科学配置身份与历史运行缓存遵守公共契约。

## 实现与维护

数据导入、战斗代理、经济状态机、玩家行为、统计搜索、报告与看板均已有实现。维护位置见 [src/zzz_research/](../../src/zzz_research/)，验证用例见 [tests/](../../tests/)。各实验完成状态与采用的配置身份分别保存在运行清单和发布清单中。

`tools/design_docs.py` 维护设计索引、参数视图、引用与依赖检查；`sync` 更新资料，`check` 检查资料一致性。该工具不代替公式、状态转移、资金守恒或统计口径的 pytest 验证。具体实验运行与发布工具见 [tools/README.md](../../tools/README.md)。
