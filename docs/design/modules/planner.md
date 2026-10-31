# M-PLANNER｜决策前期望规划

> 模块文档版本：1.0.1｜整套设计：1.1.0｜V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。当前研究差异见[V2设计](../research_v2.md)。

[总入口](../../../prompt_spec.md) · [模块地图](../architecture.md) · [变更指南](../change_guide.md)

## 模块契约

| 项目 | 约定 |
| --- | --- |
| 负责 | 确定性DP与预测；不读实际CRN、不修改状态 |
| 输入 | 只读库存、预算、PityState、共用TargetPlan |
| 输出 | 阶段分布、期望支出和各阶段战斗表现 |
| 单向依赖 | [M-CONTRACT](contracts.md)、[M-GACHA](gacha.md)、[M-COMBAT](combat.md) |
| 参数维护位置 | 无独立数值配置；引用所属参数模块 |

数值参数以对应JSON为唯一维护源，[默认参数视图](../defaults.md)是自动生成的只读投影。下面保留的v1.0数值校验例用于解释公式，不覆盖配置；不得把示例复制成另一份全局默认值。

## 详细设计

### 8.3 决策期望的确定性计算

禁止读取实际抽取 CRN 推知未来出货成本。对每种 `(概率曲线版本,pity,guaranteed,pool_kind)`，以有限状态 DP 预计算“下一目标 UP 所需抽数”的离散分布。每池首次访问未完成阶段使用其当前真实状态；获得目标UP后该池回到 pity=0、guaranteed=false，后续同池阶段使用此重置状态，不能反复套入最初的垫抽。不同池基础随机流独立。

依目标路径卷积得到前缀成本 C_j。预算上限 B 下：
`P(K=j)=P(C_j≤B)-P(C_(j+1)≤B)`，末阶段为 `P(C_L≤B)`，C_0=0。
`E[N_spent]=sum(n=0…B-1) P(C_L>n)`。
F为[行为模块](behavior.md)定义的现有整数可花抽数时：
`E[N_topup]=sum(n=F…B-1) P(C_L>n)`，空和为0。

CI_K 使用当前日、实际既有库存及各阶段潜在资产计算；新角色 G=1。由于本版歪卡不参与战斗，阶段分布足以描述规划战斗结果；扩展歪卡能力时必须扩展状态。

缓存概率分布、前缀CDF和预算截断结果；战斗部分按持有离散配置查表或向量计算。采用有损近似时必须另标 approximation，并报告与精确DP的误差，不能悄悄换成均值成本。

### 规划职责隔离

planner只调用gacha的概率内核和combat的纯评估接口，不调用真实抽取执行器，不调用wallet扣款或读取RNG。目标阶段由同一个TargetPlan给出。

输入预算视图使用公共契约中的整数F、P、H、B。输出每阶段概率、每阶段潜在库存/CI及期望资源和现金支出；行为模块据此计算效用。若CI不可评估则返回unsupported，不把NULL当0。

概率成本缓存只由概率曲线、池状态、TargetPlan和预算确定；支援增益改变不使概率CDF失效，但阶段CI/效用缓存必须失效。

### 实现说明（planner_dp_v2，2026-10-28）

单阶段“下一 UP 所需抽数”分布在首次使用某概率曲线时一次性预计算为 NumPy 查找表 `(pity, guaranteed) → pdf`，进程内按曲线参数共享；截断前缀卷积用 NumPy。数学定义不变，相对 v1 只有浮点求和次序差异（实测决策逐条相同，日志期望值差 ≤1e-13）；按版本规则升级 planner 版本并改变 config_id。

## 更新与验收

修改本模块时先查看[影响矩阵](../change_guide.md)，更新对应配置/契约版本，并执行 `python tools/design_docs.py sync` 和 `python tools/design_docs.py check`。这两条仅维护设计资料；科学验证仍按[验证模块](validation.md)实施。模块输入输出字段不得在此复制出与[公共契约](contracts.md)或[数据库契约](storage.md)相冲突的定义。

