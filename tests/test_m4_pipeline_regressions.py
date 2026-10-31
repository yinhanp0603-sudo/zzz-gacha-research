"""搜索复用、原子发布与当前观测状态回归；仅使用两人小样本和临时目录。"""

import csv
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from zzz_research import pipeline as PL
from zzz_research.analytics import search as SE
from zzz_research.config import resolve_config
from zzz_research.observations import adapter_shiyu as AD
from zzz_research.simulation import run_simulation
from zzz_research.storage.writer import DETAIL_TABLES, SQLiteWriter


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "config" / "experiment.json"


@pytest.mark.parametrize("train,validation", [
    ([1, 1], [2]), ([1], [2, 2]), ([1], [1]), ([-1], [2]), ([1], [-2]),
    ([True], [2]), ([1.5], [2]), ([1], ["2"]), ([], [2]), ([1], []),
])
def test_final_seed_lists_are_validated_before_execution(train, validation):
    with pytest.raises(SE.SearchError):
        SE.plan_search(ENTRY, train_seeds=train, validation_seeds=validation)


@pytest.fixture(scope="module")
def completed_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("validated_run")
    cfg = resolve_config(ENTRY, overrides={"population.n_players": 2})
    db = root / "source.sqlite"
    writer = SQLiteWriter(db, log_mode="full", chunk_rows=37)
    try:
        result = run_simulation(cfg, master_seed=1101, writer=writer)
    finally:
        writer.close()
    return {"db": db, "run_id": result.run_id, "config_id": cfg.config_id, "seed": 1101}


def _copy_run(tmp_path, completed_run):
    db = tmp_path / "cached.sqlite"
    shutil.copyfile(completed_run["db"], db)
    return db, (completed_run["config_id"], completed_run["seed"])


def test_full_cache_is_reused_read_only_for_both_log_modes(tmp_path, completed_run):
    db, key = _copy_run(tmp_path, completed_run)
    before = db.read_bytes()
    for mode in ("summary", "full"):
        hit = SE._index_completed(tmp_path, required_log_mode=mode)[key]
        row = SE._load_existing(hit, key, "Budget_40", required_log_mode=mode)
        assert row["reused"] and row["core_initial"] > 0
    assert db.read_bytes() == before


def test_summary_cache_cannot_satisfy_full_request(tmp_path, completed_run):
    db, key = _copy_run(tmp_path, completed_run)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE run_attempt SET log_mode='summary'")
    assert key in SE._index_completed(tmp_path, required_log_mode="summary")
    assert key not in SE._index_completed(tmp_path, required_log_mode="full")
    with pytest.raises(SE.SearchError, match="summary"):
        SE._load_existing((completed_run["run_id"], db), key, "Budget_40", required_log_mode="full")


@pytest.mark.parametrize("change", [
    "DELETE FROM run_attempt",
    "UPDATE run_attempt SET status='failed'",
    "DELETE FROM agg_player_period WHERE player_id=0 AND t_idx=1",
    "DELETE FROM fact_player_lifecycle WHERE player_id=0 AND t_idx=1",
    "UPDATE agg_player_period SET CI_post=CI_post+0.1 WHERE player_id=0 AND t_idx=1",
    "UPDATE run_manifest SET master_seed=1102",
    "UPDATE run_manifest SET config_id='incorrect'",
    "INSERT INTO fact_unit_cohort VALUES ('missing','U',1,0,0,'C')",
])
def test_invalid_completed_cache_is_not_reused(tmp_path, completed_run, change):
    db, key = _copy_run(tmp_path, completed_run)
    with sqlite3.connect(db) as conn:
        conn.execute(change)
    assert key not in SE._index_completed(tmp_path)


def test_full_flag_does_not_hide_missing_detail_rows(tmp_path, completed_run):
    db, key = _copy_run(tmp_path, completed_run)
    with sqlite3.connect(db) as conn:
        for table in DETAIL_TABLES:
            conn.execute(f"DELETE FROM {table}")
    assert key not in SE._index_completed(tmp_path, required_log_mode="full")


def test_missing_and_corrupt_run_files_are_not_created_or_trusted(tmp_path):
    assert SE._index_completed(tmp_path) == {}
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(SE.SearchError, match="缺少"):
        SE._load_existing(("r", missing), ("cfg", 1), "Budget_40")
    assert not missing.exists()
    corrupt = tmp_path / "bad.sqlite"
    corrupt.write_bytes(b"not a sqlite database")
    assert SE._index_completed(tmp_path) == {}
    assert corrupt.read_bytes() == b"not a sqlite database"


def test_cache_config_and_seed_are_checked_on_load(tmp_path, completed_run):
    db, _ = _copy_run(tmp_path, completed_run)
    with pytest.raises(SE.SearchError, match="config/seed"):
        SE._load_existing((completed_run["run_id"], db), (completed_run["config_id"], 9), "Budget_40")


@pytest.fixture(scope="module")
def tiny_search(tmp_path_factory):
    out = tmp_path_factory.mktemp("tiny_search")
    setup = SE.plan_search(ENTRY, n_players=2, train_seeds=[1101], validation_seeds=[9101],
                           candidate_ids=["L1_A1"], perturbation_ids=["P0"])
    results = SE.execute(setup, out)
    return setup, results


