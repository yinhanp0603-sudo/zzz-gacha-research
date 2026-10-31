"""Seed expansion reuses physical runs and accounts for prefix cash by product type."""

import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_research_v2_stability as RUN

from zzz_research.config import resolve_config


ENTRY = ROOT / "config/research_v2/experiment.json"


@pytest.fixture
def result(tmp_path):
    db = tmp_path / "run.sqlite"
    with sqlite3.connect(db) as con:
        con.executescript("""
            CREATE TABLE run_manifest (run_id TEXT, config_json TEXT);
            CREATE TABLE dim_period (t_idx INTEGER, period_id TEXT);
            CREATE TABLE dim_banner (catalog_id TEXT,banner_id TEXT,period_id TEXT,target_unit_id TEXT);
            CREATE TABLE dim_unit (catalog_id TEXT,unit_id TEXT,role TEXT);
            CREATE TABLE agg_player_period (run_id TEXT,player_id INTEGER,t_idx INTEGER,
                player_tier TEXT,active_end INTEGER,sub_revenue_fen INTEGER,topup_revenue_fen INTEGER,
                total_pulls INTEGER,curr_free_used REAL,hoarded_free_used REAL,sub_pulls_used REAL,
                topup_pulls_used INTEGER,ending_free_minor INTEGER,ending_sub_minor INTEGER);
        """)
        config = {"modules": {"population": {"n_players": 2, "core_retention_tier": "Budget_40"},
                              "scenarios": {"research_v2": {"observation_periods": 20}}}}
        con.execute("INSERT INTO run_manifest VALUES (?,?)", ("run20", json.dumps(config)))
        for t in range(1, 21):
            role = "support" if t in (4, 7, 14, 17) else "normal"
            con.execute("INSERT INTO dim_period VALUES (?,?)", (t, f"t{t:02d}"))
            con.execute("INSERT INTO dim_banner VALUES (?,?,?,?)", ("catalog", f"b{t}", f"t{t:02d}", f"u{t}"))
            con.execute("INSERT INTO dim_unit VALUES (?,?,?)", ("catalog", f"u{t}", role))
            for player in range(2):
                con.execute("INSERT INTO agg_player_period VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("run20", player, t, "Budget_40" if player == 0 else "Budget_500",
                     int(player == 1 or t < 15), 1000, t * 100, 8, 2, 1, 3, 2, t * 160, 320))
    return {"database": str(db), "run_id": "run20", "config_id": "physical20", "policy_id": "L1_A1",
            "cell_id": "L1_A1__combat-v2__spending-v2", "seed": 1101, "split": "train",
            "mechanism_strength": 1, "support_gain_scale": 1, "log_mode": "summary"}


def test_plan_expands_p0_seeds_only_with_one_physical_horizon():
    cfg = resolve_config(ENTRY)
    specs = RUN.build_plan(cfg)
    assert len(specs) == 200
    assert {(s.combat_variant, s.spending_variant, s.horizon, s.log_mode) for s in specs} == {
        ("v2", "v2", 20, "summary")}
    assert len({s.seed for s in specs if s.split == "train"}) == 5
    assert len({s.seed for s in specs if s.split == "validation"}) == 3
    assert len({s.policy_id for s in specs}) == 25


def test_cash_components_reconcile_and_preserve_physical_identity(result):
    rows, periods, population = RUN.read_prefix_outcomes(result)
    short, long = rows
    assert [r["horizon"] for r in rows] == [10, 20]
    assert all(r["simulation_horizon"] == 20 and r["config_id"] == "physical20" for r in rows)
    for row in rows:
        assert row["cash_yuan"] == pytest.approx(row["subscription_yuan"] +
            row["new_main_topup_yuan"] + row["support_topup_yuan"])
        assert row["cash_yuan"] == pytest.approx(sum(p["cash_fen"] for p in periods[:row["horizon"]]) / 100)
    assert short["support_topup_yuan"] == 2 * (4 + 7)
    assert long["support_topup_yuan"] == 2 * (4 + 7 + 14 + 17)
    assert short["retention_core"] == 1
    assert long["retention_core"] == 0
    assert long["retention_all"] == .5
    assert {p["player_tier"]: p["initial_players"] for p in population} == {"Budget_40": 1, "Budget_500": 1}


def test_prefix_rejects_multi_banner_cash_ambiguity(result):
    with sqlite3.connect(result["database"]) as con:
        con.execute("INSERT INTO dim_banner VALUES ('catalog','extra','t04','u4')")
    with pytest.raises(ValueError, match="恰好一个卡池"):
        RUN.read_prefix_outcomes(result)


def test_prefix_rejects_missing_or_changed_initial_cohort(result):
    with sqlite3.connect(result["database"]) as con:
        con.execute("DELETE FROM agg_player_period WHERE player_id=0 AND t_idx=12")
    with pytest.raises(ValueError, match="初始队列分母"):
        RUN.read_prefix_outcomes(result)


def test_prefix_rejects_observation_after_simulation_end(result):
    with pytest.raises(ValueError, match="观察前缀"):
        RUN.read_prefix_outcomes(result, (21,))


def test_existing_full_cache_is_reused_without_downgrading_or_writing_source(tmp_path, monkeypatch):
    spec = RUN.build_plan(resolve_config(ENTRY), [1101])[0]
    cfg = resolve_config(ENTRY, overrides=spec.overrides(200))
    cache_root, out = tmp_path / "cache", tmp_path / "new"
    cache = RUN._run_path(cache_root, spec, "Research")
    cache.parent.mkdir(parents=True)
    with sqlite3.connect(cache) as con:
        con.executescript("CREATE TABLE run_manifest(config_id TEXT,master_seed INTEGER,status TEXT);"
                          "CREATE TABLE run_attempt(attempt_id INTEGER,log_mode TEXT);")
        con.execute("INSERT INTO run_manifest VALUES (?,?,'completed')", (cfg.config_id, spec.seed))
        con.execute("INSERT INTO run_attempt VALUES (0,'full')")
    calls = []
    def original_run(entry, source, players, details):
        calls.append((source, details["log_mode"]))
        return {"database": str(cache), "reused": True}
    monkeypatch.setattr(RUN, "run_one", original_run)
    actual = RUN.resolve_cached_run(str(ENTRY), str(out), str(cache_root), 200, asdict(spec))
    assert calls == [(str(cache_root), "full")]
    assert actual["cache_source"] == "prior_experiment"
    assert not out.exists()


def test_different_population_cache_cannot_supply_current_run(tmp_path, monkeypatch):
    spec = RUN.build_plan(resolve_config(ENTRY), [1101])[0]
    cache_root, out = tmp_path / "cache", tmp_path / "new"
    cache = RUN._run_path(cache_root, spec, "Research")
    cache.parent.mkdir(parents=True)
    with sqlite3.connect(cache) as con:
        con.executescript("CREATE TABLE run_manifest(config_id TEXT,master_seed INTEGER,status TEXT);"
                          "CREATE TABLE run_attempt(attempt_id INTEGER,log_mode TEXT);")
        con.execute("INSERT INTO run_manifest VALUES ('different_population',1101,'completed')")
        con.execute("INSERT INTO run_attempt VALUES (0,'summary')")
    calls = []
    monkeypatch.setattr(RUN, "run_one", lambda entry, source, players, details:
        calls.append((source, details["log_mode"])) or {"reused": False})
    actual = RUN.resolve_cached_run(str(ENTRY), str(out), str(cache_root), 200, asdict(spec))
    assert calls == [(str(out), "summary")]
    assert actual["cache_source"] == "current_experiment"
