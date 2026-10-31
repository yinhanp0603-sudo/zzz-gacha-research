# M-STORAGE｜数据表与持久化

> 模块文档版本：1.0.1｜整套设计：1.1.0｜V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。当前研究差异见[V2设计](../research_v2.md)。

[总入口](../../../prompt_spec.md) · [模块地图](../architecture.md) · [变更指南](../change_guide.md)

## 模块契约

| 项目 | 约定 |
| --- | --- |
| 负责 | 物理schema、索引和迁移；不补算业务规则 |
| 输入 | 规范化观测、状态、EventBundle和结果 |
| 输出 | SQLite、对账宽表及可回放记录 |
| 单向依赖 | [M-CONTRACT](contracts.md) |
| 参数维护位置 | 无独立数值配置；引用所属参数模块 |

数值参数以对应JSON为唯一维护源，[默认参数视图](../defaults.md)是自动生成的只读投影。下面保留的v1.0数值校验例用于解释公式，不覆盖配置；不得把示例复制成另一份全局默认值。

## 详细设计

数据库建议名 `zzz_research.db`。开启 foreign_keys，批量事务写入；索引按查询需要建立，不用逐行commit。观测、基准和行为数据分表，禁止以相同player_id暗示跨层对应。

### M3实现与当前兼容边界

- 物理结构在[schema.sql](../../../src/zzz_research/storage/schema.sql)，格式版本为`storage_v1`；写入器实现为`storage_writer_v2`，两者分开登记。安装包包含SQL文件，安装后的包可直接创建数据库。
- `atomic_buffer`按完整事件包分块，可嵌套包住首次获取队列等关联行；不在单抽现金与出货之间提交。投影失败撤回该边界追加的缓冲；SQL失败回滚，提交成功后才累计已写行数。运行失败可以保留此前完整提交的诊断片段，下游只消费completed运行。
- 当前一个数据库内同一run_id只能创建一次。包括不同attempt_id在内的同库重跑会在修改旧状态前被拒绝；重跑或summary转full使用独立数据库。尚未实现同库attempt分区、断点续跑或原子替换发布，不把拒绝覆盖描述为已支持这些功能。
- 共享维度只复用主键与完整内容均一致的记录；主键相同但内容不同必须报错。catalog_id覆盖解析版本、事件/初始资产/商品参数、时间跨度、支援基础增益、保底组及可用目标路径名称，防止不同配置静默共用旧维度。
- 已建立`obs_battle/obs_team_member/obs_build/obs_coverage`结构；M3不写入公开观测数据，M1数据链路仍未验收。

字段约定：ID为TEXT；t/计数/布尔为INTEGER；金额为INTEGER分；日期为ISO TEXT；天数和指数为REAL；结构化配置为JSON TEXT。布尔 CHECK IN(0,1)，概率 CHECK在[0,1]。所有PK字段NOT NULL；无支援的已知状态使用维度值 `NONE`，未知资料用NULL。

下表列出完整的逻辑字段组；字段组展开后进入迁移文件，不能以一个不透明JSON代替关键分析列。JSON仅用于原配置、可变元数据或冻结状态。

### 10.1 来源、维度与运行表

| 表 | 粒度 / 主键 | 必需字段 |
| --- | --- | --- |
| source_manifest | 一个源文件快照；source_file_id | source_id,url,revision,retrieved_at,sha256,byte_size,license,coverage_json,status |
| parameter_registry | 一项配置参数；(config_id,parameter_name) | value_json,unit,source_kind,source_ref,applicability,rationale,sensitivity_json,status |
| run_manifest | 一个逻辑运行；run_id；唯一(experiment_id,config_id,replicate_id) | master_seed,rng_version,algorithm_versions_json,config_json,input_manifest_hash,code_revision,environment_json,status |
| run_attempt | 一次实际执行；(run_id,attempt_id) | log_mode,started_at,ended_at,status,error_code,output_hash |
| stage_timing | 一阶段耗时；(run_id,attempt_id,stage) | wall_seconds,row_count,peak_memory_mb,measurement_method |
| dim_period | 一个时间区间；(timeline_id,period_id) | t_idx,start_day,end_day,evaluation_day,elapsed_days,actual_start_date,actual_end_date,calendar_mode |
| dim_unit | 一个商品角色；(catalog_id,unit_id) | role,debut_period,debut_day,B_ref,pierce_share,weapon_id,base_support_gain,combat_eligible |
| dim_banner | 一个稳定供给事件；(catalog_id,banner_id) | period_id,event_order,target_unit_id,target_weapon_id,char_pity_group,wep_pity_group,allowed_target_json |
| dim_combat_scenario | 一个时期的战斗配置；scenario_id | combat_config_id,period_id,hp,base_def,shield_def_bonus,shield_dr,theta,kappa,uptime,support_gain_scale,source_kind |
| dim_reference_subject | 一条预注册参考轨迹；reference_subject_id | benchmark_id,anchor_unit_id,investment_plan,gear_policy,support_policy,origin_day,budget_description |
| dim_team_snapshot | 一份不可变实际队伍配置；team_id | anchor_unit_id,neutral_unit_id,support_id,component_builds_json,build_hash；team_id为规范化配置摘要 |
| dim_search_candidate | 一个网格位置；(experiment_id,candidate_id) | lambda_value,support_gain_scale |
| dim_perturbation | 一组假设扰动；(experiment_id,perturbation_id) | override_json,label；基线固定为P0 |
| search_run_map | 一个候选/扰动/重复的运行映射；(experiment_id,candidate_id,perturbation_id,split,replicate_id) | config_id,run_id |

