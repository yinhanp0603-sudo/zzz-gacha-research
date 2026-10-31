-- zzz_research 物理 schema（storage.md 10.1–10.3 与 agg_player_period）
-- 约定：ID=TEXT；t/计数/布尔=INTEGER；金额=INTEGER 分；日期=ISO TEXT；天数/指数=REAL；结构化=JSON TEXT。
-- 外键全部 DEFERRABLE INITIALLY DEFERRED，以便同一事务内按任意顺序写入互相引用的事件。
-- 本文件只定义结构，不补算任何业务规则。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS fact_spending_decision (
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    player_id INTEGER NOT NULL,
    t_idx INTEGER NOT NULL,
    decision_kind TEXT NOT NULL CHECK (decision_kind IN ('subscription','topup')),
    event_slot INTEGER NOT NULL,
    banner_id TEXT,
    prior_F REAL NOT NULL,
    prior_failure_span REAL NOT NULL,
    prior_hoarding REAL NOT NULL,
    last_paid_failure INTEGER NOT NULL CHECK (last_paid_failure IN (0,1)),
    probability REAL NOT NULL CHECK (probability BETWEEN 0 AND 1),
    utility REAL,
    allowed INTEGER NOT NULL CHECK (allowed IN (0,1)),
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    planned_gain REAL,
    expected_topup REAL,
    subscription_active INTEGER NOT NULL CHECK (subscription_active IN (0,1)),
    topup_allowed INTEGER NOT NULL CHECK (topup_allowed IN (0,1)),
    reason TEXT NOT NULL,
    PRIMARY KEY (run_id,player_id,t_idx,decision_kind,event_slot)
);

