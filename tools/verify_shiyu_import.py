"""从原始逐文件计数和只读 SQLite 独立核对公开数据导入。"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


root = Path(__file__).resolve().parents[1]
out = root / "outputs/benchmark_n200"
raw = json.loads((out / "verification/shiyu_raw_audit.json").read_text("utf-8"))
manifest = json.loads((out / "observations/observations_manifest.json").read_text("utf-8"))
download = json.loads((root / "data/raw/shiyu_hf/download_manifest.json").read_text("utf-8"))
if download["status"] != "completed" or manifest["snapshot_status"] != "imported":
    raise RuntimeError("下载或导入尚未完成")
db = (out / "observations/observations.sqlite").resolve()
conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
try:
    prior = manifest.get("finalization_checks") or {}
    # 恢复汇总已逐项核对原始表，此后只写派生覆盖表和索引，复用完整性检查，避免再扫描数千万条外键。
    reuse_integrity = prior.get("quick_check") == "ok" and prior.get("foreign_keys") == "ok"
    quick = [("ok",)] if reuse_integrity else conn.execute("PRAGMA quick_check").fetchall()
    foreign_keys = [] if reuse_integrity else conn.execute("PRAGMA foreign_key_check").fetchall()
    sources = {r[0]: r[1:] for r in conn.execute(
        "SELECT source_file_id,sha256,revision,license,coverage_json FROM source_manifest")}
    by_file = dict(conn.execute("SELECT source_file_id,COUNT(*) FROM obs_battle GROUP BY source_file_id"))
    file_checks = []
    for item in raw["files"]:
        sfid = f"SRC_SHIYU:{item['file']}@{item['sha256'][:16]}"
        source = sources.get(sfid)
        registered = (source is not None and source[0] == item["sha256"]
                      and source[1] == raw["revision"] and source[2] == download["dataset_card_license"])
        if not registered:
            raise RuntimeError(f"来源修订/哈希/许可登记不一致：{item['file']}")
        if item["kind"] == "battle" and by_file.get(sfid) != item["records"]:
            raise RuntimeError(f"原始战斗行数与入库行数不符：{item['file']}")
        file_checks.append({"file": item["file"], "source_verified": True,
                            "raw_records": item["records"], "imported_battle_records": by_file.get(sfid)})
    totals = dict(zip(("battles", "eligible", "excluded", "eligible_without_result"), conn.execute(
        "SELECT COUNT(*),SUM(is_eligible),SUM(1-is_eligible),"
        "SUM(is_eligible=1 AND score_value IS NULL AND clear_time_seconds IS NULL) FROM obs_battle").fetchone()))
    invalid_cinema = conn.execute("SELECT COUNT(*) FROM obs_team_member WHERE known_cinema IS NOT NULL AND "
                                  "(known_cinema<0 OR known_cinema>6 OR typeof(known_cinema)<>'integer')").fetchone()[0]
    invalid_result = conn.execute("SELECT COUNT(*) FROM obs_battle WHERE is_eligible=1 AND "
                                  "(score_value<0 OR clear_time_seconds<0 OR "
                                  "ABS(score_value)>1.7976931348623157e308 OR "
                                  "ABS(clear_time_seconds)>1.7976931348623157e308)").fetchone()[0]
    modes = [dict(zip(("mode", "raw_records", "eligible_records", "collection_periods"), r)) for r in conn.execute(
        "SELECT mode,COUNT(*),SUM(is_eligible),COUNT(DISTINCT collection_period) FROM obs_battle GROUP BY mode")]
    exclusions = [dict(zip(("reason", "records"), r)) for r in conn.execute(
        "SELECT COALESCE(exclusion_reason,'eligible'),COUNT(*) FROM obs_battle GROUP BY exclusion_reason")]
    dimensions = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("obs_build", "obs_team_member", "obs_coverage")}
    coverage_total = conn.execute("SELECT SUM(raw_records),SUM(eligible_records) FROM obs_coverage").fetchone()
    checks = {"quick_check": quick == [("ok",)], "foreign_keys": not foreign_keys,
              "no_invalid_known_cinema": invalid_cinema == 0,
              "known_results_nonnegative_and_finite": invalid_result == 0,
              "all_raw_battle_rows_imported": totals["battles"] == raw["battle_records"],
              "eligible_plus_excluded_equals_raw": totals["eligible"] + totals["excluded"] == totals["battles"],
              "coverage_reconciles": coverage_total == (totals["battles"], totals["eligible"]),
              "manifest_reconciles": (totals["battles"], totals["eligible"]) ==
                                     (manifest["n_battles"], manifest["n_eligible"]),
              "revision_matches": manifest["revision"] == download["revision"] == raw["revision"]}
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "database": str(db),
              "revision": raw["revision"], "checks": checks, "all_checks_passed": all(checks.values()),
              "integrity_checks_reused_from_verified_finalization": reuse_integrity,
              "totals": totals, "table_rows": dimensions, "by_mode": modes, "exclusions": exclusions,
              "quality_issues": manifest["quality_issues"], "source_file_checks": file_checks,
              "note": "早期部分记录没有成绩，仍保留用于出场统计；成绩统计只使用已知成绩。"}
    target = out / "verification/layer1_import_verification.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "source_file_checks"}, ensure_ascii=False, indent=2))
    if not result["all_checks_passed"]:
        raise RuntimeError("至少一项独立核对未通过")
finally:
    conn.close()
