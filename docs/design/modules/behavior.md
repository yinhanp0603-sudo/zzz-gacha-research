# M-BEHAVIOR｜购买效用与生命周期

> 模块文档版本：1.0.1｜整套设计：1.1.0｜V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。当前研究差异见[V2设计](../research_v2.md)。

[总入口](../../../prompt_spec.md) · [模块地图](../architecture.md) · [变更指南](../change_guide.md)

## 模块契约

| 项目 | 约定 |
| --- | --- |
| 负责 | 效用和生命周期公式；不执行抽取或查询数据库 |
| 输入 | Forecast、玩家只读视图、PostEval和显式随机值 |
| 输出 | ActionIntent、购买概率、疲劳/流失状态 |
| 单向依赖 | [M-CONTRACT](contracts.md) |
| 参数维护位置 | [behavior.json](../../../config/modules/behavior.json) |

数值参数以对应JSON为唯一维护源，[默认参数视图](../defaults.md)是自动生成的只读投影。下面保留的v1.0数值校验例用于解释公式，不覆盖配置；不得把示例复制成另一份全局默认值。

## 详细设计

### 8.2 平滑收益与购买概率

`CI0` 为当前库存最佳表现，不是待售单卡的表现。令：

\[
s_\tau(x)=1/(1+e^{-x/\tau}),\ \tau>0,\quad
G(c)=\beta_g c+\beta_s s_\tau(c-c_{comfort})
\]

规划必须覆盖预算截断后的部分获取。设 K 为完成的目标阶段数、`CI_K` 为对应库存最佳表现：

\[
U_i=\alpha_{tier}-\beta_h h_i+
\mathbb E[G(CI_K)]-G(CI_0)
-\beta_r\frac{\mathbb E[N_{spent}]}{B}
-\beta_p\frac{\mathbb E[N_{topup}]}{\max(P,1)}
\]
\[
p_{enter}=\sigma(U_i)=1/(1+e^{-U_i})
\]

F=`floor((可花免费credit+可花订阅credit)/credit_minor_per_pull)`，P为本期剩余直购抽数额度，H为当前剩余目标的最坏所需抽数，整数预算B=`min(F+P,H)`。B=0或目标全部持有时直接跳过，不计算购买概率。P=0时E[N_topup]=0。c_comfort为行为配置中的舒适偏好中心，不改变达标硬阈值。

β、τ、阈值、各Tier的α与h初值由behavior.json维护，当前值见默认参数视图。不存在“零收益默认50%”的隐藏假设；剩余少量购买概率来自显式偏好截距。拒绝只对当前事件生效，不形成永久不抽状态。

### 8.4 囤卡记忆、疲劳与随机流失

令d=elapsed_days/standard_span_days，g=max(0,c_full−CI_post)。c_full为公共契约的达标阈值；失败持续量s'=0若达标，否则s'=s+d。预算使用率b=topup_pulls_used/paid_cap_t，额度0时b=0。