CREATE TABLE IF NOT EXISTS schema_version (
    schema_name     TEXT NOT NULL PRIMARY KEY,
    version         TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_version VALUES ('zzz_research', 'storage_v1');

-- ---------------------------------------------------------------- 10.1 来源、维度与运行
CREATE TABLE IF NOT EXISTS source_manifest (
    source_file_id  TEXT NOT NULL PRIMARY KEY,
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    revision        TEXT,
    retrieved_at    TEXT NOT NULL,
    sha256          TEXT,
    byte_size       INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
    license         TEXT,
    coverage_json   TEXT,
    status          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parameter_registry (
    config_id       TEXT NOT NULL,
    parameter_name  TEXT NOT NULL,
    value_json      TEXT NOT NULL,
    unit            TEXT,
    source_kind     TEXT NOT NULL CHECK (source_kind IN ('source_code','observed','community_derived','scenario_assumed')),
    source_ref      TEXT,
    applicability   TEXT,
    rationale       TEXT,
    sensitivity_json TEXT,
    status          TEXT NOT NULL,
    PRIMARY KEY (config_id, parameter_name)
);

CREATE TABLE IF NOT EXISTS run_manifest (
    run_id          TEXT NOT NULL PRIMARY KEY,
    experiment_id   TEXT NOT NULL,
    config_id       TEXT NOT NULL,
    replicate_id    TEXT NOT NULL,
    master_seed     INTEGER NOT NULL CHECK (master_seed >= 0),
    rng_version     TEXT NOT NULL,
    algorithm_versions_json TEXT NOT NULL,
    config_json     TEXT NOT NULL,
    input_manifest_hash TEXT NOT NULL,
    code_revision   TEXT,
    environment_json TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('running','completed','failed','incomplete')),
    UNIQUE (experiment_id, config_id, replicate_id)
);

CREATE TABLE IF NOT EXISTS run_attempt (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    attempt_id      INTEGER NOT NULL CHECK (attempt_id >= 0),
    log_mode        TEXT NOT NULL CHECK (log_mode IN ('full','summary')),
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    status          TEXT NOT NULL CHECK (status IN ('running','completed','failed','incomplete')),
    error_code      TEXT,
    output_hash     TEXT,
    PRIMARY KEY (run_id, attempt_id)
);

CREATE TABLE IF NOT EXISTS stage_timing (
    run_id          TEXT NOT NULL,
    attempt_id      INTEGER NOT NULL,
    stage           TEXT NOT NULL,
    wall_seconds    REAL NOT NULL CHECK (wall_seconds >= 0),
    row_count       INTEGER CHECK (row_count IS NULL OR row_count >= 0),
    peak_memory_mb  REAL,
    measurement_method TEXT NOT NULL,
    PRIMARY KEY (run_id, attempt_id, stage),
    FOREIGN KEY (run_id, attempt_id) REFERENCES run_attempt(run_id, attempt_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS dim_period (
    timeline_id     TEXT NOT NULL,
    period_id       TEXT NOT NULL,
    t_idx           INTEGER NOT NULL,
    start_day       REAL NOT NULL,
    end_day         REAL NOT NULL CHECK (end_day >= start_day),
    evaluation_day  REAL NOT NULL,
    elapsed_days    REAL NOT NULL CHECK (elapsed_days >= 0),
    actual_start_date TEXT,
    actual_end_date TEXT,
    calendar_mode   TEXT NOT NULL,
    PRIMARY KEY (timeline_id, period_id),
    UNIQUE (timeline_id, t_idx)
);

CREATE TABLE IF NOT EXISTS dim_unit (
    catalog_id      TEXT NOT NULL,
    unit_id         TEXT NOT NULL,
    role            TEXT NOT NULL,
    debut_period    INTEGER,
    debut_day       REAL,
    B_ref           REAL CHECK (B_ref IS NULL OR B_ref >= 0),
    pierce_share    REAL CHECK (pierce_share IS NULL OR pierce_share BETWEEN 0 AND 1),
    weapon_id       TEXT,
    base_support_gain REAL CHECK (base_support_gain IS NULL OR base_support_gain >= 0),
    combat_eligible INTEGER NOT NULL CHECK (combat_eligible IN (0,1)),
    PRIMARY KEY (catalog_id, unit_id)
);

CREATE TABLE IF NOT EXISTS dim_banner (
    catalog_id      TEXT NOT NULL,
    banner_id       TEXT NOT NULL,
    period_id       TEXT NOT NULL,
    event_order     INTEGER NOT NULL,
    target_unit_id  TEXT NOT NULL,
    target_weapon_id TEXT NOT NULL,
    char_pity_group TEXT NOT NULL,
    wep_pity_group  TEXT NOT NULL,
    allowed_target_json TEXT NOT NULL,
    PRIMARY KEY (catalog_id, banner_id),
    FOREIGN KEY (catalog_id, target_unit_id) REFERENCES dim_unit(catalog_id, unit_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS dim_combat_scenario (
    scenario_id     TEXT NOT NULL PRIMARY KEY,
    combat_config_id TEXT NOT NULL,
    period_id       TEXT NOT NULL,
    hp              REAL NOT NULL CHECK (hp > 0),
    base_def        REAL NOT NULL CHECK (base_def >= 0),
    shield_def_bonus REAL NOT NULL CHECK (shield_def_bonus >= 0),
    shield_dr       REAL NOT NULL CHECK (shield_dr BETWEEN 0 AND 1),
    theta           REAL NOT NULL CHECK (theta BETWEEN 0 AND 1),
    kappa           REAL NOT NULL CHECK (kappa > 0),
    uptime          REAL NOT NULL CHECK (uptime BETWEEN 0 AND 1),
    support_gain_scale REAL NOT NULL CHECK (support_gain_scale >= 0),
    source_kind     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_reference_subject (
    reference_subject_id TEXT NOT NULL PRIMARY KEY,
    benchmark_id    TEXT NOT NULL,
    anchor_unit_id  TEXT NOT NULL,
    investment_plan TEXT NOT NULL,
    gear_policy     TEXT NOT NULL,
    support_policy  TEXT NOT NULL,
    origin_day      REAL NOT NULL,
    budget_description TEXT
);

CREATE TABLE IF NOT EXISTS dim_team_snapshot (
    team_id         TEXT NOT NULL PRIMARY KEY,
    anchor_unit_id  TEXT NOT NULL,
    neutral_unit_id TEXT NOT NULL,
    support_id      TEXT NOT NULL,           -- 无支援记 'NONE'
    component_builds_json TEXT NOT NULL,
    build_hash      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_search_candidate (
    experiment_id   TEXT NOT NULL,
    candidate_id    TEXT NOT NULL,
    lambda_value    REAL NOT NULL,
    support_gain_scale REAL NOT NULL,
    PRIMARY KEY (experiment_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS dim_perturbation (
    experiment_id   TEXT NOT NULL,
    perturbation_id TEXT NOT NULL,
    override_json   TEXT NOT NULL,
    label           TEXT NOT NULL,
    PRIMARY KEY (experiment_id, perturbation_id)
);

CREATE TABLE IF NOT EXISTS search_run_map (
    experiment_id   TEXT NOT NULL,
    candidate_id    TEXT NOT NULL,
    perturbation_id TEXT NOT NULL,
    split           TEXT NOT NULL,
    replicate_id    TEXT NOT NULL,
    config_id       TEXT NOT NULL,
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    PRIMARY KEY (experiment_id, candidate_id, perturbation_id, split, replicate_id)
);

-- ---------------------------------------------------------------- 10.2 公开样本
CREATE TABLE IF NOT EXISTS obs_battle (
    battle_id       TEXT NOT NULL PRIMARY KEY,
    source_file_id  TEXT NOT NULL REFERENCES source_manifest(source_file_id) DEFERRABLE INITIALLY DEFERRED,
    source_row_locator TEXT NOT NULL,
    snapshot_id     TEXT NOT NULL,
    local_uid       TEXT,
    collection_period TEXT,
    combat_season   TEXT,
    mode            TEXT,
    stage_id        TEXT,
    scoring_regime  TEXT,
    score_value     REAL,
    clear_time_seconds REAL,
    valid_clear     INTEGER CHECK (valid_clear IS NULL OR valid_clear IN (0,1)),
    is_eligible     INTEGER NOT NULL CHECK (is_eligible IN (0,1)),
    exclusion_reason TEXT
);

CREATE TABLE IF NOT EXISTS obs_team_member (
    battle_id       TEXT NOT NULL REFERENCES obs_battle(battle_id) DEFERRABLE INITIALLY DEFERRED,
    team_slot       INTEGER NOT NULL,
    unit_id         TEXT,
    known_cinema    INTEGER,
    known_weapon    TEXT,
    build_record_id TEXT,
    build_missing_reason TEXT,
    PRIMARY KEY (battle_id, team_slot)
);

CREATE TABLE IF NOT EXISTS obs_build (
    build_record_id TEXT NOT NULL PRIMARY KEY,
    source_file_id  TEXT NOT NULL REFERENCES source_manifest(source_file_id) DEFERRABLE INITIALLY DEFERRED,
    snapshot_id     TEXT NOT NULL,
    local_uid       TEXT,
    unit_id         TEXT,
    build_json      TEXT,
    coverage_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS obs_coverage (
    source_snapshot_id TEXT NOT NULL,
    combat_season   TEXT NOT NULL,
    mode            TEXT NOT NULL,
    stage_id        TEXT NOT NULL,
    raw_records     INTEGER NOT NULL CHECK (raw_records >= 0),
    eligible_records INTEGER NOT NULL CHECK (eligible_records >= 0),
    unique_players  INTEGER CHECK (unique_players IS NULL OR unique_players >= 0),
    known_build_players INTEGER,
    scoring_regime  TEXT,
    status          TEXT NOT NULL,
    PRIMARY KEY (source_snapshot_id, combat_season, mode, stage_id)
);

-- ---------------------------------------------------------------- 10.3 固定基准与模拟明细
CREATE TABLE IF NOT EXISTS fact_fixed_benchmark_panel (
    benchmark_id    TEXT NOT NULL,
    reference_subject_id TEXT NOT NULL,
    period_id       TEXT NOT NULL,
    scenario_id     TEXT NOT NULL,
    engine_kind     TEXT NOT NULL,
    anchor_unit_id  TEXT NOT NULL,
    team_id         TEXT,
    build_hash      TEXT,
    support_id      TEXT NOT NULL,
    cinema          INTEGER NOT NULL CHECK (cinema BETWEEN 0 AND 6),
    has_weapon      INTEGER NOT NULL CHECK (has_weapon IN (0,1)),
    disc_mult       REAL,
    B_ref           REAL,
    shield_damage_share REAL CHECK (shield_damage_share IS NULL OR shield_damage_share BETWEEN 0 AND 1),
    effective_damage REAL,
    clear_index     REAL,
    is_full_star    INTEGER CHECK (is_full_star IS NULL OR is_full_star IN (0,1)),
    eval_status     TEXT NOT NULL,
    first_kill_seconds REAL,
    coverage_status TEXT,
    PRIMARY KEY (benchmark_id, reference_subject_id, period_id, scenario_id)
);

CREATE TABLE IF NOT EXISTS fact_pull_event (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    player_id       INTEGER NOT NULL,
    t_idx           INTEGER NOT NULL,
    banner_id       TEXT NOT NULL,
    pool_kind       TEXT NOT NULL CHECK (pool_kind IN ('character','weapon')),
    draw_slot       INTEGER NOT NULL CHECK (draw_slot >= 0),
    pull_event_id   TEXT NOT NULL,
    pity_group      TEXT NOT NULL,
    target_stage    TEXT,
    p_S             REAL NOT NULL CHECK (p_S BETWEEN 0 AND 1),
    is_S            INTEGER NOT NULL CHECK (is_S IN (0,1)),
    is_UP           INTEGER NOT NULL CHECK (is_UP IN (0,1)),
    result_asset_id TEXT NOT NULL,
    pity_before     INTEGER NOT NULL CHECK (pity_before >= 0),
    pity_after      INTEGER NOT NULL CHECK (pity_after >= 0),
    guaranteed_before INTEGER NOT NULL CHECK (guaranteed_before IN (0,1)),
    guaranteed_after INTEGER NOT NULL CHECK (guaranteed_after IN (0,1)),
    paid_cash_event_id TEXT,
    PRIMARY KEY (run_id, player_id, t_idx, banner_id, pool_kind, draw_slot),
    UNIQUE (run_id, pull_event_id),
    CHECK (is_UP <= is_S),
    FOREIGN KEY (run_id, paid_cash_event_id) REFERENCES fact_cash_event(run_id, cash_event_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS fact_asset_event (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    asset_event_id  TEXT NOT NULL,
    player_id       INTEGER NOT NULL,
    period_id       TEXT NOT NULL,
    event_day       REAL NOT NULL,
    banner_id       TEXT,
    pull_event_id   TEXT,
    asset_kind      TEXT NOT NULL CHECK (asset_kind IN ('character','weapon','category')),
    asset_id        TEXT NOT NULL,
    event_kind      TEXT NOT NULL CHECK (event_kind IN ('initial','pull')),
    copies_delta    INTEGER NOT NULL CHECK (copies_delta >= 0),
    PRIMARY KEY (run_id, asset_event_id),
    FOREIGN KEY (run_id, pull_event_id) REFERENCES fact_pull_event(run_id, pull_event_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS fact_ownership (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    player_id       INTEGER NOT NULL,
    t_idx           INTEGER NOT NULL,
    asset_kind      TEXT NOT NULL CHECK (asset_kind IN ('character','weapon','category')),
    asset_id        TEXT NOT NULL,
    first_acquired_day REAL,
    copies          INTEGER NOT NULL CHECK (copies >= 0),
    cinema          INTEGER CHECK (cinema IS NULL OR cinema BETWEEN 0 AND 6),
    has_weapon      INTEGER CHECK (has_weapon IS NULL OR has_weapon IN (0,1)),
    disc_actual     REAL,
    is_combat_eligible INTEGER NOT NULL CHECK (is_combat_eligible IN (0,1)),
    PRIMARY KEY (run_id, player_id, t_idx, asset_kind, asset_id)
);

CREATE TABLE IF NOT EXISTS fact_player_roster_panel (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    player_id       INTEGER NOT NULL,
    t_idx           INTEGER NOT NULL,
    unit_id         TEXT NOT NULL,
    eval_phase      TEXT NOT NULL CHECK (eval_phase IN ('pre','post','frozen_post')),
    scenario_id     TEXT NOT NULL REFERENCES dim_combat_scenario(scenario_id) DEFERRABLE INITIALLY DEFERRED,
    team_id         TEXT REFERENCES dim_team_snapshot(team_id) DEFERRABLE INITIALLY DEFERRED,
    support_id      TEXT,
    cinema          INTEGER CHECK (cinema IS NULL OR cinema BETWEEN 0 AND 6),
    has_weapon      INTEGER CHECK (has_weapon IS NULL OR has_weapon IN (0,1)),
    disc_actual     REAL,
    build_hash      TEXT,
    clear_index     REAL,
    is_full_star    INTEGER CHECK (is_full_star IS NULL OR is_full_star IN (0,1)),
    is_deployed_main INTEGER NOT NULL CHECK (is_deployed_main IN (0,1)),
    has_active_support INTEGER NOT NULL CHECK (has_active_support IN (0,1)),
    active_start    INTEGER NOT NULL CHECK (active_start IN (0,1)),
    eval_status     TEXT NOT NULL,
    PRIMARY KEY (run_id, player_id, t_idx, unit_id, eval_phase)
);

CREATE TABLE IF NOT EXISTS fact_decision_log (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    player_id       INTEGER NOT NULL,
    t_idx           INTEGER NOT NULL,
    banner_id       TEXT NOT NULL,
    decision_slot   INTEGER NOT NULL CHECK (decision_slot >= 0),
    target_path     TEXT NOT NULL,
    CI_pre          REAL,
    available_pulls INTEGER NOT NULL CHECK (available_pulls >= 0),
    remaining_paid_cap INTEGER NOT NULL CHECK (remaining_paid_cap >= 0),
    expected_gain   REAL,
    expected_pulls  REAL,
    expected_topup_pulls REAL,
    utility         REAL,
    p_enter         REAL CHECK (p_enter IS NULL OR p_enter BETWEEN 0 AND 1),
    did_enter       INTEGER NOT NULL CHECK (did_enter IN (0,1)),
    skip_reason     TEXT,
    planner_version TEXT NOT NULL,
    PRIMARY KEY (run_id, player_id, t_idx, banner_id, decision_slot)
);

CREATE TABLE IF NOT EXISTS fact_cash_event (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    cash_event_id   TEXT NOT NULL,
    player_id       INTEGER NOT NULL,
    t_idx           INTEGER NOT NULL,
    event_day       REAL NOT NULL,
    purchase_kind   TEXT NOT NULL,
    amount_fen      INTEGER NOT NULL CHECK (amount_fen >= 0),
    granted_credit_minor INTEGER NOT NULL CHECK (granted_credit_minor >= 0),
    pull_event_id   TEXT,
    PRIMARY KEY (run_id, cash_event_id),
    FOREIGN KEY (run_id, pull_event_id) REFERENCES fact_pull_event(run_id, pull_event_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS fact_resource_event (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    resource_event_id TEXT NOT NULL,
    player_id       INTEGER NOT NULL,
    t_idx           INTEGER NOT NULL,
    event_day       REAL NOT NULL,
    batch_id        TEXT NOT NULL,
    provenance      TEXT NOT NULL CHECK (provenance IN ('historical_free','current_free','subscription','instant_topup')),
    earned_period   INTEGER NOT NULL,
    event_kind      TEXT NOT NULL CHECK (event_kind IN ('grant','spend','expire')),
    delta_minor     INTEGER NOT NULL,
    pull_event_id   TEXT,
    cash_event_id   TEXT,
    PRIMARY KEY (run_id, resource_event_id),
    FOREIGN KEY (run_id, pull_event_id) REFERENCES fact_pull_event(run_id, pull_event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (run_id, cash_event_id) REFERENCES fact_cash_event(run_id, cash_event_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS fact_wallet_snapshot (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    player_id       INTEGER NOT NULL,
    t_idx           INTEGER NOT NULL,
    phase           TEXT NOT NULL CHECK (phase IN ('start','end')),
    provenance      TEXT NOT NULL CHECK (provenance IN ('historical_free','current_free','subscription','instant_topup')),
    earned_period   INTEGER NOT NULL,
    balance_minor   INTEGER NOT NULL CHECK (balance_minor >= 0),
    PRIMARY KEY (run_id, player_id, t_idx, phase, provenance, earned_period)
);

CREATE TABLE IF NOT EXISTS fact_pity_snapshot (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    player_id       INTEGER NOT NULL,
    t_idx           INTEGER NOT NULL,
    pity_group      TEXT NOT NULL,
    phase           TEXT NOT NULL CHECK (phase IN ('start','end')),
    pity            INTEGER NOT NULL CHECK (pity >= 0),
    guaranteed      INTEGER NOT NULL CHECK (guaranteed IN (0,1)),
    PRIMARY KEY (run_id, player_id, t_idx, pity_group, phase)
);

CREATE TABLE IF NOT EXISTS fact_player_lifecycle (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    player_id       INTEGER NOT NULL,
    t_idx           INTEGER NOT NULL,
    player_tier     TEXT NOT NULL,
    active_start    INTEGER NOT NULL CHECK (active_start IN (0,1)),
    active_end      INTEGER NOT NULL CHECK (active_end IN (0,1)),
    churn_this_period INTEGER NOT NULL CHECK (churn_this_period IN (0,1)),
    CI_pre          REAL,
    CI_post         REAL,
    is_full_star_post INTEGER CHECK (is_full_star_post IS NULL OR is_full_star_post IN (0,1)),
    h_before        REAL,
    h_after         REAL,
    F_before        REAL,
    F_after         REAL,
    failure_span    REAL,
    paid_cap        INTEGER NOT NULL CHECK (paid_cap >= 0),
    paid_used       INTEGER NOT NULL CHECK (paid_used >= 0),
    budget_utilization_rate REAL,
    p_churn         REAL CHECK (p_churn IS NULL OR p_churn BETWEEN 0 AND 1),
    PRIMARY KEY (run_id, player_id, t_idx),
    CHECK (active_end <= active_start),
    CHECK (churn_this_period = (active_start - active_end))
);

CREATE TABLE IF NOT EXISTS fact_frozen_state (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    player_id       INTEGER NOT NULL,
    churn_period    INTEGER NOT NULL,
    freeze_day      REAL NOT NULL,
    freeze_build_hash TEXT NOT NULL,
    frozen_state_json TEXT NOT NULL,
    PRIMARY KEY (run_id, player_id)
);

CREATE TABLE IF NOT EXISTS fact_unit_cohort (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    unit_id         TEXT NOT NULL,
    acquisition_period INTEGER NOT NULL,
    player_id       INTEGER NOT NULL,
    acquisition_day REAL NOT NULL,
    cohort_id       TEXT NOT NULL,
    PRIMARY KEY (run_id, unit_id, acquisition_period, player_id)
);

-- ---------------------------------------------------------------- 10.4 对账宽表
CREATE TABLE IF NOT EXISTS agg_player_period (
    run_id          TEXT NOT NULL REFERENCES run_manifest(run_id) DEFERRABLE INITIALLY DEFERRED,
    player_id       INTEGER NOT NULL,
    t_idx           INTEGER NOT NULL,
    player_tier     TEXT NOT NULL,
    active_start    INTEGER NOT NULL CHECK (active_start IN (0,1)),
    active_end      INTEGER NOT NULL CHECK (active_end IN (0,1)),
    CI_pre          REAL,
    CI_post         REAL,
    sub_revenue_fen INTEGER NOT NULL CHECK (sub_revenue_fen >= 0),
    topup_revenue_fen INTEGER NOT NULL CHECK (topup_revenue_fen >= 0),
    total_pulls     INTEGER NOT NULL CHECK (total_pulls >= 0),
    curr_free_used  REAL NOT NULL CHECK (curr_free_used >= 0),
    hoarded_free_used REAL NOT NULL CHECK (hoarded_free_used >= 0),
    sub_pulls_used  REAL NOT NULL CHECK (sub_pulls_used >= 0),
    topup_pulls_used INTEGER NOT NULL CHECK (topup_pulls_used >= 0),
    ending_free_minor INTEGER NOT NULL CHECK (ending_free_minor >= 0),
    ending_sub_minor INTEGER NOT NULL CHECK (ending_sub_minor >= 0),
    PRIMARY KEY (run_id, player_id, t_idx)
);

CREATE INDEX IF NOT EXISTS ix_pull_player ON fact_pull_event(run_id, player_id, t_idx);
CREATE INDEX IF NOT EXISTS ix_res_player ON fact_resource_event(run_id, player_id, t_idx);
CREATE INDEX IF NOT EXISTS ix_cash_player ON fact_cash_event(run_id, player_id, t_idx);
CREATE INDEX IF NOT EXISTS ix_asset_player ON fact_asset_event(run_id, player_id);
CREATE INDEX IF NOT EXISTS ix_panel_phase ON fact_player_roster_panel(run_id, t_idx, eval_phase);

-- 外键子表侧索引（仅物理性能：延迟外键在插入父行时按子表列查找，无索引会退化为全表扫描）
CREATE INDEX IF NOT EXISTS ix_fk_asset_pull ON fact_asset_event(run_id, pull_event_id);
CREATE INDEX IF NOT EXISTS ix_fk_res_pull ON fact_resource_event(run_id, pull_event_id);
CREATE INDEX IF NOT EXISTS ix_fk_res_cash ON fact_resource_event(run_id, cash_event_id);
CREATE INDEX IF NOT EXISTS ix_fk_cash_pull ON fact_cash_event(run_id, pull_event_id);
CREATE INDEX IF NOT EXISTS ix_fk_pull_cash ON fact_pull_event(run_id, paid_cash_event_id);
CREATE INDEX IF NOT EXISTS ix_fk_panel_scenario ON fact_player_roster_panel(scenario_id);
CREATE INDEX IF NOT EXISTS ix_fk_panel_team ON fact_player_roster_panel(team_id);
CREATE INDEX IF NOT EXISTS ix_fk_banner_unit ON dim_banner(catalog_id, target_unit_id);
CREATE INDEX IF NOT EXISTS ix_fk_search_run ON search_run_map(run_id);
CREATE INDEX IF NOT EXISTS ix_fk_obs_battle_src ON obs_battle(source_file_id);
CREATE INDEX IF NOT EXISTS ix_fk_obs_build_src ON obs_build(source_file_id);