def test_valid_search_cache_avoids_execution(tmp_path, tiny_search, monkeypatch):
    setup, results = tiny_search
    runs = tmp_path / "runs"
    runs.mkdir()
    for r in results.values():
        shutil.copyfile(r["db"], runs / f"{r['run_id']}.sqlite")
    def must_not_run(*args):
        pytest.fail("有效完成缓存不应重新模拟")
    monkeypatch.setattr(SE, "_execute_one", must_not_run)
    again = SE.execute(setup, tmp_path)
    assert all(r.get("reused") for r in again.values())


def test_failed_search_publication_preserves_old_database(tmp_path, tiny_search, monkeypatch):
    setup, results = tiny_search
    db = tmp_path / "search.sqlite"
    SE.assemble(setup, results, db)
    old = db.read_bytes()
    opened = []
    def fail_after_mutating_staged_copy(setup, results, writer):
        opened.append(writer.conn)
        writer.conn.execute("DELETE FROM agg_search_results")
        writer.conn.execute("BEGIN")
        writer.conn.execute("UPDATE experiment_manifest SET status='failed'")
        raise RuntimeError("injected assembly failure")
    monkeypatch.setattr(SE, "_assemble_rows", fail_after_mutating_staged_copy)
    with pytest.raises(RuntimeError, match="injected"):
        SE.assemble(setup, results, db)
    assert db.read_bytes() == old
    assert not list(tmp_path.glob("*.tmp.sqlite"))
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT DISTINCT status FROM experiment_manifest").fetchall() == [("completed",)]


def test_import_error_rolls_back_before_detach(tmp_path, tiny_search, monkeypatch):
    setup, results = tiny_search
    closed = []
    class RejectingConnection:
        def __init__(self, conn):
            self.conn = conn
            self.actions = []
        def __getattr__(self, name):
            return getattr(self.conn, name)
        def execute(self, sql, *args):
            self.actions.append(sql)
            if sql.startswith("INSERT INTO main.agg_player_period"):
                raise RuntimeError("injected import failure")
            return self.conn.execute(sql, *args)
        def rollback(self):
            self.actions.append("ROLLBACK")
            return self.conn.rollback()
        def close(self):
            closed.append(self)
            self.conn.close()
    original = SE.SQLiteWriter
    def rejecting_writer(path):
        writer = original(path)
        writer.conn = RejectingConnection(writer.conn)
        return writer
    monkeypatch.setattr(SE, "SQLiteWriter", rejecting_writer)
    db = tmp_path / "search.sqlite"
    with pytest.raises(RuntimeError, match="injected import"):
        SE.assemble(setup, results, db)
    assert not db.exists()
    assert closed and closed[0].actions.index("ROLLBACK") < closed[0].actions.index("DETACH DATABASE src")


def test_unknown_core_retention_cannot_produce_frontier(tmp_path, tiny_search):
    setup, results = tiny_search
    bad = {k: {**v, "core_retention_end": None, "core_initial": 0} for k, v in results.items()}
    db = tmp_path / "search.sqlite"
    with pytest.raises(SE.SearchError, match="核心队列留存不可评估"):
        SE.assemble(setup, bad, db)
    assert not db.exists()


def test_pipeline_exports_only_current_experiment(tmp_path):
    kwargs = dict(n_players=2, train_seeds=[1101], validation_seeds=[9101],
                  perturbation_ids=["P0", "gap_weight_x0.75"])
    first = PL.search(ENTRY, tmp_path, candidate_ids=["L1_A1"], **kwargs)
    second = PL.search(ENTRY, tmp_path, candidate_ids=["L0_A0.8"], **kwargs)
    assert first["experiment_id"] != second["experiment_id"]
    with sqlite3.connect(tmp_path / "search.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM experiment_manifest").fetchone() == (2,)
    for name in ("seed_robustness.csv", "search_results.csv", "rank_stability.csv", "parameter_sensitivity.csv"):
        with open(tmp_path / name, encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert rows and {r["experiment_id"] for r in rows} == {second["experiment_id"]}


def test_metadata_only_refresh_removes_previous_snapshot_outputs(tmp_path):
    derived = ("obs_coverage.csv", "exclusions.csv", "appearance.csv", "results_by_stage.csv",
               "investment_association.csv", "quality_issues.json")
    for name in derived:
        (tmp_path / name).write_text("stale", encoding="utf-8")
    (tmp_path / "observations_manifest.json").write_text(
        json.dumps({"status": "imported", "observed_data_available": True}), encoding="utf-8")
    result = PL.prepare_observations(tmp_path, meta_dir=ROOT / PL.DEFAULT_META_DIR)
    assert result["status"] == "metadata_only" and not result["observed_data_available"]
    assert all(not (tmp_path / name).exists() for name in derived)
    manifest = json.loads((tmp_path / "observations_manifest.json").read_text("utf-8"))
    assert manifest["snapshot_status"] == "not_provided" and manifest["snapshot_id"] is None
    assert manifest["n_battles"] == 0 and manifest["n_eligible"] == 0
    assert manifest["adapter_version"] == AD.ADAPTER_VERSION
    with sqlite3.connect(tmp_path / "observations.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM obs_battle").fetchone() == (0,)


def test_failed_observation_refresh_is_not_marked_imported(tmp_path):
    with pytest.raises((FileNotFoundError, ValueError)):
        PL.prepare_observations(tmp_path, meta_dir=tmp_path / "missing")
    manifest = json.loads((tmp_path / "observations_manifest.json").read_text("utf-8"))
    assert manifest["status"] == "failed" and not manifest["observed_data_available"]
