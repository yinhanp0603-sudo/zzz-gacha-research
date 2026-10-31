"""M3科学身份、持久化提交边界与冻结快照回归。"""

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from zzz_research import contracts as C
from zzz_research import simulation as SIM
from zzz_research.config import resolve_config
from zzz_research.economy import gacha as G
from zzz_research.economy import planner as PL
from zzz_research.economy import wallet as W
from zzz_research.rng import HashCounterRng
from zzz_research.storage.writer import BundleAnnotation, SQLiteWriter, StorageError


ENTRY = Path(__file__).resolve().parents[1] / "config" / "experiment.json"


@pytest.fixture(scope="module")
def cfg():
    return resolve_config(ENTRY, overrides={"population.n_players": 1})


@pytest.mark.parametrize("name,constant,expected", [
    ("planner", "PLANNER_VERSION", PL.PLANNER_VERSION),
    ("runtime", "RUNTIME_VERSION", SIM.RUNTIME_VERSION),
    ("scenario_resolver", "SCENARIO_RESOLVER_VERSION", SIM.SCENARIO_RESOLVER_VERSION),
])
def test_algorithm_versions_change_scientific_identity(cfg, monkeypatch, name, constant, expected):
    assert cfg.algorithm_versions[name] == expected
    from zzz_research import versions
    monkeypatch.setattr(versions, constant, expected + "_regression")
    new = resolve_config(ENTRY, overrides={"population.n_players": 1})
    assert new.config_id != cfg.config_id
    a = HashCounterRng.from_config(1, cfg.module("randomness"))
    b = HashCounterRng.from_config(1, new.module("randomness"))
    assert a.population_u(0, "tier") == b.population_u(0, "tier")


def test_runtime_rejects_stale_algorithm_identity(cfg):
    v = {**cfg.algorithm_versions, "runtime": "stale_runtime"}
    stale = replace(cfg, algorithm_versions=C.deep_freeze(v))
    with pytest.raises(SIM.SimulationError):
        SIM.Runtime(stale, 1)


@pytest.mark.parametrize("path,value", [
    ("scenarios.timeline.days_per_period", 21),
    ("combat.support.gains.S_A", 0.5),
    ("gacha.pools.character.pity_group", "other_limited_character"),
])
def test_catalog_identity_covers_written_semantics(cfg, path, value):
    changed = resolve_config(ENTRY, overrides={"population.n_players": 1, path: value})
    assert SIM.Runtime(cfg, 1).world.catalog_id != SIM.Runtime(changed, 1).world.catalog_id


def _start(writer, rt, attempt_id=0):
    writer.start_run(
        run_id=rt.run_id, experiment_id=rt.identity.experiment_id, config_id=rt.cfg.config_id,
        replicate_id=rt.identity.replicate_id, master_seed=rt.identity.master_seed,
        rng_version=rt.rng.params.rng_version, algorithm_versions=rt.algorithm_versions(),
        config_science=C.deep_thaw(rt.cfg.science), input_manifest_hash="test", environment={},
        attempt_id=attempt_id,
    )


def _paid_bundle(rt):
    ps, _ = rt.init_player(0)
    ps.wallet = W.set_period_paid_cap(rt.policy, ps.wallet, 1)
    period = rt.world.periods[0]
    banner = period.banners[0]
    plan = G.build_target_plan(rt.gacha, "0+1", banner.unit_id,
                               {"character": banner.banner_id, "weapon": banner.banner_id})
    return rt._one_pull(ps, period, banner, plan.stages[0], 0)


@pytest.mark.parametrize("chunk_rows", [1, 2])
def test_small_chunks_do_not_split_mutually_referencing_pull(cfg, chunk_rows):
    rt = SIM.Runtime(cfg, 1)
    bundle, ann = _paid_bundle(rt)
    writer = SQLiteWriter(":memory:", chunk_rows=chunk_rows)
    _start(writer, rt)
    writer.write_bundle(bundle, ann)
    writer.flush()
    assert writer.query("PRAGMA foreign_key_check") == []
    assert writer.query("SELECT COUNT(*) FROM fact_pull_event") == [(1,)]
    assert writer.query("SELECT COUNT(*) FROM fact_cash_event") == [(1,)]


