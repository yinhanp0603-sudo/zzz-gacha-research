# V1基线默认参数视图（自动生成）

> 本视图来自 `config/experiment.json` 及其模块JSON；修改这些JSON后运行 `python tools/design_docs.py sync`。本文件不是参数维护源。

V2 已实现并完成实证接入与跨种子复核。当前研究使用独立的[V2配置入口](../../config/research_v2/experiment.json)，公式增量见[V2设计](research_v2.md)，本表保留V1基线参数，不代表当前主实验。参数中的教学假设不表示官方规则或真实玩家参数。

## combat — M-COMBAT

维护文件：[config/modules/combat.json](../../config/modules/combat.json)

| 参数路径 | 当前值 |
| --- | --- |
| `schema_version` | `"1.0.0"` |
| `model_version` | `"proxy_v1"` |
| `reference.attacker_level` | `60` |
| `reference.attacker_level_coefficient` | `794` |
| `reference.enemy_defense` | `952.8` |
| `reference.hp_index` | `100` |
| `reference.time_limit_seconds` | `120` |
| `gear_growth.max_bonus` | `0.16` |
| `gear_growth.time_constant_days` | `84` |
| `investment.cinema_multipliers` | `[1,1.12,1.25]` |
| `investment.signature_weapon_multiplier` | `1.15` |
| `support.gains.S_A` | `0.35` |
| `support.gains.S_B` | `0.45` |
| `support.default_gain_scale` | `1` |
| `shield.defense_bonus` | `0.8` |
| `shield.damage_reduction` | `0.25` |
| `shield.alignment_values` | `[0.7,1,1.3]` |
| `shield.default_alignment` | `1` |
| `default_penetration.ratio` | `0` |
| `default_penetration.flat` | `0` |
| `default_defense_reduction.ratio` | `0` |
| `default_defense_reduction.flat` | `0` |

## gacha — M-GACHA

维护文件：[config/modules/gacha.json](../../config/modules/gacha.json)

| 参数路径 | 当前值 |
| --- | --- |
| `schema_version` | `"1.0.0"` |
| `model_version` | `"gacha_v1"` |
| `curve_version` | `"assumed_empirical_v1"` |
| `pools.character.base_s_probability` | `0.006` |
| `pools.character.soft_pity_start` | `74` |
| `pools.character.hard_pity` | `90` |
| `pools.character.soft_increment` | `0.06` |
| `pools.character.up_given_s` | `0.5` |
| `pools.character.pity_group` | `"limited_character"` |
| `pools.weapon.base_s_probability` | `0.01` |
| `pools.weapon.soft_pity_start` | `65` |
| `pools.weapon.hard_pity` | `80` |
| `pools.weapon.soft_increment` | `0.06` |
| `pools.weapon.up_given_s` | `0.75` |
| `pools.weapon.pity_group` | `"limited_weapon"` |
| `target_paths.0+0` | `["body"]` |
| `target_paths.0+1` | `["body","signature_weapon"]` |
| `target_paths.2+1` | `["body","signature_weapon","cinema_1","cinema_2"]` |
| `target_paths.support_body` | `["body"]` |
| `offrate_combat_enabled` | `false` |
| `non_s_result` | `"NON_S"` |

## economy — M-WALLET

维护文件：[config/modules/economy.json](../../config/modules/economy.json)

| 参数路径 | 当前值 |
| --- | --- |
| `schema_version` | `"1.0.0"` |
| `model_version` | `"wallet_v1"` |
| `credit_minor_per_pull` | `160` |
| `standard_span_days` | `42` |
| `free_pulls_per_standard_span` | `85` |
| `subscription.renewal_days` | `42` |
| `subscription.price_fen` | `9800` |
| `subscription.grant_pulls_immediately` | `40` |
| `subscription.unused_credit_rollover` | `true` |
| `topup.price_fen_per_pull` | `1600` |
| `topup.instant_whole_pull_only` | `true` |
| `topup.balance_rollover` | `false` |
| `spend_priority` | `["historical_free_fifo","current_free","subscription_fifo","instant_topup"]` |
| `allow_mixed_existing_provenance_payment` | `true` |
| `paid_budget_rollover` | `false` |

