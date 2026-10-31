"""仅从已完整入库的本次 ShiyuData 数据库恢复索引与汇总，绝不重新导入。

主导入进程停止后才可运行：
    .venv/Scripts/python.exe tools/finalize_existing_observations.py --writer-stopped

CLI固定核验本次HF修订、v3、81批及4,013,856条战斗记录。模块函数的expected
参数仅用于临时smoke副本验收；CLI不提供绕过固定规模/身份校验的开关。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzz_research.analytics import observed as OB
from zzz_research.observations import adapter_shiyu as AD
from zzz_research.observations import quality as QA
from zzz_research.pipeline import DEFAULT_META_DIR, write_csv, write_json


@dataclass(frozen=True)
class ExpectedImport:
    repo_id: str = "LvlUrArti/ShiyuData"
    revision: str = "324c95d5fb05961fa8926168de8768acc8500c1b"
    snapshot_id: str = "SHIYU_HF_324c95d5fb05"
    adapter_version: str = "shiyu_adapter_v3"
    license: str = "mit"
    battle_files: int = 81
    battle_records: int = 4_013_856
    char_files: int = 48
    char_records: int = 7_612_800


class ExistingStore:
    """最小QA/OB适配器；只允许重建派生obs_coverage，不初始化或覆盖数据库。"""

    def __init__(self, connection):
        self.conn = connection

    def query(self, sql, parameters=()):
        return self.conn.execute(sql, parameters).fetchall()

    def write_rows(self, table: str, rows: Sequence[Mapping]) -> None:
        if table != "obs_coverage":
            raise RuntimeError("后备汇总禁止写入原始观测表")
        if rows:
            columns = tuple(rows[0])
            sql = f"INSERT INTO obs_coverage ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
            self.conn.executemany(sql, [tuple(row[c] for c in columns) for row in rows])

    def flush(self) -> None:
        pass  # 所有派生表变更由外层事务统一提交。


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _validate_inputs(raw, download, manifest, expected):
    for label, source in (("raw audit", raw), ("download", download)):
        _require(source.get("repo_id") == expected.repo_id, f"{label}: repo不属于本次固定导入")
        _require(source.get("revision") == expected.revision, f"{label}: HF修订不一致")
    _require(download.get("status") == "completed", "下载尚未完成")
    _require(download.get("dataset_card_license") == expected.license, "下载许可不一致")
    _require(manifest.get("snapshot_id") == expected.snapshot_id, "manifest快照身份不一致")
    _require(manifest.get("revision") == expected.revision, "manifest修订不一致")
    _require(manifest.get("adapter_version") == expected.adapter_version,
             "manifest适配器版本不属于本次固定导入")
    _require(manifest.get("data_license") == expected.license, "manifest数据许可不一致")
    _require(manifest.get("source_url") == download.get("dataset_url"), "manifest来源URL不一致")
    _require(bool(manifest.get("started_at")), "manifest缺原始started_at")
    files = raw.get("files", [])
    _require(len({f["file"] for f in files}) == len(files), "raw audit文件身份重复")
    battle = [f for f in files if f.get("kind") == "battle"]
    chars = [f for f in files if f.get("kind") == "char"]
    _require(len(battle) == expected.battle_files and len(chars) == expected.char_files,
             "raw audit文件数不等于本次固定范围")
    _require(len(files) == len(battle) + len(chars), "raw audit含未知文件类别")
    _require(sum(f["records"] for f in battle) == raw.get("battle_records") == expected.battle_records,
             "raw audit战斗行数不完整")
    _require(sum(f["records"] for f in chars) == raw.get("char_records") == expected.char_records,
             "raw audit角色行数不完整")
    downloads = {f["file"]: f for f in download.get("files", [])}
    _require({f["file"] for f in files} == {name for name in downloads if name.endswith(".csv")},
             "下载清单与raw audit的CSV范围不一致")
    for f in files:
        d = downloads[f["file"]]
        _require(d.get("sha256") == f["sha256"] and d.get("upstream_hash_verified") is True,
                 f"下载哈希或上游校验不一致: {f['file']}")
    return battle, chars


def _validate_database(conn, raw, manifest, expected):
    _require(conn.execute("PRAGMA quick_check").fetchall() == [("ok",)], "数据库quick_check失败")
    _require(not conn.execute("PRAGMA foreign_key_check").fetchall(), "数据库外键检查失败")
    registered = {r[0]: r[1:] for r in conn.execute(
        "SELECT source_file_id,sha256,revision,license,coverage_json FROM source_manifest")}
    actual = dict(conn.execute("SELECT source_file_id,COUNT(*) FROM obs_battle GROUP BY source_file_id"))
    expected_ids = set()
    for f in raw["files"]:
        sfid = f"SRC_SHIYU:{f['file']}@{f['sha256'][:16]}"
        source = registered.get(sfid)
        _require(source is not None, f"缺少已入库来源: {f['file']}")
        _require(source[:3] == (f["sha256"], expected.revision, expected.license),
                 f"已入库来源哈希/修订/许可不一致: {f['file']}")
        coverage = json.loads(source[3])
        period = coverage.get("collection_period")
        suffix = "_char.csv" if f["kind"] == "char" else ("_da.csv" if f["file"].endswith("_da.csv") else ".csv")
        _require(period == f["file"][:-len(suffix)], f"采集期映射不一致: {f['file']}")
        _require(coverage.get("kind") == f["kind"]
                 and coverage.get("base_snapshot_id") == expected.snapshot_id
                 and coverage.get("snapshot_group_id") == f"{expected.snapshot_id}:{period}"
                 and coverage.get("adapter_version") == expected.adapter_version,
                 f"来源身份组/适配器不一致: {f['file']}")
        if f["kind"] == "battle":
            expected_ids.add(sfid)
            _require(actual.get(sfid, 0) == f["records"], f"战斗文件尚未完整入库: {f['file']}")
    _require(set(actual) == expected_ids and len(actual) == expected.battle_files,
             "数据库战斗文件范围不是完整的81批固定范围")
    total, eligible = conn.execute("SELECT COUNT(*),COALESCE(SUM(is_eligible),0) FROM obs_battle").fetchone()
    _require(total == expected.battle_records, "数据库原始战斗行数不完整，禁止恢复汇总")
    return {"battle_files_verified": len(actual), "source_files_verified": len(raw["files"]),
            "n_battles": total, "n_eligible": eligible, "quick_check": "ok", "foreign_keys": "ok"}


def finalize_existing(out_dir: Path, raw_audit_path: Path, download_manifest_path: Path,
                      meta_dir: Path, *, writer_stopped: bool = False,
                      expected: ExpectedImport = ExpectedImport()) -> dict:
    _require(writer_stopped, "必须先停止主导入进程，并显式确认writer_stopped")
    out, meta = Path(out_dir).resolve(), Path(meta_dir).resolve()
    manifest_path, db = out / "observations_manifest.json", out / "observations.sqlite"
    _require(db.is_file(), "现有数据库不存在；后备工具禁止创建或重新导入")
    raw = json.loads(Path(raw_audit_path).read_text("utf-8"))
    download = json.loads(Path(download_manifest_path).read_text("utf-8"))
    original = json.loads(manifest_path.read_text("utf-8"))
    _validate_inputs(raw, download, original, expected)
    manifest = dict(original)
    started, touched = time.perf_counter(), False
    conn = sqlite3.connect(db.as_uri() + "?mode=rw", uri=True, timeout=1, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-131072")
    try:
        conn.execute("BEGIN IMMEDIATE")
        checked = _validate_database(conn, raw, original, expected)
        backup = out / "observations_manifest.before_finalize.json"
        if not backup.exists():
            write_json(backup, original)
        manifest.update(status="running", observed_data_available=False, snapshot_status="pending",
                        finalization_mode="existing_database_only", finalization_started_at=_now())
        write_json(manifest_path, manifest)
        touched = True
        progress = {"phase": "finalizing_existing_database", "batches": expected.battle_files, **checked,
                    "updated_at": _now()}
        write_json(out / "import_progress.json", progress)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_obs_build_identity ON obs_build(snapshot_id,local_uid)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_obs_scope ON obs_battle(snapshot_id,combat_season,mode,is_eligible,stage_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_obs_member_unit ON obs_team_member(unit_id,battle_id)")
        for table in ("obs_build", "obs_battle", "obs_team_member"):
            conn.execute(f"ANALYZE {table}")
        # 索引与统计量独立提交；后续派生输出失败不撤回已完成的查询优化。
        conn.execute("COMMIT")
        conn.execute("BEGIN IMMEDIATE")
        writer = ExistingStore(conn)
        conn.execute("DELETE FROM obs_coverage")  # 只重建派生覆盖表；所有原始obs表均只读。
        coverage = QA.write_obs_coverage(writer)
        exclusions = [{"mode": m, "reason": r, "records": n} for m, r, n in QA.exclusion_summary(writer)]
        # _validate_database 已检查过全部外键，此后只改索引与派生覆盖表。
        issues = QA.check_quality(writer, check_foreign_keys=False)
        characters = AD.load_characters(meta / "characters.json")
        scopes = OB.scopes(writer)
        app_rows, result_rows, investment_rows = [], [], []
        for i, scope in enumerate(scopes, start=1):
            base = {"snapshot_id": scope.snapshot_id, "combat_season": scope.combat_season, "mode": scope.mode}
            appearances = OB.appearance(writer, scope)
            app_rows.extend({**base, **row} for row in appearances)
            result_rows.extend({**base, **row} for row in OB.results_by_stage(writer, scope))
            for row in appearances[:10]:
                value = OB.investment_association(writer, scope, characters, row["unit_id"])
                if value.get("status") == "ok":
                    investment_rows.append({**base, "unit_id": value["unit_id"], "scoring_regime": value["scoring_regime"],
                        "n_c0": value["cinema_0"]["n"], "median_c0": value["cinema_0"]["median"],
                        "n_c1plus": value["cinema_1plus"]["n"], "median_c1plus": value["cinema_1plus"]["median"],
                        "unknown_records": value["cinema_unknown_records"], "label": value["label"]})
            write_json(out / "import_progress.json", {**progress, "phase": "summarizing_existing_database",
                       "scopes_completed": i, "n_scopes": len(scopes), "updated_at": _now()})
        conn.execute("COMMIT")
        vm = AD.load_version_map(meta / "versions_config.json")
        repo_coverage = QA.repo_file_coverage(meta / "repo_files.csv", vm)
        for name, rows in (("repo_file_coverage.csv", [r.__dict__ for r in repo_coverage]),
                           ("obs_coverage.csv", coverage), ("exclusions.csv", exclusions),
                           ("appearance.csv", app_rows), ("results_by_stage.csv", result_rows),
                           ("investment_association.csv", investment_rows)):
            write_csv(out / name, rows)
        write_json(out / "quality_issues.json", issues)
        manifest.update(status="imported_with_issues" if issues else "imported", observed_data_available=True,
                        snapshot_status="imported", path=str(db), n_battles=checked["n_battles"],
                        n_eligible=checked["n_eligible"], n_import_batches=expected.battle_files, n_scopes=len(scopes),
                        n_collection_periods=len(repo_coverage), n_periods_complete=sum(r.status == "complete" for r in repo_coverage),
                        quality_issues=issues, ended_at=_now(), finalization_seconds=time.perf_counter() - started,
                        finalization_checks=checked)
        manifest.pop("error", None)
        write_json(manifest_path, manifest)
        write_json(out / "import_progress.json", {"phase": "completed", "batches": expected.battle_files,
                   "n_battles": checked["n_battles"], "n_eligible": checked["n_eligible"],
                   "finalization_mode": "existing_database_only", "updated_at": _now()})
        return manifest
    except BaseException as exc:
        if conn.in_transaction:
            conn.rollback()
        if touched:
            manifest.update(status="failed", observed_data_available=False, snapshot_status="pending",
                            error=f"{type(exc).__name__}: {exc}", ended_at=_now())
            write_json(manifest_path, manifest)
            write_json(out / "import_progress.json", {"phase": "finalization_failed", "error": manifest["error"],
                       "raw_database_preserved": True, "updated_at": _now()})
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--writer-stopped", action="store_true", help="明确确认主导入进程已停止")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "outputs/benchmark_n200/observations")
    parser.add_argument("--raw-audit", type=Path, default=ROOT / "outputs/benchmark_n200/verification/shiyu_raw_audit.json")
    parser.add_argument("--download-manifest", type=Path, default=ROOT / "data/raw/shiyu_hf/download_manifest.json")
    parser.add_argument("--meta-dir", type=Path, default=ROOT / DEFAULT_META_DIR)
    args = parser.parse_args()
    if not args.writer_stopped:
        parser.error("须先停止主导入进程；仅在确认后加 --writer-stopped")
    _require(AD.ADAPTER_VERSION == ExpectedImport().adapter_version, "当前适配器不是本次固定v3")
    result = finalize_existing(args.out_dir, args.raw_audit, args.download_manifest, args.meta_dir,
                               writer_stopped=True)
    print(json.dumps({k: result[k] for k in ("status", "n_battles", "n_eligible", "n_import_batches",
                                            "n_scopes", "quality_issues", "finalization_seconds")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
