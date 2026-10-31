"""Read existing Deadly Assault observations and summarize verified room/boss associations."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
import statistics
import time
from array import array
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from build_archetype_evolution import FAMILY_GROUP, GROUPS, alpha, version_key
from zzz_research.pipeline import project_path


ROOT = Path(__file__).resolve().parents[1]
STAGE_SLOT = {"1-1": "S1", "1-2": "S2", "1-3": "S3", "2-1": "ADV"}
ROSTER_SQL = """
SELECT b.stage_id,b.score_value,b.source_row_locator,m1.unit_id,m2.unit_id,m3.unit_id
FROM obs_battle b
LEFT JOIN obs_team_member m1 ON m1.battle_id=b.battle_id AND m1.team_slot=1
LEFT JOIN obs_team_member m2 ON m2.battle_id=b.battle_id AND m2.team_slot=2
LEFT JOIN obs_team_member m3 ON m3.battle_id=b.battle_id AND m3.team_slot=3
WHERE b.snapshot_id=? AND b.combat_season=? AND b.mode='deadly_assault' AND b.is_eligible=1
"""
COLUMNS = (
    "scope", "combat_season", "stage_slot", "stage_scope", "boss_zh", "boss_en",
    "mech_regime", "mechanism_code", "mech_tag", "association_status", "archetype_group",
    "eligible_records", "valid_3man_records", "invalid_or_partial_records", "known_score_records",
    "missing_score_records", "median_score", "archetype_records", "archetype_share",
    "archetype_known_score_records", "archetype_median_score", "snapshot_count",
)


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows, columns):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def file_stat(path):
    stat = Path(path).stat()
    return {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "file_id": stat.st_ino}


def normalize_boss(value):
    return re.sub(r"[^\w]", "", value.casefold())


def score_number(value):
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


class Counts:
    def __init__(self):
        self.eligible = 0
        self.valid = 0
        self.scores = array("d")
        self.groups = Counter()
        self.group_scores = defaultdict(lambda: array("d"))
        self.snapshots = set()

    def add(self, valid, groups, score, snapshot):
        self.eligible += 1
        self.valid += valid
        self.snapshots.add(snapshot)
        if score is not None:
            self.scores.append(score)
        if valid:
            for group in groups:
                self.groups[group] += 1
                if score is not None:
                    self.group_scores[group].append(score)

    def summary(self):
        return {"eligible_records": self.eligible, "valid_3man_records": self.valid,
                "invalid_or_partial_records": self.eligible - self.valid,
                "known_score_records": len(self.scores),
                "missing_score_records": self.eligible - len(self.scores),
                "median_score": statistics.median(self.scores) if self.scores else None,
                "snapshot_count": len(self.snapshots)}


def raw_labels(raw_dir, collection, season, dimensions):
    """Retain raw-column conflicts by line; aliases never cross boss identities."""
    path = raw_dir / f"{collection}_da.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Existing raw source is needed for boss-label association: {path}")
    conflicts = {}
    raw_counts = Counter()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for line, row in enumerate(csv.DictReader(stream), 2):
            try:
                floor = int(row["floor"])
            except (KeyError, ValueError):
                conflicts[line] = {"raw_boss_en": row.get("boss", ""), "reason": "raw_floor_unknown"}
                continue
            slot = "ADV" if floor > 3 else f"S{floor}"
            boss = (row.get("boss") or "").strip()
            raw_counts[slot, boss] += 1
            expected = dimensions.get((season, slot))
            aliases = ({normalize_boss(expected["boss_en"])}
                       | {normalize_boss(alias) for alias in json.loads(expected.get("raw_boss_aliases") or "[]")}) if expected else set()
            if not expected or not boss or normalize_boss(boss) not in aliases:
                conflicts[line] = {"raw_boss_en": boss, "raw_stage_slot": slot,
                                   "expected_boss_en": expected["boss_en"] if expected else "",
                                   "reason": "raw_boss_label_conflict" if expected else "history_room_missing"}
    return conflicts, [{"stage_slot": slot, "raw_boss_en": boss, "raw_records": n}
                       for (slot, boss), n in sorted(raw_counts.items())]


def metadata(scope, season="", slot="", track="normal", dim=None, **extra):
    d = dim or {}
    return {"scope": scope, "combat_season": season, "stage_slot": slot, "stage_scope": track,
            "boss_zh": d.get("boss_zh", ""), "boss_en": d.get("boss_en", ""),
            "mech_regime": d.get("mech_regime", ""), "mechanism_code": d.get("mechanism_code", ""),
            "mech_tag": d.get("mech_tag", ""), "association_status": "exact_room_raw_label_confirmed", **extra}


def current_dimension_labels(out, rows):
    """Use the current dimension's descriptions with already aggregated numbers."""
    dimensions = read_csv(out / "dim_deadly_assault_bosses.csv")
    by_room = {(row["combat_season"], row["stage_slot"]): row for row in dimensions}
    by_boss = {(row["boss_zh"], row["mechanism_code"]): row for row in dimensions}
    by_mechanism = {row["mechanism_code"]: row for row in dimensions}
    for row in rows:
        if row["scope"] == "season_room":
            dim = by_room[row["combat_season"], row["stage_slot"]]
            for key in ("boss_zh", "boss_en", "mech_regime", "mechanism_code", "mech_tag"):
                row[key] = dim[key]
        elif row["scope"] == "boss":
            dim = by_boss[row["boss_zh"], row["mechanism_code"]]
            row["boss_en"], row["mech_tag"] = dim["boss_en"], dim["mech_tag"]
        elif row["scope"] == "mechanism":
            row["mech_tag"] = by_mechanism[row["mechanism_code"]]["mech_tag"]
    return rows