## population — M-CONFIG

维护文件：[config/modules/population.json](../../config/modules/population.json)

| 参数路径 | 当前值 |
| --- | --- |
| `schema_version` | `"1.0.0"` |
| `model_version` | `"population_v1"` |
| `n_players` | `3000` |
| `tiers[0].id` | `"Budget_0"` |
| `tiers[0].share` | `0.4` |
| `tiers[0].paid_pulls_per_standard_span` | `0` |
| `tiers[0].subscribed` | `false` |
| `tiers[1].id` | `"Budget_40"` |
| `tiers[1].share` | `0.4` |
| `tiers[1].paid_pulls_per_standard_span` | `40` |
| `tiers[1].subscribed` | `true` |
| `tiers[2].id` | `"Budget_160"` |
| `tiers[2].share` | `0.15` |
| `tiers[2].paid_pulls_per_standard_span` | `160` |
| `tiers[2].subscribed` | `true` |
| `tiers[3].id` | `"Budget_500"` |
| `tiers[3].share` | `0.05` |
| `tiers[3].paid_pulls_per_standard_span` | `500` |
| `tiers[3].subscribed` | `true` |
| `core_retention_tier` | `"Budget_40"` |
| `allow_new_players` | `false` |
| `allow_return` | `false` |

## behavior — M-BEHAVIOR

维护文件：[config/modules/behavior.json](../../config/modules/behavior.json)

| 参数路径 | 当前值 |
| --- | --- |
| `schema_version` | `"1.0.0"` |
| `model_version` | `"behavior_v1"` |
| `comfort_threshold` | `0.95` |
| `full_star_threshold` | `1` |
| `temperature` | `0.03` |
| `utility.beta_gain` | `4` |
| `utility.beta_comfort` | `2` |
| `utility.beta_hoarding` | `2` |
| `utility.beta_resource_cost` | `1` |
| `utility.beta_paid_cost` | `2` |
| `tier_policy.Budget_0.alpha` | `-3` |
| `tier_policy.Budget_0.initial_hoarding` | `0.5` |
| `tier_policy.Budget_0.dps_target` | `"0+1"` |
| `tier_policy.Budget_40.alpha` | `-2.5` |
| `tier_policy.Budget_40.initial_hoarding` | `0.4` |
| `tier_policy.Budget_40.dps_target` | `"0+1"` |
| `tier_policy.Budget_160.alpha` | `-2` |
| `tier_policy.Budget_160.initial_hoarding` | `0.3` |
| `tier_policy.Budget_160.dps_target` | `"2+1"` |
| `tier_policy.Budget_500.alpha` | `-1` |
| `tier_policy.Budget_500.initial_hoarding` | `0.05` |
| `tier_policy.Budget_500.dps_target` | `"2+1"` |
| `support_target` | `"support_body"` |
| `hoarding.decay_base` | `0.85` |
| `hoarding.gap_weight` | `0.25` |
| `hoarding.success_recovery` | `0.1` |
| `hoarding.min` | `0` |
| `hoarding.max` | `1` |
| `fatigue.decay_rate` | `0.25` |
| `fatigue.gap_weight` | `2.0` |
| `fatigue.failure_weight` | `0.8` |
| `fatigue.failure_span_cap` | `3` |
| `fatigue.budget_weight` | `0.2` |
| `fatigue.success_recovery` | `0.3` |
| `fatigue.min` | `0` |
| `fatigue.max` | `5` |
| `churn.base_hazard` | `0.005` |
| `churn.fatigue_sensitivity` | `1.5` |
| `initial.fatigue` | `0` |
| `initial.failure_span` | `0` |