角色资产沿用unit_id，对应专武使用公共契约的`unit_id#signature`；资产类型与ID共同区分命名空间，非战斗类别另存类别计数。catalog_id与timeline_id都进入config。reference_subject固定的是干预/配置方案，不能在结果出来后按“最终有支援”重新分组。

candidate_id只表示固定(λ,a)网格位置；扰动改变config_id但不改变candidate_id。映射与汇总必须同时保留这两个身份，不能按config_id误把一次扰动当作新的网格位置。

### 10.2 公开样本表

| 表 | 粒度 / 主键 | 必需字段 |
| --- | --- | --- |
| obs_battle | 一条源战斗记录；battle_id | source_file_id,source_row_locator,snapshot_id,local_uid,collection_period,combat_season,mode,stage_id,scoring_regime,score_value,clear_time_seconds,valid_clear,is_eligible,exclusion_reason |
| obs_team_member | 一个队伍槽位；(battle_id,team_slot) | unit_id,known_cinema,known_weapon,build_record_id,build_missing_reason |
| obs_build | 一份展示配装；build_record_id | source_file_id,snapshot_id,local_uid,unit_id,build_json,coverage_status |
| obs_coverage | 一期/模式/关卡覆盖；(source_snapshot_id,combat_season,mode,stage_id) | raw_records,eligible_records,unique_players,known_build_players,scoring_regime,status |

raw来源没有稳定battle_id时，用源文件摘要+原行定位生成，不将重复上报自动当重试样本。不同快照重叠的无法去重记录要另标，不盲目合并分母。

### 10.3 固定基准与模拟明细

| 表 | 粒度 / 主键 | 必需字段 |
| --- | --- | --- |
| fact_fixed_benchmark_panel | 一参考主体一期一场景；(benchmark_id,reference_subject_id,period_id,scenario_id) | engine_kind,anchor_unit_id,team_id,build_hash,support_id,cinema,has_weapon,disc_mult,B_ref,shield_damage_share,effective_damage,clear_index,is_full_star,eval_status,first_kill_seconds,coverage_status |
| fact_asset_event | 一次资产变动；(run_id,asset_event_id) | player_id,period_id,event_day,banner_id,pull_event_id,asset_id,event_kind,copies_delta |
| fact_ownership | 一玩家一期末一资产；(run_id,player_id,t_idx,asset_id) | first_acquired_day,copies,cinema,has_weapon,disc_actual,is_combat_eligible |
| fact_player_roster_panel | 一玩家一期一主输出一评估阶段；(run_id,player_id,t_idx,unit_id,eval_phase) | scenario_id,team_id,support_id,cinema,has_weapon,disc_actual,build_hash,clear_index,is_full_star,is_deployed_main,has_active_support,active_start,eval_status |
| fact_decision_log | 一次下池决策；(run_id,player_id,t_idx,banner_id,decision_slot) | target_path,CI_pre,available_pulls,remaining_paid_cap,expected_gain,expected_pulls,expected_topup_pulls,utility,p_enter,did_enter,skip_reason,planner_version |
| fact_pull_event | 一次实际抽取；(run_id,player_id,t_idx,banner_id,pool_kind,draw_slot) | pull_event_id,pity_group,target_stage,p_S,is_S,is_UP,result_asset_id,pity_before,pity_after,guaranteed_before,guaranteed_after,paid_cash_event_id |
| fact_resource_event | 一次钱包记账；(run_id,resource_event_id) | player_id,t_idx,event_day,provenance,earned_period,event_kind,delta_minor,pull_event_id,cash_event_id |
| fact_wallet_snapshot | 一来源批次余额；(run_id,player_id,t_idx,phase,provenance,earned_period) | balance_minor |
| fact_cash_event | 一次现金购买；(run_id,cash_event_id) | player_id,t_idx,event_day,purchase_kind,amount_fen,granted_credit_minor,pull_event_id |
| fact_pity_snapshot | 一共享保底组快照；(run_id,player_id,t_idx,pity_group,phase) | pity,guaranteed |
| fact_player_lifecycle | 一玩家一期；(run_id,player_id,t_idx) | player_tier,active_start,active_end,churn_this_period,CI_pre,CI_post,is_full_star_post,h_before,h_after,F_before,F_after,failure_span,paid_cap,paid_used,budget_utilization_rate,p_churn |
| fact_frozen_state | 一玩家退出时冻结；(run_id,player_id) | churn_period,freeze_day,freeze_build_hash,frozen_state_json |
| fact_unit_cohort | 首次获取队列成员；(run_id,unit_id,acquisition_period,player_id) | acquisition_day,cohort_id |
| agg_player_period | 一玩家一期对账宽表；(run_id,player_id,t_idx) | player_tier,active_start,active_end,CI_pre,CI_post,sub_revenue_fen,topup_revenue_fen,total_pulls,curr_free_used,hoarded_free_used,sub_pulls_used,topup_pulls_used,ending_free_minor,ending_sub_minor |

