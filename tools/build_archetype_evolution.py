"""Count observed mechanism coverage per season without altering the observation store."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzz_research.pipeline import project_path

GROUPS = (
    "常规直伤/失衡", "异常/紊乱异放", "追加攻击", "命破贯穿",
    "帷幕无视失衡", "耀变体系", "锐化防御",
)
FAMILY_GROUP = {
    "常规直伤": "常规直伤/失衡", "常规失衡": "常规直伤/失衡",
    "异常伤害/积蓄": "异常/紊乱异放", "紊乱异放": "异常/紊乱异放",
    "追加攻击": "追加攻击", "贯穿": "命破贯穿",
    "帷幕无视失衡": "帷幕无视失衡", "耀变体系": "耀变体系", "锐化防御": "锐化防御",
}
SCOPES_SQL = """
SELECT snapshot_id, combat_season, mode, COUNT(*)
FROM obs_battle WHERE is_eligible=1
GROUP BY snapshot_id, combat_season, mode
"""
ROSTERS_SQL = """
SELECT m1.unit_id, m2.unit_id, m3.unit_id
FROM obs_battle b
LEFT JOIN obs_team_member m1 ON m1.battle_id=b.battle_id AND m1.team_slot=1
LEFT JOIN obs_team_member m2 ON m2.battle_id=b.battle_id AND m2.team_slot=2
LEFT JOIN obs_team_member m3 ON m3.battle_id=b.battle_id AND m3.team_slot=3
WHERE b.snapshot_id=? AND b.combat_season=? AND b.mode=? AND b.is_eligible=1
"""


def version_key(season):
    return tuple(map(int, season.split(".")))


def alpha(values):
    return tuple(sorted(values, key=lambda unit: (unit.casefold(), unit)))


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def stat_record(path):
    value = path.stat()
    return {"bytes": value.st_size, "mtime_ns": value.st_mtime_ns, "file_id": value.st_ino}


def build(out):
    started = time.perf_counter()
    obs = out / "observations"
    db = obs / "observations.sqlite"
    unit_path, comp_path = out / "dim_observed_archetype.csv", out / "observed_comp_archetype.csv"
    rules_path = ROOT / "tools/observed_archetype_rules.json"
    outputs = {"archetype_evolution.csv", "archetype_evolution_manifest.json"}
    protected = {str(p): stat_record(p) for p in obs.iterdir() if p.is_file() and p.name not in outputs}
    source_stats = {str(p): stat_record(p) for p in (unit_path, comp_path, rules_path)}
    units = {row["unit_id"]: row for row in read_csv(unit_path)}
    comp_rows = read_csv(comp_path)
    rules = json.loads(rules_path.read_text("utf-8"))
    records, expected_counts, comp_groups = {}, Counter(), {}
    for row in comp_rows:
        members = alpha(json.loads(row["comp_units"]))
        if members in records:
            raise ValueError(f"Duplicate composition mapping: {members}")
        records[members] = row
        expected_counts[members] = int(row["eligible_records"])
        groups = set()
        if row["is_valid_3man"] == "1":
            assert len(members) == len(set(members)) == 3 and set(members) <= units.keys()
            for core in json.loads(row["core_units"]):
                groups.add(FAMILY_GROUP[units[core]["mechanism_family"]])
        comp_groups[members] = groups
    assert len(units) == 60
    expected_valid = sum(int(row["eligible_records"]) for row in comp_rows if row["is_valid_3man"] == "1")
    eligible, valid, mixed, no_core, coverage = Counter(), Counter(), Counter(), Counter(), Counter()
    quality = defaultdict(Counter)
    seen_comps = Counter()
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA cache_size=-65536")
    con.execute("BEGIN")
    try:
        scopes = list(con.execute(SCOPES_SQL))
        expected = sum(scope[3] for scope in scopes)
        assert expected == sum(expected_counts.values())
        for snapshot, season, mode, scope_count in sorted(scopes, key=lambda row: (
                version_key(row[1]), row[0], row[2])):
            count = 0
            for row in con.execute(ROSTERS_SQL, (snapshot, season, mode)):
                members = alpha(unit for unit in row if unit is not None and unit.strip())
                comp = records[members]
                count += 1
                eligible[season] += 1
                seen_comps[members] += 1
                quality[season][comp["quality_status"]] += 1
                if comp["is_valid_3man"] != "1":
                    continue
                valid[season] += 1
                groups = comp_groups[members]
                mixed[season] += len(groups) > 1
                no_core[season] += not groups
                for group in groups:
                    coverage[season, group] += 1
            assert count == scope_count, (snapshot, season, mode, count, scope_count)
            print(f"{season} {mode}: {sum(eligible.values()):,}/{expected:,} eligible records", flush=True)
    finally:
        con.close()
    assert seen_comps == expected_counts, "Observed composition counts differ from the classified mapping"
    assert sum(valid.values()) == expected_valid
    assert all(count <= valid[season] for (season, _), count in coverage.items())
    assert all(stat_record(Path(p)) == value for p, value in protected.items()), "An observation source changed"
    assert all(stat_record(Path(p)) == value for p, value in source_stats.items()), "A classification input changed"
    seasons = sorted(eligible, key=version_key)
    rows = [{"combat_season": season, "mechanism_group": group, "records": coverage[season, group],
             "valid_3man_records": valid[season],
             "share": coverage[season, group] / valid[season] if valid[season] else None}
            for season in seasons for group in GROUPS]
    with (obs / "archetype_evolution.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=("combat_season", "mechanism_group", "records", "valid_3man_records", "share"))
        writer.writeheader()
        writer.writerows(rows)
    first_seen = {group: next((season for season in seasons if coverage[season, group]), None) for group in GROUPS}
    peak = {}
    for group in GROUPS:
        observed = [row for row in rows if row["mechanism_group"] == group and row["share"] is not None]
        winner = max(observed, key=lambda row: row["share"])
        peak[group] = {key: winner[key] for key in ("combat_season", "records", "valid_3man_records", "share")}
    latest = seasons[-1]
    manifest = {
        "status": "completed", "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "statistics": "per-season record-level mechanism coverage, not mutually exclusive composition shares",
        "denominator": "is_eligible=1 and mapped composition is_valid_3man=1; 3 distinct known characters",
        "multiple_cores": "Each battle contributes at most once to each broad group; groups may overlap and must not be stacked or summed to 100%.",
        "group_order": list(GROUPS), "family_group_mapping": FAMILY_GROUP,
        "source_database": project_path(db), "source_unit_dimension": project_path(unit_path),
        "source_comp_mapping": project_path(comp_path),
        "classification_rule_version": rules["schema_version"], "classification_definition_date": rules["definition_date"],
        "eligible_records": sum(eligible.values()), "valid_3man_records": sum(valid.values()),
        "excluded_records": sum(eligible.values()) - sum(valid.values()), "distinct_compositions": len(seen_comps),
        "n_seasons": len(seasons), "season_range": [seasons[0], seasons[-1]], "season_order": seasons,
        "mixed_mechanism_records": sum(mixed.values()), "no_core_records": sum(no_core.values()),
        "season_counts": [{"combat_season": season, "eligible_records": eligible[season],
                           "valid_3man_records": valid[season], "mixed_mechanism_records": mixed[season],
                           "no_core_records": no_core[season], "quality_records": dict(quality[season])}
                          for season in seasons],
        "first_seen_group": first_seen,
        "latest_group_share": {group: coverage[latest, group] / valid[latest] for group in GROUPS},
        "peak_group_share": peak,
        "first_seen_note": "First seen is an observed combat-season boundary, not an official character release version.",
        "time_axis_note": "Seasons are ordered by integer tuples; missing seasons are not filled and equal spacing does not mean equal elapsed days.",
        "observation_sources_unchanged": True, "classification_sources_unchanged": True,
        "protected_observation_file_stats": protected, "classification_input_file_stats": source_stats,
        "all_composition_counts_reconciled": True, "query_and_aggregation_seconds": round(time.perf_counter() - started, 3),
    }
    (obs / "archetype_evolution_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: manifest[key] for key in ("eligible_records", "valid_3man_records", "n_seasons",
          "first_seen_group", "latest_group_share", "query_and_aggregation_seconds")}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / "outputs/final_release")
    args = parser.parse_args()
    build(args.dir.resolve())


if __name__ == "__main__":
    main()
