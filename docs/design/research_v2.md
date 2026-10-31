# 研究情景 v2：机制匹配、售后支援与付费意愿

本设计回答：机制锁强度 λ 与售后支援强度 a 怎样改变老角色保值寿命、玩家囤抽和付费心理，以及固定初始玩家队列的现金与留存。实验范围、候选、观察期和执行命令见[研究协议](../development/research_v2_protocol.md)。

## 配置与模块边界

研究入口为 [config/research_v2/experiment.json](../../config/research_v2/experiment.json)，独立产物写入 `outputs/research_v2/`。

| 维护源 | 职责 |
|---|---|
| [combat.json](../../config/research_v2/modules/combat.json) | 防御公式、养成/投资乘区、机制惩罚、主核适配、定向支援 |
| [behavior.json](../../config/research_v2/modules/behavior.json) | 下池效用、续订/直购意愿、囤抽、疲劳与流失 |
| [scenarios.json](../../config/research_v2/modules/scenarios.json) | 固定商品排期、机制类型排期、HP 与主核基准增长、观察窗口 |
| [population.json](../../config/research_v2/modules/population.json) | 固定初始人口、预算能力和核心留存分组 |
| [experiments.json](../../config/research_v2/modules/experiments.json) | 5×5 候选、筛选/复核种子、扰动范围 |

`proxy_v2*` 启用机制匹配公式，`behavior_v2*` 启用动态付费意愿。两者可分别替换为 v1，形成协议规定的 2×2 模块对照。每种科学配置拥有独立 `config_id`。

## 战斗：三个机制通道

角色的 `pierce_share=v` 只表示跳过防御计算的伤害份额。机制类型 `k` 的适配度和支援能力另行登记：

\[
m_{u,s,k}=\min(1,f_{u,k}+\ell_{u,s,k}),\qquad
e=\lambda\,\gamma_k(1-m_{u,s,k}).
\]

`f` 为主核机制适配，`ℓ` 为该主核—支援组合的适配提升，`γ` 为机制基础惩罚。无配对支援时 `ℓ=0`。λ 为零或完全适配时，机制惩罚为零。

防御区保留参考 ZSim 的结构。`K` 是攻击方等级系数，`DEF_effective` 按减防与穿透顺序计算：

\[
M_{\rm DEF}=\frac{K}{K+DEF_{\rm effective}},\qquad
Q_j=(1-v)\frac{M_j}{M_{\rm ref}}+v.
\]

`Q₀` 表示环境基础防御下的相对输出。`Q₁(e)` 另将盾内防御加成缩放为 `b₀e`。盾覆盖率 θ 与爆发对齐 κ 共同给出潜在伤害落在覆盖期的份额：

\[
q=\frac{\kappa\theta}{1-\theta+\kappa\theta}.
\]

三类机制的有效伤害为：

| 机制 | `D_window` | 业务含义 |
|---|---|---|
| `defense` 防御锁 | `B·G·C·W·S·u·[(1−q)Q₀+q(1−d₀e)Q₁(e)]` | 覆盖期同时增加防御和额外减伤 |
| `window` 窗口锁 | `B·G·C·W·S·u·exp(−e)·Q₀` | 缩短整体可攻击窗口，贯穿同样受限 |
| `barrier` 壁垒锁 | `B·G·C·W·S·u·Q₀·[(1−q)+q·exp(−e)]` | 覆盖期的输出受机制适配限制 |

`B/G/C/W/u` 分别为主核参考战力、驱动盘、影画、专武与基础可攻击窗口，`CI=D_window/HP`。防御锁要求 `d₀e≤1`，参数有效域检查由战斗模块执行。

影画和专武改变伤害乘区；覆盖份额 q 只由环境与爆发对齐决定。机制窗口折损与基础窗口 u 分别保留在伤害分解中。

### 专拐净增益与配对

\[
S=1+a\,g_{u,s}.
\]

`g` 是相对已有可用通用辅助的数值净增益。a 只缩放 g；破壁能力 `ℓ` 不随 a 改变。未登记配对的支援取 `g=0,ℓ=0`，按其实际收益保留在面板中。

| `combat.research_v2` 字段 | 内容 |
|---|---|
| `mechanism_strength` | λ |
| `mechanisms.{k}.penalty` | `γₖ` |
| `unit_profiles.{unit_id}.affinities.{k}` | `fᵤ,ₖ∈[0,1]`，覆盖全部登记机制 |
| `support_pairs.{anchor}.{support}.net_gain` | `gᵤ,ₛ≥0` |
| `support_pairs.{anchor}.{support}.match_lift.{k}` | `ℓᵤ,ₛ,ₖ∈[0,1]`，未列机制取零 |
| `co_output.{unit_id}`（可选） | 混合型坐可替换位时的副输出份额 `ρₕ∈[0,1]`，缺省为 0 |

主核档案覆盖 `starter_dps` 和全部主输出商品；配对支援必须引用 `role=support` 或 `role=hybrid` 的商品。混合型同时是主核与支援资产；它坐可替换位时 \(D=D_{u}+\rho_h D_h^{solo}\)，\(D_h^{solo}\) 按混合型自身的投资、养成与机制适配计算且不带支援，请求以 `flex_investment`／`flex_actual_g` 携带其投资。`co_output` 只接受 `role=hybrid` 的商品。玩家战斗评估始终选择已有库存中的最佳可行队伍，新增支援资产不会强制替换原有更优队伍。