## scenarios — M-CONFIG

维护文件：[config/modules/scenarios.json](../../config/modules/scenarios.json)

| 参数路径 | 当前值 |
| --- | --- |
| `schema_version` | `"1.0.0"` |
| `model_version` | `"scenario_v1"` |
| `timeline.kind` | `"synthetic_relative"` |
| `timeline.n_periods` | `10` |
| `timeline.days_per_period` | `42` |
| `timeline.label_prefix` | `"P"` |
| `timeline.evaluation` | `"period_end"` |
| `initial_assets.starter_dps.B_ref` | `110` |
| `initial_assets.starter_dps.pierce_share` | `0` |
| `initial_assets.starter_dps.cinema` | `0` |
| `initial_assets.starter_dps.signature_weapon` | `false` |
| `initial_assets.starter_dps.acquired_day` | `0` |
| `initial_assets.starter_dps.disc_multiplier` | `1` |
| `initial_assets.neutral_units` | `["neutral_stun","neutral_flex"]` |
| `product_power.B_ref_first` | `126.875` |
| `product_power.growth_per_debut_step` | `0.19` |
| `events[0].t` | `1` |
| `events[0].unit_id` | `"DPS_01"` |
| `events[0].role` | `"normal"` |
| `events[0].theta` | `0` |
| `events[0].uptime` | `1` |
| `events[0].banner_id` | `"SYN_P01_A"` |
| `events[0].event_order` | `1` |
| `events[1].t` | `2` |
| `events[1].unit_id` | `"DPS_02"` |
| `events[1].role` | `"normal"` |
| `events[1].theta` | `0.05` |
| `events[1].uptime` | `0.98` |
| `events[1].banner_id` | `"SYN_P02_A"` |
| `events[1].event_order` | `1` |
| `events[2].t` | `3` |
| `events[2].unit_id` | `"DPS_03"` |
| `events[2].role` | `"piercing"` |
| `events[2].theta` | `0.1` |
| `events[2].uptime` | `0.95` |
| `events[2].banner_id` | `"SYN_P03_A"` |
| `events[2].event_order` | `1` |
| `events[3].t` | `4` |
| `events[3].unit_id` | `"S_A"` |
| `events[3].role` | `"support"` |
| `events[3].theta` | `0.15` |
| `events[3].uptime` | `0.95` |
| `events[3].banner_id` | `"SYN_P04_A"` |
| `events[3].event_order` | `1` |
| `events[4].t` | `5` |
| `events[4].unit_id` | `"DPS_05"` |
| `events[4].role` | `"piercing"` |
| `events[4].theta` | `0.45` |
| `events[4].uptime` | `0.9` |
| `events[4].banner_id` | `"SYN_P05_A"` |
| `events[4].event_order` | `1` |
| `events[5].t` | `6` |
| `events[5].unit_id` | `"DPS_06"` |
| `events[5].role` | `"normal"` |
| `events[5].theta` | `0.4` |
| `events[5].uptime` | `0.9` |
| `events[5].banner_id` | `"SYN_P06_A"` |
| `events[5].event_order` | `1` |
| `events[6].t` | `7` |
| `events[6].unit_id` | `"S_B"` |
| `events[6].role` | `"support"` |
| `events[6].theta` | `0.45` |
| `events[6].uptime` | `0.9` |
| `events[6].banner_id` | `"SYN_P07_A"` |
| `events[6].event_order` | `1` |
| `events[7].t` | `8` |
| `events[7].unit_id` | `"DPS_08"` |
| `events[7].role` | `"piercing"` |
| `events[7].theta` | `0.4` |
| `events[7].uptime` | `0.88` |
| `events[7].banner_id` | `"SYN_P08_A"` |
| `events[7].event_order` | `1` |
| `events[8].t` | `9` |
| `events[8].unit_id` | `"DPS_09"` |
| `events[8].role` | `"normal"` |
| `events[8].theta` | `0.45` |
| `events[8].uptime` | `0.88` |
| `events[8].banner_id` | `"SYN_P09_A"` |
| `events[8].event_order` | `1` |
| `events[9].t` | `10` |
| `events[9].unit_id` | `"DPS_10"` |
| `events[9].role` | `"normal"` |
| `events[9].theta` | `0.4` |
| `events[9].uptime` | `0.88` |
| `events[9].banner_id` | `"SYN_P10_A"` |
| `events[9].event_order` | `1` |
| `reference_scenarios.Ref_HP.hp_growth` | `0.18` |
| `reference_scenarios.Ref_HP.theta_mode` | `"constant"` |
| `reference_scenarios.Ref_HP.theta_constant` | `0.1` |
| `reference_scenarios.Ref_HP.theta_scale` | `1` |
| `reference_scenarios.Ref_HP.support_gain_scale` | `1` |
| `reference_scenarios.Ref_Mechanism.hp_growth` | `0.12` |
| `reference_scenarios.Ref_Mechanism.theta_mode` | `"path"` |
| `reference_scenarios.Ref_Mechanism.theta_scale` | `1` |
| `reference_scenarios.Ref_Mechanism.support_gain_scale` | `1` |
| `reference_scenarios.Ref_Support.hp_growth` | `0.12` |
| `reference_scenarios.Ref_Support.theta_mode` | `"path"` |
| `reference_scenarios.Ref_Support.theta_scale` | `0.75` |
| `reference_scenarios.Ref_Support.support_gain_scale` | `1.15` |