def build(out, raw_dir):
    started = time.perf_counter()
    db = out / "observations/observations.sqlite"
    protected = {str(path): file_stat(path) for path in (out / "observations").iterdir() if path.is_file()}
    dimensions = {(r["combat_season"], r["stage_slot"]): r for r in read_csv(out / "dim_deadly_assault_bosses.csv")}
    units = {r["unit_id"]: r for r in read_csv(out / "dim_observed_archetype.csv")}
    classifications = {}
    for row in read_csv(out / "observed_comp_archetype.csv"):
        key = alpha(json.loads(row["comp_units"]))
        valid = row["is_valid_3man"] == "1"
        groups = {FAMILY_GROUP[units[unit]["mechanism_family"]] for unit in json.loads(row["core_units"])} if valid else set()
        classifications[key] = (valid, groups)
    coverage = [r for r in read_csv(out / "observations/obs_coverage.csv") if r["mode"] == "deadly_assault"]
    scopes = defaultdict(int)
    for row in coverage:
        scopes[row["source_snapshot_id"], row["combat_season"]] += int(row["eligible_records"])
    buckets = defaultdict(Counts)
    labels = {}
    audit = []
    sources = []
    matched = Counter()
    unresolved = Counter()
    for (season, slot), dim in dimensions.items():
        track = "advanced" if slot == "ADV" else "normal"
        key = ("season_room", season, slot, track)
        labels[key] = metadata("season_room", season, slot, track, dim)
        buckets[key]
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA cache_size=-65536")
    con.execute("BEGIN")
    try:
        total_expected = sum(scopes.values())
        total_seen = 0
        for (snapshot, season), expected in sorted(scopes.items(), key=lambda item: (version_key(item[0][1]), item[0][0])):
            collection = snapshot.rsplit(":", 1)[-1]
            conflicts, raw_counts = raw_labels(raw_dir, collection, season, dimensions)
            sources.append({"snapshot_id": snapshot, "collection_period": collection, "combat_season": season,
                            "raw_file": f"{collection}_da.csv", "raw_boss_counts": raw_counts})
            seen = 0
            for stage_id, score, locator, *members in con.execute(ROSTER_SQL, (snapshot, season)):
                seen += 1
                slot = STAGE_SLOT.get(stage_id, "UNKNOWN")
                track = "advanced" if slot == "ADV" else "normal" if slot != "UNKNOWN" else "unknown"
                dim = dimensions.get((season, slot))
                members = alpha(unit for unit in members if unit and unit.strip())
                valid, groups = classifications[members]
                score = score_number(score)
                line = int(locator.rsplit(":", 1)[-1])
                problem = conflicts.get(line)
                season_key = ("season", season, track)
                labels[season_key] = metadata("season", season, "ALL", track,
                    mech_regime=dim["mech_regime"] if dim else "", association_status="season_observation")
                buckets[season_key].add(valid, groups, score, snapshot)
                if dim:
                    regime_key = ("regime", dim["mech_regime"], track)
                    labels[regime_key] = metadata("regime", slot="ALL", track=track,
                        mech_regime=dim["mech_regime"], association_status="historical_era_observation")
                    buckets[regime_key].add(valid, groups, score, snapshot)
                if problem or dim is None:
                    unresolved[track] += 1
                    key = ("unresolved_room", season, slot, track)
                    labels[key] = metadata("unresolved_room", season, slot, track,
                        mech_regime=dim["mech_regime"] if dim else "", association_status="raw_label_conflict_unassigned")
                    buckets[key].add(valid, groups, score, snapshot)
                    audit.append({"snapshot_id": snapshot, "combat_season": season, "stage_id": stage_id,
                                  "stage_slot": slot, "source_row_locator": locator,
                                  "raw_boss_en": (problem or {}).get("raw_boss_en", ""),
                                  "expected_boss_en": dim["boss_en"] if dim else "",
                                  "reason": (problem or {}).get("reason", "history_room_missing")})
                    continue
                matched[track] += 1
                room_key = ("season_room", season, slot, track)
                buckets[room_key].add(valid, groups, score, snapshot)
                boss_key = ("boss", dim["boss_zh"], dim["mechanism_code"], track)
                labels[boss_key] = metadata("boss", slot="ALL", track=track, dim=dim, mech_regime="跨时期汇总")
                buckets[boss_key].add(valid, groups, score, snapshot)
                mechanism_key = ("mechanism", dim["mechanism_code"], track)
                labels[mechanism_key] = metadata("mechanism", slot="ALL", track=track,
                    mechanism_code=dim["mechanism_code"], mech_tag=dim["mech_tag"], mech_regime="跨时期汇总")
                buckets[mechanism_key].add(valid, groups, score, snapshot)
            if seen != expected:
                raise ValueError(f"Coverage and eligible DB rows differ: {snapshot} {seen} != {expected}")
            total_seen += seen
            print(f"{season} / {collection}: {total_seen:,}/{total_expected:,}", flush=True)
    finally:
        con.close()
    rows = []
    for key, counts in buckets.items():
        common = {**labels[key], **counts.summary()}
        if common["scope"] == "season_room" and not counts.eligible:
            common["association_status"] = "no_confirmed_observation"
        for group in GROUPS:
            group_scores = counts.group_scores[group]
            rows.append({**common, "archetype_group": group, "archetype_records": counts.groups[group],
                         "archetype_share": counts.groups[group] / counts.valid if counts.valid else None,
                         "archetype_known_score_records": len(group_scores),
                         "archetype_median_score": statistics.median(group_scores) if group_scores else None})
    rows.sort(key=lambda r: (r["scope"], version_key(r["combat_season"]) if r["combat_season"] else (),
                             r["stage_scope"], r["stage_slot"], r["boss_zh"], r["mechanism_code"], GROUPS.index(r["archetype_group"])))
    if total_seen != sum(matched.values()) + sum(unresolved.values()):
        raise ValueError("Associated plus unassigned records do not reconcile")
    for path, before in protected.items():
        if file_stat(path) != before:
            raise ValueError(f"Observation input changed: {path}")
    write_csv(out / "boss_mechanism_archetype_stats.csv", current_dimension_labels(out, rows), COLUMNS)
    write_csv(out / "boss_association_issues.csv", audit,
              ("snapshot_id", "combat_season", "stage_id", "stage_slot", "source_row_locator",
               "raw_boss_en", "expected_boss_en", "reason"))
    historic = sorted({s for s, _ in dimensions}, key=version_key)
    observed = sorted({s for _, s in scopes}, key=version_key)
    totals = {}
    for track in ("normal", "advanced"):
        selected = [v for k, v in buckets.items() if k[0] == "season" and k[-1] == track]
        totals[track] = {"eligible_records": sum(v.eligible for v in selected),
                         "valid_3man_records": sum(v.valid for v in selected),
                         "known_score_records": sum(len(v.scores) for v in selected),
                         "matched_boss_records": matched[track], "unassigned_boss_records": unresolved[track]}
    manifest = {
        "status": "completed", "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_database": project_path(db), "source_mode": "deadly_assault", "stage_mapping": STAGE_SLOT,
        "stage_mapping_basis": "adapter_shiyu.py: raw floor 1/2/3 -> 1-1/1-2/1-3; raw floor>3 -> 2-1 (advanced)",
        "association_basis": "combat_season + stage_slot, confirmed against each raw-row boss label or registered identity alias",
        "unassigned_policy": "Raw boss-label conflicts remain in season and historical-era totals, excluded from definite room/boss/mechanism aggregates.",
        "raw_conflict_audit": "boss_association_issues.csv", "raw_conflict_records": len(audit),
        "history_season_count": len(historic), "history_room_count": len(dimensions),
        "observed_season_count": len(observed), "observed_snapshot_count": len(scopes),
        "history_unobserved_seasons": [s for s in historic if s not in observed],
        "observed_seasons": observed, "history_seasons": historic,
        "eligible_records": total_seen, "matched_boss_records": sum(matched.values()),
        "valid_3man_records": sum(row["valid_3man_records"] for row in totals.values()),
        "unassigned_boss_records": sum(unresolved.values()), "stage_scope_totals": totals,
        "regime_basis": "Five historical eras. Each era contains legacy bosses as well as new mechanics.",
        "archetype_groups": list(GROUPS),
        "denominator": "archetype_share uses identity-valid, distinct full three-agent eligible records within the same scope and track",
        "overlap": "A multi-core battle may appear in multiple mechanism groups; shares cannot be added or stacked to 100%.",
        "score_medians": "Exact median of known score values in each eligible scope; archetype medians use only valid full-three-agent records in that group. Missing scores remain missing, not zero.",
        "advanced_policy": "Normal rooms and advanced rooms remain separate in every aggregate and median.",
        "snapshot_policy": "Records are pooled across fixed snapshots; player identities are not joined or deduplicated across collections. Two 2.5.1 combat-season collections remain distinct records.",
        "sources": sources, "observation_sources_unchanged": True,
        "query_and_aggregation_seconds": round(time.perf_counter() - started, 3),
    }
    (out / "boss_mechanism_stats_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("eligible_records", "matched_boss_records", "unassigned_boss_records",
                     "observed_season_count", "observed_snapshot_count", "history_unobserved_seasons", "query_and_aggregation_seconds")}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / "outputs/final_release")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw/shiyu_hf")
    args = parser.parse_args()
    build(args.dir.resolve(), args.raw_dir.resolve())


if __name__ == "__main__":
    main()