伤害分解保留 `Q0/Q1`、`q`、`mechanism_base_match`、`mechanism_support_match_lift`、`mechanism_retained_output` 和 `support_factor`；启用副输出时另记 `D_anchor`、`co_output_share` 与 `D_co_output_solo`，用于区分防御贯穿、机制适配与数值增益的贡献。

## 行为：支付能力与支付意愿分开

初始预算层及其直购上限、订阅资格描述支付能力，运行内固定。`SpendingState` 另存 `subscription_active`、`topup_allowed` 和 `last_paid_failure`；这三个字段不改变玩家的初始分组。

续订和直购使用各自的 Logistic 门控。对渠道 j：

\[
z_j=\alpha_j+\eta_jI_{j,prev}
-\beta_{F,j}F_{prev}-\beta_{s,j}s_{prev}
-\beta_{h,j}h_{prev}-\beta_{L,j}L_{prev},\qquad p_j=\sigma(z_j).
\]

`F/s/h` 是进入本期前已形成的疲劳、连续失败持续量和囤抽记忆；`I` 是该渠道上一次的允许状态。`L` 表示最近一次发生实际直购的期末仍未满星。

直购额外加入预期补强收益与支付成本：

\[
z_{topup}\mathrel{+}=\beta_g\Delta U-\beta_c\frac{E[\text{直购抽数}]}{\text{剩余直购上限}}.
\]

`ΔU=E[G(CI_K)]−G(CI₀)` 来自逐阶段规划器，包含连续战力提升和舒适线附近的平滑跨线收益。允许直购只开放既有额度；实际现金仍在抽取不足整抽资源时按需产生。

| 接口 | 调用时点与结果 |
|---|---|
| `SpendingParams.from_config` | 读取 `behavior.research_v2.spending.subscription/topup` |
| `subscription_decision` | 每个续订节点决定是否购买该次权益；拒绝时保留已获得权益 |
| `paid_decision` | 每个待抽商品规划后决定是否开放直购；拒绝则用免费/已有资源重新规划 |
| `fact_spending_decision` | 记录先验状态、能力、概率、允许/拒绝、预期收益与成本 |

停续订、拒绝直购和退坑是不同结果。停止付费的玩家仍可活跃、抽取免费资源，后续意愿恢复时可再次付费。无支付能力或已退坑者不能由门控创造额度。

本期最终 `Post-Eval` 后，只有发生实际直购才更新 `last_paid_failure`；无直购期保留记忆。后续实际直购且满星可清除该记忆。当前购买不读取尚未发生的期末结果。

囤抽、疲劳与流失仍按[生命周期规则](modules/behavior.md)在期末更新一次。满星按配置扣减疲劳，是否归零取决于原疲劳与恢复量。

## 时序与共同随机数

每期顺序为：资源与续订 → `Pre-Eval` → 付费门控与下池 → 逐抽保留成果 → `Post-Eval` → 生命周期与流失。逐抽保底、部分成果入库和四来源钱包沿用原模块。

新增支付事件使用独立键，以下均不包含 `config_id` 或候选策略：

```text
续订：[player_id, t_idx, "subscription", renewal_slot, "spending"]
直购：[player_id, t_idx, banner_id, "topup", "spending"]
下池：[player_id, t_idx, banner_id, decision_slot, "decision"]
单抽：[player_id, t_idx, banner_id, pool_kind, draw_slot, rng_type]
流失：[player_id, t_idx, "churn"]
```

随机源统一前置主种子与域标签。是否进入某次消费或抽取不改变其他事件的随机值。

`timeline.n_periods` 定义固定供给目录，`research_v2.observation_periods` 只截断观察窗口。10/20 期使用相同初始人口与前 10 期商品、环境和事件键；未观测的后续商品不会提前进入玩家库存。

## 保值寿命与结果口径

自然寿命：固定主核、投资和养成方案，无限定支援。维护寿命：同一方案在指定支援出道时加入该支援。配对在 Layer 2 预先定义，不依赖玩家是否主动购买。

首次 `CI<1` 是首次失效；出道即不达标可以记为 0 天。后续恢复另记恢复时点和累计满星期数，不撤销首次事件。观察结束仍未失败按右删失处理；KM 未降至 0.5 时报告中位数未达到。

维护支援登场晚于首次失效时，仍可能增加后续满星期数，却不会推迟已经发生的首次失效。晚出道主核随访较短，应优先比较相同出道队列与相同投资方案。

Layer 3 保留全库存实际配置、部署和冻结配置反事实结果。收入与留存以固定初始队列为分母，停氪者仍留在原核心层分母中。支援池实际抽数、直购现金和订阅收入分别核算；共享支援的支出不重复分摊到多个主核。

## 证据与适用范围

防御区结构参考 ZSim；两张 Layer 1 条件成绩表提供机制类别和支援收益范围的观测参照。`combat.provenance` 按参数路径登记具体来源，参数注册表优先采用最长匹配前缀。

机制指数惩罚、角色适配、配对能力与行为系数均为显式情景设定。专拐净增益参考条件成绩区间，数值与机制两通道的分解也属于情景假设。公开得分比不等于伤害保留率，条件成绩差异不构成购买或延寿的因果效应。

本研究不含新增获客、回流和运营成本；固定队列累计现金用于比较情景，不代表游戏全盘流水或完整 LTV。收入排序与留存排序分别报告，参数扰动与跨种子波动分开解释。