def test_failed_flush_does_not_count_uncommitted_rows():
    writer = SQLiteWriter(":memory:")
    writer.insert("fact_unit_cohort", dict(run_id="missing", unit_id="U", acquisition_period=1,
                                         player_id=0, acquisition_day=0, cohort_id="C"))
    with pytest.raises(sqlite3.IntegrityError):
        writer.flush()
    assert writer.row_counts == {}
    assert not writer.conn.in_transaction


@pytest.mark.parametrize("attempt_id", [0, 1])
def test_same_database_rerun_is_rejected_without_touching_completed_run(cfg, attempt_id):
    rt = SIM.Runtime(cfg, 1)
    writer = SQLiteWriter(":memory:")
    _start(writer, rt)
    writer.finish_run(status="completed")
    before = writer.query("SELECT * FROM run_attempt")
    with pytest.raises(StorageError):
        _start(writer, rt, attempt_id)
    assert not writer.conn.in_transaction
    assert writer.query("SELECT status FROM run_manifest") == [("completed",)]
    assert writer.query("SELECT * FROM run_attempt") == before


def test_shared_dimension_conflict_is_not_silently_ignored():
    writer = SQLiteWriter(":memory:")
    row = dict(team_id="T", anchor_unit_id="A", neutral_unit_id="N", support_id="NONE",
               component_builds_json="{}", build_hash="first")
    writer.write_dim("dim_team_snapshot", [row])
    writer.write_dim("dim_team_snapshot", [row])
    with pytest.raises(StorageError):
        writer.write_dim("dim_team_snapshot", [{**row, "build_hash": "changed"}])
    assert writer.query("SELECT build_hash FROM dim_team_snapshot") == [("first",)]


def test_single_pull_writer_failure_does_not_commit_memory(cfg):
    class RejectingWriter(SQLiteWriter):
        def write_bundle(self, *args, **kwargs):
            raise StorageError("injected writer failure")
    rt = SIM.Runtime(cfg, 1, RejectingWriter(":memory:"))
    ps, _ = rt.init_player(0)
    ps.wallet = W.set_period_paid_cap(rt.policy, ps.wallet, 1)
    p = rt.world.periods[0]
    b = p.banners[0]
    plan = G.build_target_plan(rt.gacha, "0+1", b.unit_id, {"character": b.banner_id, "weapon": b.banner_id})
    before = (ps.wallet, ps.roster, dict(ps.pities), dict(ps.batch_earned))
    with pytest.raises(StorageError):
        rt._one_pull(ps, p, b, plan.stages[0], 0)
    assert (ps.wallet, ps.roster, ps.pities, ps.batch_earned) == before


def test_bundle_projection_failure_restores_pending_buffer(cfg):
    rt = SIM.Runtime(cfg, 1)
    bundle, ann = _paid_bundle(rt)
    writer = SQLiteWriter(":memory:")
    _start(writer, rt)
    bad = BundleAnnotation(ann.period_id, {}, ann.resources)
    with pytest.raises(KeyError):
        writer.write_bundle(bundle, bad)
    assert writer._pending == 0 and writer._buf == {}
    assert writer.query("SELECT COUNT(*) FROM fact_cash_event") == [(0,)]


def test_new_character_gear_is_materialized_before_freeze(cfg):
    writer = SQLiteWriter(":memory:")
    rt = SIM.Runtime(cfg, 1, writer)
    rt.rng.draw_u = lambda *args: 0.0
    rt.rng.decision_u = lambda *args: 0.0
    rt.rng.churn_u = lambda *args: 0.0
    result = rt.run()
    player = result.players[0]
    new_id = rt.world.periods[0].banners[0].unit_id
    assert player.roster.frozen and player.roster.character(new_id) is not None
    assert all(c.gear_value is not None for c in player.roster.characters)
    assert player.roster.character(new_id).gear_value == 1.0
    assert writer.query("SELECT COUNT(*) FROM fact_ownership WHERE asset_kind='character' AND disc_actual IS NULL") == [(0,)]
    rows = writer.query("SELECT DISTINCT disc_actual FROM fact_player_roster_panel WHERE unit_id=? AND eval_phase='frozen_post'", (new_id,))
    assert rows == [(1.0,)]
