# M-RUNTIME｜六步编排与状态提交

> 模块文档版本：1.0.1｜整套设计：1.1.0｜V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。当前研究差异见[V2设计](../research_v2.md)。

[总入口](../../../prompt_spec.md) · [模块地图](../architecture.md) · [变更指南](../change_guide.md)

## 模块契约

| 项目 | 约定 |
| --- | --- |
| 负责 | 执行顺序和原子提交；不另写概率/价格/效用公式 |
| 输入 | 初始状态、事件日历、模块服务与持久化端口 |
| 输出 | PeriodResult及完整EventBundle |
| 单向依赖 | [M-CONTRACT](contracts.md)、[M-COMBAT](combat.md)、[M-GACHA](gacha.md)、[M-WALLET](wallet.md)、[M-ROSTER](roster.md)、[M-PLANNER](planner.md)、[M-BEHAVIOR](behavior.md)、[M-RNG](randomness.md) |
| 参数维护位置 | 无独立数值配置；引用所属参数模块 |

数值参数以对应JSON为唯一维护源，[默认参数视图](../defaults.md)是自动生成的只读投影。下面保留的v1.0数值校验例用于解释公式，不覆盖配置；不得把示例复制成另一份全局默认值。

## 详细设计

### 8.1 每期执行顺序

1. 固定期初活跃状态 `active_start`，推进日期；对活跃旧角色更新养成，按事件发放资源并记录订阅现金。
2. PreEval：基于实际库存，求各已持有锚定主输出的最佳可行队伍，再选全库存最佳 CI。
3. 按 banner 顺序做一次下池决策；决定尝试后执行逐抽过程，可停在任一部分阶段。
4. 全部实际成果即时入库，更新钱包与保底；下一 banner 使用更新后的库存和本期剩余额度。
5. PostEval：重评所有持有主输出，保存最佳队伍与实际部署。
6. 仅更新一次囤卡记忆、疲劳、失败持续量，判断本期末流失，再保存期末与冻结快照。

本期末流失者本期仍参与并可能产生现金，下一期不再活跃。禁止用期末流失状态回填本期的实际参与分母。前一时期已流失者不执行1–5，只保留余额与反事实冻结评估。

### 编排层的单抽提交

execute_target归属于runtime，不归gacha或wallet。单抽流程为：检查预算与钱包 → wallet构造支付草案 → gacha单抽转移 → roster应用获取结果 → 原子提交三个新状态与关联事件。任一步失败均不提交半条科学状态。

M3使用`runtime_six_step_v2`：支付、入库、保底和来源批次索引先形成草案，完整抽取事件包与首次获取队列被writer接收、必要的块事务成功后，才一起提交内存状态。writer按完整事件边界分块，不能把同一次抽取的现金和出货拆成两个事务。未达到块阈值时事件仍在缓冲，持久化完成以flush和运行最终状态为准；后续提交失败的运行标为failed，已提交的诊断片段不能被当作完成结果。

PostEval前再次保存全部当前持有角色的实际装备倍率，保证当期新角色以G=1进入持有与冻结快照。冻结后缺失装备值应报错，不能用默认1掩盖未知值。

wallet和gacha彼此不调用；storage只接收EventBundle，不补算价格、保底或资产。编排层不能再定义第二份概率或效用公式。

### 接口与状态所有权

step_period接收只读ResolvedConfig、期初PlayerState、供给事件、模块服务与事件随机源；返回PeriodResult。时间推进与六步顺序只在此维护。实际字段定义引用 [公共契约](contracts.md)，物理字段投影引用 [数据契约](storage.md)。

历史资源、跨池保底、逐抽部分出货、当前入库与PostEval必须处在同一个本期状态路径。不得用另一配置已经生成的购买或流失决定加速本配置。

## 更新与验收

修改本模块时先查看[影响矩阵](../change_guide.md)，更新对应配置/契约版本，并执行 `python tools/design_docs.py sync` 和 `python tools/design_docs.py check`。这两条仅维护设计资料；科学验证仍按[验证模块](validation.md)实施。模块输入输出字段不得在此复制出与[公共契约](contracts.md)或[数据库契约](storage.md)相冲突的定义。