eval_phase枚举 `pre / post / frozen_post`。每期对所有已持有主输出计算，不只写出战者；支援在ownership及队伍字段体现，不伪造支援单独通关指数。`is_deployed_main`只对实际全库存最佳主输出为1。

活跃玩家的pre只含决策前持有角色，新出货出现在post。非活跃持有者的post保留CI=NULL与eval_status=inactive；冻结情景另写frozen_post，不覆盖实际状态。计算失败记error或unsupported，不悄悄当低战力。

fact_pull_event另设 `UNIQUE(run_id,pull_event_id)`；资产、资源、现金表中的抽取引用统一使用这两个字段作为外键。其他事件同理以(run_id,event_id)引用，不能只用局部事件号。所有带run_id的表引用run_manifest；观测表的source_file_id引用source_manifest。

每个主输出锚定后分别求最佳可行支援搭配；这些最优队伍是潜在表现，不能声称可在多关同时复用同一支援。

### 10.4 汇总表

- `agg_obs_character_period`：快照/实际赛季/模式/关卡/投资档位/角色，保存分子分母和成绩分布。
- `agg_benchmark_pairs`：预注册配对ID、时期、改变因素、基线CI、干预CI、绝对/相对差值与成本差。
- `agg_unit_cohort_availability`：run、角色、首次获取期、评估期的三口径率、分母、退出占比、新增获取数。
- `agg_revenue_structure`：run、时期、banner的直购现金与四来源抽数；订阅现金单列时期项，不强行归属一个banner。
- `agg_cohort_cash_retention`：run、初始预算层、时期，期初/期末留存、固定分母累计人均现金、库存和消费。
- `agg_search_results`：主键(experiment_id,candidate_id,perturbation_id,split,replicate_id)，保留config_id/run_id、两目标及相对基准差值。
- `agg_seed_robustness`：候选/扰动/split粒度的均值、标准差、范围和非支配入选频率。
- `agg_rank_stability`：experiment/perturbation/比较协议粒度的rho_rev、rho_ret；相关系数基于整个25点集合，不能当候选自身的统计。
- `agg_parameter_sensitivity`：痛感等扰动、稳定candidate_id、复核目标及相对变化；不与随机误差混表。

现金、抽取、生命周期分别先汇总到(run,player,t)，再关联。禁止直接把现金表与多行角色面板关联后SUM。若按角色归因现金，另建权重表且权重和为1；默认不做这种归因。

SQL可在上述粒度使用CTE、LAG和累计SUM/滚动AVG；所有窗口PARTITION必须含run与实际业务分组，不能跨种子、跨配置累计。增长率基数为0时记NULL并给原因，不用无穷大制造增长。

## 更新与验收

修改本模块时先查看[影响矩阵](../change_guide.md)，更新对应配置/契约版本，并执行 `python tools/design_docs.py sync` 和 `python tools/design_docs.py check`。这两条仅维护设计资料；科学验证仍按[验证模块](validation.md)实施。模块输入输出字段不得在此复制出与[公共契约](contracts.md)或[数据库契约](storage.md)相冲突的定义。

