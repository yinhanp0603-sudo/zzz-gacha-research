"""M4：采集期 UID 身份隔离、缺失 UID 与数据许可登记回归。

所有 CSV 均为临时合成夹具，不是公开战斗记录。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from zzz_research.analytics import observed as OB
from zzz_research.observations import adapter_shiyu as AD
from zzz_research.observations import quality as QA
from zzz_research.storage.writer import SQLiteWriter

BASE = "IMPORT_TEST"
OLD = "9.2.1"
NEW = "9.2.2"
SEASON = "shared_sd_season"
UID = "1000000"  # 上游各采集期可能重新使用相同编号。
RAW_URL = "https://example.invalid/synthetic-observation-fixture"
RETRIEVED_AT = "2026-10-28T00:00:00+00:00"
SD_FIELDS = ("uid", "floor", "node", "rating", "ch1", "ch2", "ch3", "score")
CHAR_FIELDS = ("uid", "name", "level", "cons", "weapon", "element", "artifacts")
DA_FIELDS = ("uid", "floor", "star", "ch1", "ch2", "ch3", "score")


def _write_csv(path: Path, fields, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def _fixture(tmp_path: Path, *, missing_uids: bool = False) -> dict:
    snap = tmp_path / "snapshot"
    snap.mkdir()
    periods = {
        OLD: {"collect_date": "01/08/2026", "sd": {"ver": SEASON}, "da": None},
        NEW: {"collect_date": "08/08/2026", "sd": {"ver": SEASON}, "da": {"ver": "da_current"}},
    }
    vm = tmp_path / "versions_config.json"
    vm.write_text(json.dumps(periods), encoding="utf-8")
    old_row = {"uid": UID, "floor": 5, "node": 1, "rating": "S", "ch1": "Ellen", "score": 100}
    rows = [old_row]
    if missing_uids:
        rows += [{**old_row, "uid": "", "score": 999}, {**old_row, "uid": "   ", "score": 888}]
    _write_csv(snap / f"{OLD}.csv", SD_FIELDS, rows)
    _write_csv(snap / f"{NEW}.csv", SD_FIELDS, [{**old_row, "score": 200}])
    _write_csv(snap / f"{NEW}_da.csv", DA_FIELDS,
               [{"uid": UID, "floor": 1, "star": 3, "ch1": "Ellen", "score": 300}])
    _write_csv(snap / f"{OLD}_char.csv", CHAR_FIELDS,
               [{"uid": UID, "name": "Ellen", "cons": 2, "weapon": "old_snapshot_weapon"}])
    _write_csv(snap / f"{NEW}_char.csv", CHAR_FIELDS, [])
    return {"snapshot": snap, "version_map": vm}


def _parse(fixture: dict, **kwargs) -> AD.ParsedSnapshot:
    return AD.parse_snapshot(
        fixture["snapshot"], version_map_path=fixture["version_map"], snapshot_id=BASE,
        url=RAW_URL, revision="synthetic_fixture_revision", retrieved_at=RETRIEVED_AT, **kwargs,
    )


def _database(parsed: AD.ParsedSnapshot) -> SQLiteWriter:
    w = SQLiteWriter(":memory:")
    AD.write_snapshot(w, parsed)
    return w


def test_same_uid_in_same_season_isolated_by_collection_period(tmp_path):
    parsed = _parse(_fixture(tmp_path))
    w = _database(parsed)
    old_id, new_id = AD.snapshot_group_id(BASE, OLD), AD.snapshot_group_id(BASE, NEW)
    assert old_id == f"{BASE}:{OLD}" and new_id == f"{BASE}:{NEW}" and old_id != new_id
    assert {r[0] for r in w.query("SELECT DISTINCT snapshot_id FROM obs_battle")} == {old_id, new_id}
    assert {r[0] for r in w.query("SELECT DISTINCT local_uid FROM obs_battle")} == {UID}
    scopes = OB.scopes(w)
    assert {(s.snapshot_id, s.combat_season, s.mode) for s in scopes} == {
        (old_id, SEASON, AD.MODE_SD), (new_id, SEASON, AD.MODE_SD),
        (new_id, "da_current", AD.MODE_DA),
    }
    old = OB.Scope(old_id, SEASON, AD.MODE_SD, ("5-1",))
    new = OB.Scope(new_id, SEASON, AD.MODE_SD, ("5-1",))
    assert OB.sample_players(w, old) == OB.sample_players(w, new) == 1
    assert OB.results_by_stage(w, old)[0]["median"] == 100
    assert OB.results_by_stage(w, new)[0]["median"] == 200
    coverage = {(r["source_snapshot_id"], r["mode"]): r for r in QA.compute_obs_coverage(w)}
    assert coverage[(old_id, AD.MODE_SD)]["known_build_players"] == 1
    assert coverage[(new_id, AD.MODE_SD)]["known_build_players"] == 0
    assert coverage[(new_id, AD.MODE_DA)]["known_build_players"] == 0
    assert w.query("""SELECT m.known_cinema, m.build_missing_reason FROM obs_team_member m
                      JOIN obs_battle b USING(battle_id) WHERE b.snapshot_id=? AND b.mode=?""",
                   (new_id, AD.MODE_SD)) == [(None, "no_char_record")]
    assert QA.check_quality(w) == []


def test_same_collection_sd_da_and_char_share_identity_group(tmp_path):
    fixture = _fixture(tmp_path)
    _write_csv(fixture["snapshot"] / f"{NEW}_char.csv", CHAR_FIELDS,
               [{"uid": UID, "name": "Ellen", "cons": 1, "weapon": "new_snapshot_weapon"}])
    w = _database(_parse(fixture))
    group = AD.snapshot_group_id(BASE, NEW)
    members = w.query("""SELECT b.mode, b.snapshot_id, m.build_record_id, m.known_cinema, m.known_weapon
                         FROM obs_team_member m JOIN obs_battle b USING(battle_id)
                         WHERE b.snapshot_id=? ORDER BY b.mode""", (group,))
    assert {r[0] for r in members} == {AD.MODE_SD, AD.MODE_DA}
    assert all(r[1] == group and r[3:] == (1, "new_snapshot_weapon") for r in members)
    assert len({r[2] for r in members}) == 1  # 同一期 char 文件只解析一次。
    assert w.query("SELECT COUNT(*) FROM obs_build WHERE snapshot_id=?", (group,)) == [(1,)]
    assert all(r["known_build_players"] == 1 for r in QA.compute_obs_coverage(w)
               if r["source_snapshot_id"] == group)


def test_missing_uid_excluded_from_all_sample_denominators(tmp_path):
    w = _database(_parse(_fixture(tmp_path, missing_uids=True)))
    assert w.query("SELECT is_eligible, exclusion_reason FROM obs_battle WHERE local_uid IS NULL") == [
        (0, AD.EXCL_MISSING_UID), (0, AD.EXCL_MISSING_UID),
    ]
    scope = OB.Scope(AD.snapshot_group_id(BASE, OLD), SEASON, AD.MODE_SD, ("5-1",))
    assert OB.sample_players(w, scope) == 1
    appearance = OB.appearance(w, scope)
    assert len(appearance) == 1
    assert appearance[0]["sample_players"] == appearance[0]["players"] == 1
    assert appearance[0]["eligible_clears"] == appearance[0]["clears"] == 1
    assert appearance[0]["appearance_player_share"] == appearance[0]["appearance_per_clear"] == 1
    result = OB.results_by_stage(w, scope)[0]
    assert result["n"] == result["players"] == 1 and result["median"] == 100
    coverage = next(r for r in QA.compute_obs_coverage(w)
                    if r["source_snapshot_id"] == scope.snapshot_id)
    assert coverage["raw_records"] == 3 and coverage["eligible_records"] == coverage["unique_players"] == 1
    assert QA.check_quality(w) == []


def test_quality_guard_rejects_eligible_rows_without_uid(tmp_path):
    w = _database(_parse(_fixture(tmp_path, missing_uids=True)))
    # 防御已有数据库中被错误标为合格的缺失 UID 记录。
    w.conn.execute("UPDATE obs_battle SET is_eligible=1, exclusion_reason=NULL WHERE local_uid IS NULL")
    assert "eligible_without_uid:2" in QA.check_quality(w)
    scope = OB.Scope(AD.snapshot_group_id(BASE, OLD), SEASON, AD.MODE_SD, ("5-1",))
    assert OB.sample_players(w, scope) == 1
    assert OB.appearance(w, scope)[0]["eligible_clears"] == 1
    assert OB.results_by_stage(w, scope)[0]["n"] == 1


def test_raw_data_license_unknown_is_not_inherited_from_code(tmp_path):
    fixture = _fixture(tmp_path)
    parsed = _parse(fixture)
    assert parsed.manifest and all(r["license"] is None for r in parsed.manifest)
    w = _database(parsed)
    assert all(r[0] is None for r in w.query("SELECT license FROM source_manifest"))
    metadata = AD.manifest_row(
        fixture["version_map"], url="https://example.invalid/metadata", revision=AD.SOURCE_REVISION,
        coverage={"kind": "metadata"}, retrieved_at=RETRIEVED_AT,
    )
    assert metadata["license"] == "MIT"


def test_explicit_data_license_and_adapter_identity_are_registered(tmp_path):
    parsed = _parse(_fixture(tmp_path), data_license="CC-BY-4.0")
    assert parsed.manifest and all(r["license"] == "CC-BY-4.0" for r in parsed.manifest)
    for r in parsed.manifest:
        coverage = json.loads(r["coverage_json"])
        assert coverage["adapter_version"] == AD.ADAPTER_VERSION == "shiyu_adapter_v3"
        assert coverage["base_snapshot_id"] == BASE
        assert coverage["snapshot_group_id"] == AD.snapshot_group_id(BASE, coverage["collection_period"])
