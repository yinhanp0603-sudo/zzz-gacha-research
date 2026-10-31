# M-ROSTER｜资产、库存与配置冻结

> 模块文档版本：1.0.1｜整套设计：1.1.0｜V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。当前研究差异见[V2设计](../research_v2.md)。

[总入口](../../../prompt_spec.md) · [模块地图](../architecture.md) · [变更指南](../change_guide.md)

## 模块契约

| 项目 | 约定 |
| --- | --- |
| 负责 | 资产数量、首次获取日和实际装备状态；不算出货概率或伤害 |
| 输入 | AssetDelta、已计算的养成值与库存快照 |
| 输出 | RosterState、可行队伍及冻结快照 |
| 单向依赖 | [M-CONTRACT](contracts.md) |
| 参数维护位置 | 无独立数值配置；引用所属参数模块 |

数值参数以对应JSON为唯一维护源，[默认参数视图](../defaults.md)是自动生成的只读投影。下面保留的v1.0数值校验例用于解释公式，不覆盖配置；不得把示例复制成另一份全局默认值。

## 详细设计

### 权威状态与职责

roster拥有角色/武器数量、首次获取日、影画、实际装备倍率和被冻结的库存；不拥有概率、价格或战斗公式。中性角色是显式库存资产，不是评估时凭空创建。

- apply_asset_delta：应用初始化或已实际获得的资产；同一asset_event_id只能应用一次。
- snapshot：产生只读RosterView，供规划和战斗评估。
- update_disc_values：保存combat按当前日期计算的值；获取影画或专武不重置角色首次获取日。
- enumerate_feasible_teams：只列库存可用、锚定主输出且符合槽位限制的候选；不计算伤害。
- freeze：在退出期最后PostEval后冻结实际Box、装备、影画、武器和支援，后续不再养成或获取。

### M2资产命名与影画溢出

专武资产使用公共契约的`signature_weapon_id(unit_id)`，即`unit_id#signature`；不在各模块自行拼接另一种名称。

接受`CHAR_DUPLICATE_OVERFLOW`作为超过6影的重复角色的非战斗类别ID。一次获取先补本体，再填满剩余影画，超出数量逐个累计；角色影画始终封顶6，首次获取日期保留。该类别仅登记数量，不产生返还货币、额外战力或收入，也不改变抽卡结果和保底状态；以后加入重复补偿必须作为新的经济规则显式建模。

### 全库存与潜在阶段库存

实际入库只处理PullOutcome中的真实结果。规划可以构造临时只读的各阶段库存，不能写入实际持有状态或获取队列。两者共用同一资产解释规则，避免forecast与实际入库含义不同。

每个历史持有主输出均参与潜在队伍评估，即使未实际部署；不同锚定主输出的最佳队伍不代表可以同时复用支援。支援通过队伍成员体现，不伪造单独的支援通关指标。

### 与其他模块的契约

公共类型在 [contracts.md](contracts.md)，字段落库在 [storage.md](storage.md)，成长公式在 [combat.md](combat.md)，Pre/Post与冻结时序在 [runtime.md](runtime.md)，三分母在 [statistics.md](statistics.md)。这里只维护状态转移规则。

## 更新与验收

修改本模块时先查看[影响矩阵](../change_guide.md)，更新对应配置/契约版本，并执行 `python tools/design_docs.py sync` 和 `python tools/design_docs.py check`。这两条仅维护设计资料；科学验证仍按[验证模块](validation.md)实施。模块输入输出字段不得在此复制出与[公共契约](contracts.md)或[数据库契约](storage.md)相冲突的定义。

