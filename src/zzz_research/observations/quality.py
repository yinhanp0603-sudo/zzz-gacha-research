"""M-OBS 3.1 步骤 3–4：覆盖矩阵与质量检查。

- 公开文件覆盖：来自固定修订的 repo_files.csv 与采集期映射（真实元数据，不含战斗记录）。
- 快照覆盖：来自已导入的 obs_* 表，写入 obs_coverage。
缺期如实保留，不连成连续观测。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .adapter_shiyu import MODE_DA, MODE_SD, CollectionPeriod, regime_for

SMALL_SAMPLE_PLAYERS = 20  # observations.md 3.2：项目展示门槛，并非上游源码事实。


@dataclass(frozen=True)
class FileCoverage:
    collection_period: str
    collect_date: str
    sd_season: Optional[str]
    da_season: Optional[str]
    has_sd_battle: bool
    has_da_battle: bool
    has_char: bool
    has_build: bool
    sd_regime: str
    status: str


def repo_file_coverage(repo_files_csv: Path | str,
                       version_map: Mapping[str, CollectionPeriod]) -> list[FileCoverage]:
    """每个采集期在公开清单中是否有战斗/角色/配装文件。清单存在≠每个战斗赛季均有文件。"""
    with open(repo_files_csv, newline="", encoding="utf-8") as f:
        names = {row[0].strip() for row in csv.reader(f) if row and row[0].strip()}
    out = []
    for ver in sorted(version_map, key=lambda v: (version_map[v].collect_date, v)):
        p = version_map[ver]
        sd, da = f"{ver}.csv" in names, f"{ver}_da.csv" in names
        ch, bd = f"{ver}_char.csv" in names, f"{ver}_build.csv" in names
        missing = []
        if p.sd_season and not sd:
            missing.append("sd_battle")
        if p.da_season and not da:
            missing.append("da_battle")
        if (sd or da) and not ch:
            missing.append("char")
        out.append(FileCoverage(ver, p.collect_date.isoformat(), p.sd_season, p.da_season, sd, da, ch, bd,
                                regime_for(MODE_SD, p.collect_date)["regime"],
                                "complete" if not missing else "missing:" + ",".join(missing)))
    return out


def compute_obs_coverage(writer: Any) -> list[dict]:
    """按 (快照, 实际赛季, 模式, 关卡) 汇总原始/合格记录、唯一样本人数、配装已知人数。"""
    rows = writer.query("""
        SELECT b.snapshot_id, COALESCE(b.combat_season,'UNKNOWN'), b.mode, COALESCE(b.stage_id,'UNKNOWN'),
               COUNT(*), SUM(b.is_eligible),
               COUNT(DISTINCT CASE WHEN b.is_eligible=1 THEN b.local_uid END),
               COUNT(DISTINCT CASE WHEN b.is_eligible=1 AND EXISTS (
                     SELECT 1 FROM obs_build ob WHERE ob.snapshot_id=b.snapshot_id AND ob.local_uid=b.local_uid)
                     THEN b.local_uid END),
               MIN(b.scoring_regime), COUNT(DISTINCT b.scoring_regime)
        FROM obs_battle b GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""")
    out = []
    for snap, season, mode, stage, raw, elig, uniq, known, regime, n_regime in rows:
        status = "ok"
        if n_regime > 1:
            status = "mixed_regime"
        elif uniq < SMALL_SAMPLE_PLAYERS:
            status = "small_sample"
        out.append({"source_snapshot_id": snap, "combat_season": season, "mode": mode, "stage_id": stage,
                    "raw_records": raw, "eligible_records": elig or 0, "unique_players": uniq,
                    "known_build_players": known, "scoring_regime": regime, "status": status})
    return out


def write_obs_coverage(writer: Any) -> list[dict]:
    rows = compute_obs_coverage(writer)
    writer.write_rows("obs_coverage", rows)
    writer.flush()
    return rows


def exclusion_summary(writer: Any) -> list[tuple[str, str, int]]:
    return writer.query("SELECT mode, COALESCE(exclusion_reason,'eligible'), COUNT(*) FROM obs_battle "
                        "GROUP BY 1,2 ORDER BY 1,2")


def check_quality(writer: Any, *, check_foreign_keys: bool = True) -> list[str]:
    """返回问题列表：外键、合格行 UID、队伍与成绩。

    仅在同一数据库的原始表已核对且此后未改动时，才可复用外键检查结果。
    """
    issues = []
    if check_foreign_keys and writer.query("PRAGMA foreign_key_check"):
        issues.append("foreign_key_violation")
    missing_uid = writer.query("SELECT COUNT(*) FROM obs_battle WHERE is_eligible=1 AND "
                               "(local_uid IS NULL OR TRIM(local_uid)='')")[0][0]
    if missing_uid:
        issues.append(f"eligible_without_uid:{missing_uid}")
    bad = writer.query("SELECT COUNT(*) FROM obs_battle WHERE is_eligible=1 AND "
                       "(score_value IS NULL AND clear_time_seconds IS NULL)")[0][0]
    if bad:
        issues.append(f"eligible_without_result:{bad}")
    no_team = writer.query("SELECT COUNT(*) FROM obs_battle b WHERE is_eligible=1 AND NOT EXISTS "
                           "(SELECT 1 FROM obs_team_member m WHERE m.battle_id=b.battle_id)")[0][0]
    if no_team:
        issues.append(f"eligible_without_team:{no_team}")
    mixed = writer.query("SELECT COUNT(*) FROM (SELECT combat_season, mode, stage_id FROM obs_battle "
                         "GROUP BY 1,2,3 HAVING COUNT(DISTINCT scoring_regime)>1)")[0][0]
    if mixed:
        issues.append(f"mixed_scoring_regime_groups:{mixed}")
    return issues


MODES: Sequence[str] = (MODE_SD, MODE_DA)
