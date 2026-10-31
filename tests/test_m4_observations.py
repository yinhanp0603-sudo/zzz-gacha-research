"""M1/M4 观测链路：ShiyuStats 字段映射、源码有效通关规则、覆盖与描述指标的分母。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from zzz_research.analytics import observed as OB
from zzz_research.observations import adapter_shiyu as AD
from zzz_research.observations import quality as QA
from zzz_research.storage.writer import SQLiteWriter

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "data" / "raw" / "SRC_SHIYU_meta_d930cec50bfd"


def _import(snap) -> SQLiteWriter:
    parsed = AD.parse_snapshot(snap["snapshot"], version_map_path=snap["version_map"], snapshot_id="SNAP_T",
                               url="https://example.invalid/shiyu", revision="rev_test", retrieved_at="2026-10-27T00:00:00+00:00")
    w = SQLiteWriter(":memory:")
    AD.write_snapshot(w, parsed)
    return w


def test_regime_rules_follow_pinned_source():
    assert AD.regime_for(AD.MODE_SD, date(2025, 12, 29))["regime"] == AD.REGIME_TIME
    new = AD.regime_for(AD.MODE_SD, date(2025, 12, 30))
    assert new["regime"] == AD.REGIME_SCORE and new["stages"] == ("5-1", "5-2", "5-3")
    assert "2-1" not in AD.regime_for(AD.MODE_DA, date(2026, 7, 28))["stages"]
    assert "2-1" in AD.regime_for(AD.MODE_DA, date(2026, 7, 29))["stages"]
    assert AD.SOURCE_REVISION.startswith("d930cec5")


def test_version_map_separates_collection_and_season(shiyu_snapshot):
    vm = AD.load_version_map(shiyu_snapshot["version_map"])
    assert vm["9.1.1"].sd_season == "9.1.0" and vm["9.1.1"].da_season == "9.1.1"
    assert vm["9.0.1"].da_season is None


def test_parse_eligibility_and_exclusions(shiyu_snapshot):
    w = _import(shiyu_snapshot)
    rows = {(r[0], r[1]): (r[2], r[3], r[4], r[5])
            for r in w.query("SELECT source_row_locator, local_uid, is_eligible, exclusion_reason, stage_id, scoring_regime "
                             "FROM obs_battle")}
    by_reason = dict(w.query("SELECT COALESCE(exclusion_reason,'ok'), COUNT(*) FROM obs_battle GROUP BY 1"))
    assert by_reason[AD.EXCL_DUPLICATE] == 1
    assert by_reason[AD.EXCL_NOT_VALID] == 2      # SD A 评级 + DA 2星于第1阶段
    assert by_reason[AD.EXCL_STAGE] == 2          # 计时制 6-1；计分制 7-1
    assert by_reason[AD.EXCL_EMPTY] == 1
    # 计时制写 clear_time_seconds，计分制写 score_value，不跨制度换算
    t = w.query("SELECT COUNT(*) FROM obs_battle WHERE scoring_regime='time' AND score_value IS NOT NULL")[0][0]
    s = w.query("SELECT COUNT(*) FROM obs_battle WHERE scoring_regime='score' AND clear_time_seconds IS NOT NULL")[0][0]
    assert t == 0 and s == 0
    # DA：floor>3 → 2-1，1 星即合格
    da2 = w.query("SELECT stage_id, is_eligible FROM obs_battle WHERE mode=? AND local_uid='d1' ORDER BY stage_id",
                  (AD.MODE_DA,))
    assert da2 == [("1-1", 1), ("2-1", 1)]
    # 采集期与赛季分开登记
    assert w.query("SELECT DISTINCT combat_season FROM obs_battle WHERE collection_period='9.1.1' AND mode=?",
                   (AD.MODE_SD,)) == [("9.1.0",)]
    assert QA.check_quality(w) == []


def test_team_members_and_builds(shiyu_snapshot):
    w = _import(shiyu_snapshot)
    # rank 列优先；无 rank 时取 _char 资料；无资料记缺失原因而不是 0
    rank = w.query("""SELECT m.known_cinema FROM obs_team_member m JOIN obs_battle b USING(battle_id)
                      WHERE b.local_uid='u1' AND b.stage_id='7-1' AND m.unit_id='Soukaku' AND b.is_eligible=1""")
    assert rank == [(6,)]
    char = w.query("""SELECT m.known_cinema, m.known_weapon, m.build_missing_reason FROM obs_team_member m
                      JOIN obs_battle b USING(battle_id) WHERE b.local_uid='u5' AND m.unit_id='Miyabi'""")
    assert char == [(2, "Hailstorm Shrine", None)]
    miss = w.query("""SELECT m.known_cinema, m.build_missing_reason FROM obs_team_member m
                      JOIN obs_battle b USING(battle_id) WHERE b.local_uid='v1' AND m.unit_id='Yanagi'""")
    assert miss == [(None, "no_char_record")]
    man = w.query("SELECT source_id, revision, sha256 IS NOT NULL, license FROM source_manifest")
    assert man and all(r[0] == AD.SOURCE_ID and r[1] == "rev_test" and r[2] == 1 and r[3] is None for r in man)
    assert w.query("PRAGMA foreign_key_check") == []


def test_unknown_collection_period_fails(shiyu_snapshot):
    (shiyu_snapshot["snapshot"] / "8.8.8.csv").write_text("uid,floor,node,rating\n", encoding="utf-8")
    with pytest.raises(AD.ObservationError):
        _import(shiyu_snapshot)


def test_obs_coverage_and_small_sample(shiyu_snapshot):
    w = _import(shiyu_snapshot)
    rows = QA.write_obs_coverage(w)
    key = {(r["combat_season"], r["mode"], r["stage_id"]): r for r in rows}
    r71 = key[("9.0.1", AD.MODE_SD, "7-1")]
    assert r71["raw_records"] == 5 and r71["eligible_records"] == 2 and r71["unique_players"] == 2
    assert r71["status"] == "small_sample"
    assert r71["known_build_players"] == 1  # 只有 u1 有 _char 资料
    assert w.query("SELECT COUNT(*) FROM obs_coverage")[0][0] == len(rows)


def test_observed_metrics_denominators(shiyu_snapshot):
    w = _import(shiyu_snapshot)
    sc = OB.Scope(AD.snapshot_group_id("SNAP_T", "9.0.1"), "9.0.1", AD.MODE_SD, ("7-1", "7-2"))
    assert OB.sample_players(w, sc) == 3  # u1、u3、u5
    app = {r["unit_id"]: r for r in OB.appearance(w, sc)}
    # u1 两关都用 Lycaon：人数去重 2/3；队伍出场 3/4
    assert app["Lycaon"]["appearance_player_share"] == pytest.approx(2 / 3)
    assert app["Lycaon"]["appearance_per_clear"] == pytest.approx(3 / 4)
    assert app["Lycaon"]["small_sample"]
    res = {r["stage_id"]: r for r in OB.results_by_stage(w, sc)}
    assert res["7-1"]["n"] == 2 and res["7-1"]["median"] == pytest.approx(110)
    assert res["7-2"]["p25"] == pytest.approx(145) and res["7-2"]["p75"] == pytest.approx(155)
    teams = OB.team_compositions(w, sc)
    assert sum(t["records"] for t in teams) == 4 and all(t["share"] == pytest.approx(1 / 4) for t in teams)


def test_quantile_matches_linear_definition():
    v = [1.0, 2.0, 4.0, 8.0]
    assert OB.quantile(v, 0.5) == pytest.approx(3.0)
    assert OB.quantile(v, 0.25) == pytest.approx(1.75)
    assert OB.quantile([], 0.5) is None
    assert OB.describe([5.0])["median"] == 5.0


def test_investment_association_excludes_unknown(shiyu_snapshot):
    w = _import(shiyu_snapshot)
    chars = AD.load_characters(shiyu_snapshot["characters"])
    sc = OB.Scope(AD.snapshot_group_id("SNAP_T", "9.0.1"), "9.0.1", AD.MODE_SD, ("7-1", "7-2"))
    a = OB.investment_association(w, sc, chars, "Miyabi")
    assert a["status"] == "ok"
    # u1(rank=1)、u5(_char cons=2) 已知 ≥1；u3 无 rank 也无资料 → 单列未知，不并入 0 组
    assert a["cinema_0"]["n"] == 0 and a["cinema_1plus"]["n"] == 2
    assert a["cinema_unknown_records"] == 1
    assert OB.investment_association(w, sc, chars, "Lycaon")["status"] == "not_limited_s"


def test_real_repo_file_coverage_metadata():
    """固定修订的真实清单与采集期映射：每个采集期一行，缺期如实标注。"""
    vm = AD.load_version_map(META / "versions_config.json")
    cov = QA.repo_file_coverage(META / "repo_files.csv", vm)
    assert len(cov) == len(vm)
    assert [c.collect_date for c in cov] == sorted(c.collect_date for c in cov)
    with open(META / "repo_files.csv", encoding="utf-8") as f:
        names = {l.strip() for l in f if l.strip()}
    for c in cov:
        assert c.has_sd_battle == (f"{c.collection_period}.csv" in names)
        if c.status != "complete":
            assert c.status.startswith("missing:")
    assert (META / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