## randomness — M-RNG

维护文件：[config/modules/randomness.json](../../config/modules/randomness.json)

| 参数路径 | 当前值 |
| --- | --- |
| `schema_version` | `"1.0.0"` |
| `rng_version` | `"hash_counter_v1"` |
| `domain_tag` | `"zzz_research_rng_v1"` |
| `digest` | `"SHA256"` |
| `uint_bytes` | `8` |
| `byte_order` | `"big"` |
| `discard_low_bits` | `11` |
| `uniform_bits` | `53` |
| `interval` | `"[0,1)"` |
| `strategy_id_in_key` | `false` |
| `config_id_in_key` | `false` |
| `event_key_version` | `"event_keys_v1"` |
| `overflow_policy` | `"stable_key_expand_or_fail"` |

## experiments — M-SEARCH

维护文件：[config/modules/experiments.json](../../config/modules/experiments.json)

| 参数路径 | 当前值 |
| --- | --- |
| `schema_version` | `"1.0.0"` |
| `protocol_version` | `"search_v1"` |
| `base_scenario` | `"Ref_Mechanism"` |
| `grid.mechanism_strength` | `[0,0.5,1,1.5,2]` |
| `grid.support_gain_scale` | `[0.8,1,1.2,1.4,1.6]` |
| `train_seeds` | `[1101,1102,1103,1104,1105]` |
| `validation_seeds` | `[9101,9102,9103]` |
| `pain_sensitivity.parameters` | `["behavior.fatigue.gap_weight","behavior.fatigue.failure_weight","behavior.fatigue.budget_weight"]` |
| `pain_sensitivity.factors` | `[0.75,1,1.25]` |
| `pain_sensitivity.method` | `"one_factor_at_a_time"` |
| `baseline_grid_point.mechanism_strength` | `1` |
| `baseline_grid_point.support_gain_scale` | `1` |
| `grid_log_mode` | `"summary"` |
| `reference_log_mode` | `"full"` |

## V1基线执行规模（从配置推导）

- 候选点：25；主网格逻辑run：200。
- 单因素痛感扰动新增run：450；去重网格总run：650。
- 单run生命周期记录：30,000；网格总生命周期记录：19,500,000。
- 不包含另行参照、可选扩展或重跑attempt；全库存与逐抽日志按实际生成量计。
