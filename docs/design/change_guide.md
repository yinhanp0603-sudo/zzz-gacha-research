# 修改与更新指南

> V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。当前研究参数从[V2配置入口](../../config/research_v2/experiment.json)维护，V2公式与行为增量见[V2设计](research_v2.md)。下表中的 `config/modules/` 路径为V1基线维护源；模块职责与影响关系继续适用。

## 最常见的修改入口

| 需求 | 首先修改 | 同步关注 |
| --- | --- | --- |
| 盾覆盖、HP、供给或新品强度 | [scenarios.json](../../config/modules/scenarios.json) | combat基准、规划、行为路径与统计 |
| 盾/成长/影画/支援倍率 | [combat.json](../../config/modules/combat.json) | 阶段CI、规划效用、完整行为及冻结评估 |
| 软保底、UP率或目标顺序 | [gacha.json](../../config/modules/gacha.json) | gacha、planner、最坏抽数、CRN容量与分布验证 |
| 免费/订阅/价格或来源顺序 | [economy.json](../../config/modules/economy.json) | 钱包、规划预算/成本、效用、现金汇总与守恒 |
| 下池、痛感与流失 | [behavior.json](../../config/modules/behavior.json) | 生命周期、独立验证与参数敏感性 |
| 玩家数量/预算层 | [population.json](../../config/modules/population.json) | 每层策略、固定队列分母、资源配额及规模 |
| 网格/种子/扰动 | [experiments.json](../../config/modules/experiments.json) |候选映射、运行数量、复核隔离与稳健性 |
| 随机映射 | [randomness.json](../../config/modules/randomness.json) | 科学身份、全run复现与CRN边界 |
| 输入/输出字段及单位 | [公共契约](modules/contracts.md) | schema、writer、SQL、CSV、报告及回放 |
| 图表布局与说明 | [呈现模块](modules/presentation.md) | 展示验证；不重定义科学算法 |

价格变更时明确是否仍保持“抽数预算”不变；本设计不能自动把价格变化解释为已识别的需求价格弹性。

## 一次修改的最短流程

1. 找权威模块或JSON，修改一个维护源；不要修改archive或defaults.md。
2. 判断是数值、算法、接口、schema还是展示变化，更新对应版本。
3. `python tools/design_docs.py impact M-COMBAT` 等命令查看受影响模块。
4. `python tools/design_docs.py sync` 更新参数视图；`python tools/design_docs.py check` 检查引用、依赖和配置。
5. 若需要单份全文，执行 `python tools/design_docs.py assemble`；生成文档只读。
6. 开发完成后运行影响矩阵指定的科学回归；文档检查不等于仿真正确。
7. 记录CHANGELOG；旧run仍指向旧配置/算法，不覆盖重算。

仅改变参数值通常只需编辑JSON和同步视图；改公式或数据含义必须更新对应模块协议。文档保留的v1.0推导/计数校验例是固定样例，不是运行默认值，不能从例子反向覆盖配置。

## 变更影响与最低回归

| 变更 | 最低检查 |
| --- | --- |
| 概率/保底 | 分布、保证状态、阶段顺序、部分获取、DP/执行一致、容量 |
| 钱包/价格 | 来源守恒、整数预算、现金只记一次、多banner总额度 |
| 资产/支援 | 真实入库与潜在阶段库存一致、获取日不重置、Pre/Post/冻结 |
| 战斗公式 | 独立标量、重复折损、κ端点、固定配对、规划CI |
| 行为 | 信息集不偷看未来、概率范围、六步时序、期初/期末活跃 |
| CRN | 键无碰撞、任务调序/扩容不变、同配置可复现 |
| schema/事件 | 迁移、FK、唯一键、现金关联不放大、summary/full回放 |
| 统计 | 队列不漂移、NULL与0、三分母恒等式、KM事件、非支配与并列 |
| 展示 | 配置标签与结果匹配、无旧图冒充新参数、单位/来源一致 |

## 版本与兼容

- 模块文档版本、API/DTO契约版本、科学算法版本、schema版本、产物格式版本分别记录。
- 同样API但科学计算变了，仍必须更换科学算法版本和config_id。
- 向后兼容可选字段通常MINOR；删除/必填/单位或事件语义变化是MAJOR。
- 只改文档错字或样式不改变科学身份。
- 新算法不能使用只按策略显示名命中的旧缓存；数据迁移保留旧run的可读版本。
- 配置布局变化若解析后的科学输入完全一致，可在记录迁移版本后保持同样科学结果；是否复用ID须由规范化协议显式决定。

## 更新状态

当前已完成公开观测接入、V2战斗与付费回路、1000人25候选的5组筛选＋3组独立复核；结果入口为[发布清单](../../outputs/final_release/publication_manifest.json)。[M0](../development/M0_verification.md)、[M2](../development/M2_verification.md)、[M3](../development/M3_verification.md)、[M4/M5记录](../development/M4_M5_verification.md)保留历史验证与V1基线身份。设计维护工具只同步文档与参数视图，不下载数据或运行模拟；`sync` 的默认参数投影仍来自V1配置，当前V2配置以独立入口为准。