\[
h'=\operatorname{clip}(a_h^d h+w_hdg-r_hdI(CI_{post}\ge c_{full}),h_{min},h_{max})
\]
\[
F'=\operatorname{clip}\{e^{-a_Fd}F+
d[w_g\,g+w_s\min(s',s_{cap})+w_b\,b-r_FI(CI_{post}\ge c_{full})],F_{min},F_{max}\}
\]
\[
p_{churn}=1-\exp[-\lambda_0d\exp(\gamma F')]
\]

用独立流失随机槽判定。没有策略标签、Boss机制系数或“虚狩持有”直接进入疲劳/流失公式；影响通过结果和投入传导。相同结果同权只是中立基准，不是真实心理规律。h可恢复，疲劳不为负，策略A未达标也可积累疲劳。

a_h、w_h、r_h及h的min/max来自hoarding；a_F、w_g、w_s、s_cap、w_b、r_F及F的min/max来自fatigue；λ_0、γ来自churn。所有系数均为假设。低于门槛不是确定退坑；满星也有很小基线流失。公开数据不用于伪称已估计这些参数。

### 情景标定记录（2026-10-28）

流失与疲劳系数、β_h 的当前值是按目标区间选取的情景假设：在与筛选/复核种子不相交的标定种子（7101、7102，200 人，Ref_Mechanism）上，使基准期末留存约 0.70–0.85、流失集中在 P04–P08。因此“留存落在该区间”是标定结果而非模型发现；λ、a 与痛感扰动的方向与大小才是在此基线下的情景推演输出。标定不使用筛选或复核种子，复核结果不回流调参。

### 直购痛感与满星恢复的交叉（2026-10-28）

预算项w_b·b只在未被下界截断时影响疲劳。报告从`fact_player_lifecycle`导出（满星×是否直购）交叉计数（`*_budget_fatigue_cross.csv`），据此说明budget_weight扰动是否为零效应；若扰动上限w_b·max(factor)小于r_F且直购玩家-期全部满星，则零效应是结构性的。该结论只在本模型设定下成立。

### 停氪与退坑分为两个状态

V2把付费意愿（订阅续订、逐池直购许可）与活跃状态分开：疲劳、连续失败和“上次付费后仍失败”只扣减下一期付费意愿，退坑由期末流失危险率单独判定，玩家可以停氪但继续免费活跃。商业手游的面板研究同样把两类流失作为不同对象：[Fernández del Río 等（Intelligent Data Analysis 2021，arXiv:2003.10287）](https://arxiv.org/abs/2003.10287)在约 210 万玩家的移动免费 RPG 日志上，将登录流失（9 天未登录）与付费流失（付费玩家 50 天无购买）建为两条日度序列，二者对抽卡活动、团战活动与节假日协变量的响应不同；[Mustač 等（Applied Sciences 2022, 12(6):2795）](https://www.mdpi.com/2076-3417/12/6/2795)单独预测免费手游的登录流失。外部研究支持双状态结构，不提供本模块任何系数。付费动机方面，[Lakić 等（Information 2023, 14(7):399）](https://www.mdpi.com/2078-2489/14/7/399)的 713 人调查中，自报付费原因以解锁角色／武器为主（77.7%），为更快推进占 20.5%、为竞争优势占 5.8%，更接近下文 M2 的追新结构；现行 M0 的“缺口驱动”购买效用因此是一种情景设定，依赖它的结论在报告中单独标注。

### 行为函数结构消融

| 设定 | 实现 |
| --- | --- |
| M0 | 现行 `behavior.json` |
| M1 无停氪缓冲 | 覆盖 `research_v2.spending.{subscription,topup}.{fatigue_weight,failure_weight,paid_failure_weight}=0`；疲劳只进入流失 |
| M2 强追新偏好 | [`behavior_m2_novelty.json`](../../../config/research_v2/modules/behavior_m2_novelty.json) 增加 `research_v2.novelty.beta_novelty=1.5`：新主C池（非支援商品）的下池效用 U 另加该常数，不随 CI₀ 满星归零；直购许可与支援池不变 |

`beta_novelty` 缺省为 0，此时决策与现行模型逐位一致，M0 配置身份不变。运行入口为 `tools/run_behavior_ablation.py`，结果见[总报告 5.5 节](../../../outputs/final_release/report/REPORT.md)。M1 与 M0 的核心层留存逐运行相同：现行模型中停氪状态只作用于现金通道，退坑由战斗挫败驱动。

## 更新与验收

修改本模块时先查看[影响矩阵](../change_guide.md)，更新对应配置/契约版本，并执行 `python tools/design_docs.py sync` 和 `python tools/design_docs.py check`。这两条仅维护设计资料；科学验证仍按[验证模块](validation.md)实施。模块输入输出字段不得在此复制出与[公共契约](contracts.md)或[数据库契约](storage.md)相冲突的定义。


