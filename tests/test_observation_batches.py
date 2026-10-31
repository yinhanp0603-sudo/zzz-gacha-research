"""逐文件观测导入、原聚合内容兼容与编码回退；全部是临时合成数据。"""

from __future__ import annotations

import csv
import gc
import json
import weakref
from pathlib import Path

import pytest

from zzz_research.observations import adapter_shiyu as AD
from zzz_research.storage.writer import SQLiteWriter

STAMP = "2026-10-28T00:00:00+00:00"


def _write(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def batch_snapshot(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    versions = tmp_path / "versions_config.json"
    versions.write_text(json.dumps({
        "9.2.1": {"collect_date": "01/08/2026", "sd": {"ver": "shared"}, "da": None},
        "9.2.2": {"collect_date": "08/08/2026", "sd": {"ver": "shared"}, "da": {"ver": "da"}},
    }), encoding="utf-8")
    common = {"uid": "1000000", "floor": 5, "node": 1, "rating": "S", "ch1": "Ellen", "score": 100}
    _write(snapshot / "9.2.1.csv", tuple(common),
           [common, {**common, "uid": "other"}, common])  # 重复 UID 块仍按原规则排除
    _write(snapshot / "9.2.2.csv", tuple(common), [{**common, "score": 200}])
    da = {"uid": "1000000", "floor": 1, "star": 3, "ch1": "Ellen", "score": 300}
    _write(snapshot / "9.2.2_da.csv", tuple(da), [da])
    for version, cinema in (("9.2.1", 2), ("9.2.2", 1)):
        row = {"uid": "1000000", "name": "Ellen", "cons": cinema, "weapon": version}
        _write(snapshot / f"{version}_char.csv", tuple(row), [row])
    kwargs = {"version_map_path": versions, "snapshot_id": "BATCH_TEST",
              "url": "https://example.invalid/synthetic", "revision": "fixture_revision",
              "retrieved_at": STAMP, "data_license": "CC-BY-4.0"}
    return snapshot, kwargs


def _aggregate(batches):
    return AD.ParsedSnapshot(
        [row for batch in batches for row in batch.battles],
        [row for batch in batches for row in batch.members],
        [row for batch in batches for row in batch.builds],
        [row for batch in batches for row in batch.manifest])


def _legacy_reference(snapshot, kwargs):
    """冻结改造前的跨文件拼接协议，复用已单测的单文件字段转换。"""
    vm = AD.load_version_map(kwargs["version_map_path"])
    battles, members, builds, manifest, cache = [], [], [], [], {}
    for version, mode, battle, char in AD.discover(snapshot, vm):
        group = AD.snapshot_group_id(kwargs["snapshot_id"], version)
        coverage = {"collection_period": version, "base_snapshot_id": kwargs["snapshot_id"],
                    "snapshot_group_id": group, "adapter_version": AD.ADAPTER_VERSION}
        provenance = {k: kwargs[k] for k in ("url", "revision", "retrieved_at", "data_license")}
        index = {}
        if char is not None:
            if char not in cache:
                cache[char] = AD.parse_builds(char, snapshot_id=group, sfid=AD.source_file_id(char))
                builds.extend(cache[char])
                manifest.append(AD.manifest_row(char, coverage={**coverage, "kind": "char"}, **provenance))
            index = {(r["local_uid"], r["unit_id"]): r for r in cache[char]}
        manifest.append(AD.manifest_row(battle, coverage={**coverage, "mode": mode, "kind": "battle"}, **provenance))
        br, mr = AD.parse_battles(battle, mode=mode, snapshot_id=group, sfid=AD.source_file_id(battle),
                                   period=vm[version], builds_by_uid_unit=index)
        battles.extend(br)
        members.extend(mr)
    return AD.ParsedSnapshot(battles, members, builds, manifest)


def test_batch_contents_match_legacy_and_compatible_api(batch_snapshot):
    snapshot, kwargs = batch_snapshot
    batches = list(AD.iter_snapshot_batches(snapshot, **kwargs))
    assert len(batches) == 3
    assert all(len({b["source_file_id"] for b in batch.battles}) == 1 for batch in batches)
    actual = _aggregate(batches)
    assert actual == _legacy_reference(snapshot, kwargs) == AD.parse_snapshot(snapshot, **kwargs)
    assert len(actual.battles) == len(actual.members) == 5
    assert len(actual.builds) == 2 and len(actual.manifest) == 5
    assert sum(b["exclusion_reason"] == AD.EXCL_DUPLICATE for b in actual.battles) == 1
    assert all(row["license"] == "CC-BY-4.0" for row in actual.manifest)


def test_char_is_emitted_once_and_batches_are_writable_in_order(batch_snapshot, monkeypatch):
    snapshot, kwargs = batch_snapshot
    original, calls = AD.parse_builds, []

    def counted(path, **kw):
        calls.append(Path(path).name)
        return original(path, **kw)

    monkeypatch.setattr(AD, "parse_builds", counted)
    iterator = AD.iter_snapshot_batches(snapshot, **kwargs)
    assert calls == []
    writer = SQLiteWriter(":memory:")
    try:
        build_counts, char_manifests = [], []
        for batch in iterator:
            build_counts.append(len(batch.builds))
            char_manifests += [m for m in batch.manifest if json.loads(m["coverage_json"])["kind"] == "char"]
            AD.write_snapshot(writer, batch)
        assert calls == ["9.2.1_char.csv", "9.2.2_char.csv"]
        assert build_counts == [1, 1, 0] and len(char_manifests) == 2
        assert writer.query("SELECT COUNT(*) FROM obs_build") == [(2,)]
        assert writer.query("PRAGMA foreign_key_check") == []
        rows = writer.query("""SELECT b.collection_period,b.mode,m.known_cinema,m.known_weapon,b.snapshot_id
            FROM obs_battle b JOIN obs_team_member m USING(battle_id)
            WHERE b.local_uid='1000000' AND b.is_eligible=1 ORDER BY b.collection_period,b.mode""")
        assert len(rows) == 3
        assert rows[0][2:] == (2, "9.2.1", "BATCH_TEST:9.2.1")
        assert all(r[2:] == (1, "9.2.2", "BATCH_TEST:9.2.2") for r in rows[1:])
    finally:
        writer.close()


def test_current_period_cache_releases_previous_period_builds(batch_snapshot, monkeypatch):
    snapshot, kwargs = batch_snapshot
    original = AD.parse_builds

    class WeakBuild(dict):
        pass

    monkeypatch.setattr(AD, "parse_builds", lambda path, **kw: [WeakBuild(r) for r in original(path, **kw)])
    iterator = AD.iter_snapshot_batches(snapshot, **kwargs)
    first = next(iterator)
    old_build = weakref.ref(first.builds[0])
    del first
    second = next(iterator)
    gc.collect()
    assert old_build() is None
    assert second.builds[0]["snapshot_id"] == "BATCH_TEST:9.2.2"
    iterator.close()


def test_empty_snapshot_is_rejected_by_both_apis(batch_snapshot, tmp_path):
    _, kwargs = batch_snapshot
    empty = tmp_path / "empty"
    empty.mkdir()
    for directory in (empty, tmp_path / "does-not-exist"):
        with pytest.raises(AD.ObservationError, match="没有可导入的战斗"):
            list(AD.iter_snapshot_batches(directory, **kwargs))
        with pytest.raises(AD.ObservationError, match="没有可导入的战斗"):
            AD.parse_snapshot(directory, **kwargs)


def test_cp1252_fallback_does_not_repeat_prefix_rows(tmp_path):
    path = tmp_path / "late_cp1252.csv"
    text = "uid,name\n" + "same,ASCII\n" * 4096 + "last,café\n"
    path.write_bytes(text.encode("cp1252"))
    rows = list(AD._read_csv(path))
    assert len(rows) == 4097
    assert rows[0] == (2, {"uid": "same", "name": "ASCII"})
    assert rows[-1] == (4098, {"uid": "last", "name": "café"})


def test_utf8_bom_and_multiline_record_keep_original_row_numbering(tmp_path):
    path = tmp_path / "utf8.csv"
    path.write_bytes('uid,name\none,中文\ntwo,"line1\nline2"\n'.encode("utf-8-sig"))
    assert list(AD._read_csv(path)) == [
        (2, {"uid": "one", "name": "中文"}), (3, {"uid": "two", "name": "line1\nline2"})]


def test_csv_parser_does_not_read_ahead_all_records(tmp_path, monkeypatch):
    path = tmp_path / "stream.csv"
    path.write_text("uid,name\none,first\ntwo,second\n", encoding="utf-8")
    original = AD.csv.DictReader

    def guarded(stream):
        reader = original(stream)
        yield next(reader)
        raise RuntimeError("second record requested")

    monkeypatch.setattr(AD.csv, "DictReader", guarded)
    iterator = AD._read_csv(path)
    assert next(iterator) == (2, {"uid": "one", "name": "first"})
    with pytest.raises(RuntimeError, match="second record"):
        next(iterator)


@pytest.mark.parametrize("text,expected", [("0.0", 0), ("2.0", 2), ("6.0", 6), (" 1.000 ", 1), (2, 2)])
def test_cinema_accepts_exact_integer_numeric_values(text, expected):
    assert AD._known_cinema(text) == expected


@pytest.mark.parametrize("text", ["2.5", "1.0000000000000001", "NaN", "inf", "-Infinity", "7.0", "-1", "", " ", None, True])
def test_cinema_rejects_fractional_nonfinite_and_out_of_range_values(text):
    assert AD._known_cinema(text) is None


@pytest.mark.parametrize("rank,expected", [("0.0", 0), ("2.0", 2), ("2.5", None), ("NaN", None), ("7.0", None)])
def test_real_format_rank_keeps_priority_without_truncation(batch_snapshot, rank, expected):
    snapshot, kwargs = batch_snapshot
    row = {"uid": "1000000", "floor": 5, "node": 1, "rating": "S", "ch1": "Ellen",
           "ch1_rank": rank, "score": 200}
    _write(snapshot / "9.2.2.csv", tuple(row), [row])
    parsed = AD.parse_snapshot(snapshot, **kwargs)
    target = {r["battle_id"] for r in parsed.battles
              if r["collection_period"] == "9.2.2" and r["mode"] == AD.MODE_SD}
    member, = [m for m in parsed.members if m["battle_id"] in target]
    assert member["known_cinema"] == expected
    # 无效的非空rank保持未知，不擅自用同文件的已知char值1或默认0替代。


@pytest.mark.parametrize("cons,expected", [("2.0", 2), ("0.0", 0), ("1.5", None), ("NaN", None), ("-1", None)])
def test_real_format_char_cons_fallback_is_defensive(batch_snapshot, cons, expected):
    snapshot, kwargs = batch_snapshot
    char = {"uid": "1000000", "name": "Ellen", "cons": cons, "weapon": "new"}
    _write(snapshot / "9.2.2_char.csv", tuple(char), [char])
    parsed = AD.parse_snapshot(snapshot, **kwargs)
    target = {r["battle_id"] for r in parsed.battles if r["collection_period"] == "9.2.2"}
    members = [m for m in parsed.members if m["battle_id"] in target]
    assert len(members) == 2 and all(m["known_cinema"] == expected for m in members)
