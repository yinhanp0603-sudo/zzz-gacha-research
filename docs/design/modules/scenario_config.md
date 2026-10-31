# M-CONFIG｜配置、商品与情景

> 模块文档版本：1.0.2｜整套设计：1.1.0｜V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。当前研究差异见[V2设计](../research_v2.md)。

[总入口](../../../prompt_spec.md) · [模块地图](../architecture.md) · [变更指南](../change_guide.md)

## 模块契约

| 项目 | 约定 |
| --- | --- |
| 负责 | 配置装配和默认人群/时间/商品；不重复维护其他模块参数 |
| 输入 | 模块配置、实验选择和覆盖值 |
| 输出 | 只读ResolvedConfig、供给事件、有效科学参数 |
| 单向依赖 | [M-CONTRACT](contracts.md)、[M-RNG参数校验](randomness.md) |
| 参数维护位置 | [experiment.json](../../../config/experiment.json)、[population.json](../../../config/modules/population.json)、[scenarios.json](../../../config/modules/scenarios.json) |

数值参数以对应JSON为唯一维护源，[默认参数视图](../defaults.md)是自动生成的只读投影。下面保留的v1.0数值校验例用于解释公式，不覆盖配置；不得把示例复制成另一份全局默认值。

## 详细设计

### 配置入口与权威关系

当前参数入口是 [config/experiment.json](../../../config/experiment.json)，其中模块路径相对于该入口所在目录解析。完整默认值由 [默认参数视图](../defaults.md) 自动生成；**修改JSON，不手工修改该视图**。

- 人群、初始预算层与核心留存层：[population.json](../../../config/modules/population.json)。
- 时间轴、初始资产、商品强度、供给及三参照情景：[scenarios.json](../../../config/modules/scenarios.json)。
- 战斗、概率、钱包、行为与随机参数分别由对应模块JSON维护。
- 搜索点、种子和扰动协议由 experiments.json 维护。

所有教学默认值的含义与来源边界不变。商品强度仍独立于各策略HP；新主输出使用配置的首期强度与共同供给成长率，不按当前环境反推。角色类型不自动获得当期免罚。

### 配置解析顺序

1. 读取入口及所有模块，验证schema_version和依赖契约。
2. 展开默认值并建立只读基线；source、路径、标签等元数据单独登记。
3. 应用命名情景；HP、θ路径/常量及支援增益缩放都解析成实际有效输入。
4. 应用候选网格的λ/a，再应用已登记的痛感参数扰动。
5. 可选用户覆盖必须显式列出字段；与候选轴冲突时默认报错，不能悄悄覆盖后仍沿用原candidate_id。
6. 计算规范化科学配置摘要。显示名称、文件路径、日志模式不进入科学身份；算法版本和决定结果的实际输入必须进入。

配置引用不能越出项目配置根；重复键、未知字段、NaN、非法概率、缺失Tier策略或缺失商品引用应失败。后续应用的实际resolver遵守本协议；当前维护工具只校验设计资料，不宣称已实现仿真resolver。

### M3确认的三条显式情景假设

当前`scenario_resolve_v1_assumed`仅实现合成相对时间轴、区间末评估。以下是项目假设，不是从ZSim或公开样本估计出的规律：

1. `HP_t = reference.hp_index * (1 + hp_growth) ** (t - 1)`，首期使用参考HP。这里`hp_growth`表示每个情景区间更新的增幅；默认区间为42天。它不是自动按天连续增长的参数，变更区间天数会改变单位日的内容更新速度。若实验要求保持每42天相同增长率，调用方须显式换算区间增幅并登记覆盖，不能默认为物理时间等价。
2. 将事件按`(t,event_order,banner_id)`排序；仅对首次推出的主输出计数k，`B_ref_k = B_ref_first * (1 + growth_per_debut_step) ** (k - 1)`。支援推出不增加k，同期多名新主输出按稳定顺序逐个计数；商品强度仍独立于当期HP。
3. `role=normal`映射`pierce_share=0`，`role=piercing`映射`pierce_share=1`，表示两个理想类型。初始主输出使用其显式配置值；支援不生成独立主输出伤害。真实混合贯穿占比须扩展输入并升级解析版本，不把0/1假定冒充角色实测。

规划、编排和解析的实际科学版本由[versions.py](../../../src/zzz_research/versions.py)统一登记，在`resolve_config`计算`config_id`前纳入摘要；运行清单记录相同版本。存储实现与物理schema版本单独记录。该登记会使M3默认身份不同于早期M2记录，但不会改变公共随机键。

### 参数归属规则

每个科学参数只有一个模块JSON作为维护位置。其他模块拿到只读配置切片，不能import全局可变字典或在运行中原地覆盖。派生量只保存来源参数与推导版本，不再增加一个可独立编辑的重复输入。例如0.5222是条件校验结果，不作为额外减伤参数。

新增Tier需同时提供预算/订阅设定和行为策略；新增卡池必须声明稳定banner_id、pity_group及商品。修改这些内容的影响范围见 [变更指南](../change_guide.md)。

### 情景标定记录（2026-10-28）

`growth_per_debut_step` 由 0.12 调至 0.19：原值按首发计数增长而 HP 按区间增长，合成时间轴 10 期仅 8 个主输出首发，后期新角色出道即低于达标线。新值按 L2 参考轨迹目标（0+0/0+1 无支援出道当期与第 1 期 CI≥1、第 2–3 期跌破）选取，公式与解析版本不变；属情景假设，不是由真实商品强度估计。

`B_ref_first` 由 125 调至 126.875（+1.5%）：原值下 DPS_09 的 0+0 无支援出道 CI 为 0.9903，是 L2 目标的唯一例外；调整后全部主输出 0+0 出道 CI≥1（最低 1.0052），其余 L2 目标仍满足。同属情景假设。

## 更新与验收

修改本模块时先查看[影响矩阵](../change_guide.md)，更新对应配置/契约版本，并执行 `python tools/design_docs.py sync` 和 `python tools/design_docs.py check`。这两条仅维护设计资料；科学验证仍按[验证模块](validation.md)实施。模块输入输出字段不得在此复制出与[公共契约](contracts.md)或[数据库契约](storage.md)相冲突的定义。

