-- storage.md 10.4 汇总表（搜索/统计产物；依赖 schema.sql 已建立的维度与运行表）
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS experiment_manifest (
    experiment_id   TEXT NOT NULL PRIMARY KEY,
    protocol_version TEXT NOT NULL,
    base_config_id  TEXT NOT NULL,
    scale_label     TEXT NOT NULL,
    common_overrides_json TEXT NOT NULL,
    train_seeds_json TEXT NOT NULL,
    validation_seeds_json TEXT NOT NULL,
    log_mode        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('running','completed','failed','incomplete'))
);

CREATE TABLE IF NOT EXISTS agg_search_results (
    experiment_id   TEXT NOT NULL,
    candidate_id    TEXT NOT NULL,
    perturbation_id TEXT NOT NULL,
    split           TEXT NOT NULL CHECK (split IN ('train','validation')),
    replicate_id    TEXT NOT NULL,
    master_seed     INTEGER NOT NULL,
    config_id       TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    total_revenue_fen INTEGER NOT NULL CHECK (total_revenue_fen >= 0),
    core_retention_end REAL CHECK (core_retention_end IS NULL OR core_retention_end BETWEEN 0 AND 1),
    core_initial    INTEGER NOT NULL,
    delta_revenue_vs_base INTEGER,
    delta_retention_vs_base REAL,
    nondominated    INTEGER NOT NULL CHECK (nondominated IN (0,1)),
    PRIMARY KEY (experiment_id, candidate_id, perturbation_id, split, replicate_id)
);

CREATE TABLE IF NOT EXISTS agg_seed_robustness (
    experiment_id   TEXT NOT NULL,
    candidate_id    TEXT NOT NULL,
    perturbation_id TEXT NOT NULL,
    split           TEXT NOT NULL,
    n_seeds         INTEGER NOT NULL,
    revenue_mean    REAL, revenue_sd REAL, revenue_min REAL, revenue_max REAL,
    retention_mean  REAL, retention_sd REAL, retention_min REAL, retention_max REAL,
    delta_revenue_mean REAL, delta_retention_mean REAL,
    inclusion_count INTEGER NOT NULL,
    inclusion_frequency REAL NOT NULL CHECK (inclusion_frequency BETWEEN 0 AND 1),
    PRIMARY KEY (experiment_id, candidate_id, perturbation_id, split)
);

CREATE TABLE IF NOT EXISTS agg_rank_stability (
    experiment_id   TEXT NOT NULL,
    perturbation_id TEXT NOT NULL,
    comparison      TEXT NOT NULL,
    n_candidates    INTEGER NOT NULL,
    rho_rev         REAL,
    rho_ret         REAL,
    PRIMARY KEY (experiment_id, perturbation_id, comparison)
);

CREATE TABLE IF NOT EXISTS agg_parameter_sensitivity (
    experiment_id   TEXT NOT NULL,
    perturbation_id TEXT NOT NULL,
    parameter_name  TEXT NOT NULL,
    factor          REAL NOT NULL,
    candidate_id    TEXT NOT NULL,
    revenue_mean    REAL,
    retention_mean  REAL,
    rel_change_revenue_vs_P0 REAL,
    rel_change_retention_vs_P0 REAL,
    nondominated_freq REAL,
    PRIMARY KEY (experiment_id, perturbation_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS agg_benchmark_pairs (
    benchmark_id    TEXT NOT NULL,
    pair_id         TEXT NOT NULL,
    period_id       TEXT,
    changed_factor  TEXT NOT NULL,
    base_label      TEXT NOT NULL,
    alt_label       TEXT NOT NULL,
    base_ci         REAL,
    alt_ci          REAL,
    abs_diff        REAL,
    rel_diff        REAL,
    cost_note       TEXT NOT NULL,
    PRIMARY KEY (benchmark_id, pair_id)
);
