"""Real main-DPS deployment longevity and support-release event windows from observed clears.

Reads the finalized observation database once; does not import, download or run L2/L3.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from array import array
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import numpy as np

from empirical_common import ROOT, json_text, read_csv, weighted_geomean, write_csv

QUERY = """
SELECT b.local_uid,b.stage_id,b.score_value,b.source_row_locator,
       m1.unit_id,m1.known_cinema,m1.known_weapon,
       m2.unit_id,m2.known_cinema,m2.known_weapon,
       m3.unit_id,m3.known_cinema,m3.known_weapon
FROM obs_battle b
LEFT JOIN obs_team_member m1 ON m1.battle_id=b.battle_id AND m1.team_slot=1
LEFT JOIN obs_team_member m2 ON m2.battle_id=b.battle_id AND m2.team_slot=2
LEFT JOIN obs_team_member m3 ON m3.battle_id=b.battle_id AND m3.team_slot=3
WHERE b.snapshot_id=? AND b.combat_season=? AND b.mode=? AND b.is_eligible=1
"""
STAGES = {"1-1": "S1", "1-2": "S2", "1-3": "S3", "2-1": "ADV"}
MODE_KEY = {"shiyu_defense": "sd", "deadly_assault": "da"}


def season_dates(meta_dir):
    versions = json.loads((meta_dir / "versions_config.json").read_text("utf-8"))
    dates = {}
    for entry in versions.values():
        for mode, key in MODE_KEY.items():
            block = entry.get(key)
            if block:
                dates[mode, block["ver"]] = datetime.strptime(block["start"], "%d/%m/%Y").date()
    return dates


def quartiles(values):
    if not values:
        return None, None, None
    p25, p50, p75 = np.percentile(np.frombuffer(values, dtype="d") if isinstance(values, array) else values,
                                  [25, 50, 75])
    return float(p25), float(p50), float(p75)


class Scan:
    def __init__(self, out, spec):
        self.spec = spec
        self.tracked = spec["tracked_dps"]
        self.catalog = json.loads((ROOT / "data/raw/SRC_SHIYU_meta_d930cec50bfd/characters.json").read_text("utf-8"))
        self.comps = {tuple(sorted(json.loads(r["comp_units"]))): r for r in read_csv(out / "observed_comp_archetype.csv")
                      if r["is_valid_3man"] == "1"}
        self.issues = {(r["snapshot_id"], r["source_row_locator"]) for r in read_csv(out / "boss_association_issues.csv")}
        self.valid = Counter()
        self.appear = Counter()
        self.room = defaultdict(lambda: array("d"))
        self.track_scores = defaultdict(lambda: array("d"))
        self.event_scores = defaultdict(lambda: array("d"))
        self.attach = Counter()
        self.quality = Counter()
        self.events = spec["events"]
        self.release = {e["event_id"]: self.catalog[e["component"]]["release_date"] for e in self.events}

    def legacy(self, unit, event_id):
        info = self.catalog[unit]
        return (info["availability"] in ("A", "Standard S", "Free S")
                or (info["release_date"] < self.release[event_id] and info["role"] in ("support", "subdps")))

    def consume(self, snapshot, season, mode, row):
        uid, stage, score, locator, *team = row
        members = [(team[i], team[i + 1], team[i + 2] or None) for i in range(0, 9, 3) if team[i] and team[i].strip()]
        names = tuple(sorted(m[0] for m in members))
        comp = self.comps.get(names)
        if comp is None:
            return
        self.valid[mode, season] += 1
        for unit in names:
            self.appear[mode, season, unit] += 1
        for event in self.events:
            if event["main"] in names and event["component"] in names:
                self.attach[mode, season, event["event_id"]] += 1
        if mode != "deadly_assault":
            return
        if (snapshot, locator) in self.issues:
            self.quality["unresolved_boss_record"] += 1
            return
        if score is None or score <= 0:
            self.quality["missing_or_nonpositive_score"] += 1
            return
        room = season, STAGES[stage]
        self.room[room].append(score)
        cores = json.loads(comp["core_units"])
        by_unit = {m[0]: m for m in members}
        for unit, weapon in self.tracked.items():
            main = by_unit.get(unit)
            if main and cores == [unit] and main[1] == 0 and main[2] == weapon:
                self.track_scores[unit, *room].append(score)
        for event in self.events:
            main = by_unit.get(event["main"])
            if not main or main[1] != 0 or main[2] != self.tracked[event["main"]]:
                continue
            component, event_id = event["component"], event["event_id"]
            others = [u for u in names if u != event["main"]]
            legacy = [u for u in others if self.legacy(u, event_id)]
            if component in names:
                self.event_scores[event_id, "main_build_matched", *room, "", "", 1].append(score)
                fixed = next(u for u in others if u != component)
                self.event_scores[event_id, "fixed_third_matched", *room, fixed,
                                  self.catalog[component]["specialty"], 1].append(score)
            elif legacy:
                self.event_scores[event_id, "main_build_matched", *room, "", "", 0].append(score)
                specialty = self.catalog[component]["specialty"]
                for replacement in legacy:
                    if self.catalog[replacement]["specialty"] == specialty:
                        fixed = next(u for u in others if u != replacement)
                        self.event_scores[event_id, "fixed_third_matched", *room, fixed, specialty, 0].append(score)


def mode_order(dates, valid, minimum):
    orders = {}
    for mode in MODE_KEY:
        seasons = sorted({s for m, s in valid if m == mode}, key=lambda s: (dates[mode, s], s))
        orders[mode] = [s for s in seasons if valid[mode, s] >= minimum]
    return orders


def longevity_rows(scan, dates, orders, units_zh, bosses):
    rows, summaries = [], []
    for unit in scan.tracked:
        for mode, seasons in orders.items():
            shares = [(s, scan.appear[mode, s, unit] / scan.valid[mode, s]) for s in seasons if scan.appear[mode, s, unit]]
            if not shares:
                continue
            first = shares[0][0]
            start = seasons.index(first)
            track = seasons[start:]
            peak_season, peak = max(shares, key=lambda item: item[1])
            crossings = {}
            for level in (0.5, 0.25):
                hit = next((s for s in track[track.index(peak_season):]
                            if scan.appear[mode, s, unit] / scan.valid[mode, s] < level * peak), None)
                crossings[level] = hit
            for index, season in enumerate(track):
                share = scan.appear[mode, season, unit] / scan.valid[mode, season]
                rows.append({"scope": "appearance_track", "unit_id": unit, "unit_zh": units_zh[unit], "mode": mode,
                             "combat_season": season, "season_start": dates[mode, season].isoformat(),
                             "delta_t_seasons": index, "days_since_first": (dates[mode, season] - dates[mode, first]).days,
                             "n_valid_3man": scan.valid[mode, season], "n_unit_records": scan.appear[mode, season, unit],
                             "appearance_per_clear": share, "retention_vs_peak": share / peak,
                             "denominator": "valid_full_three_agent_records"})
            summary = {"scope": "unit_summary", "unit_id": unit, "unit_zh": units_zh[unit], "mode": mode,
                       "first_season": first, "first_season_start": dates[mode, first].isoformat(),
                       "peak_season": peak_season, "peak_delta_t_seasons": track.index(peak_season),
                       "peak_appearance_per_clear": peak, "last_season": track[-1],
                       "last_retention_vs_peak": scan.appear[mode, track[-1], unit] / scan.valid[mode, track[-1]] / peak,
                       "seasons_observed": len(track)}
            for level, label in ((0.5, "half"), (0.25, "quarter")):
                hit = crossings[level]
                summary[f"{label}_life_season"] = hit
                summary[f"{label}_life_seasons_from_first"] = track.index(hit) if hit else None
                summary[f"{label}_life_days_from_first"] = (dates[mode, hit] - dates[mode, first]).days if hit else None
                summary[f"{label}_life_status"] = "observed" if hit else "right_censored"
            summaries.append(summary)
    da = orders["deadly_assault"]
    room_median = {room: float(np.median(np.frombuffer(values, dtype="d"))) for room, values in scan.room.items()}
    generation = defaultdict(list)
    minimum = scan.spec["stable_descriptive_min_per_arm"]
    for unit in scan.tracked:
        observed = [s for s in da if any((unit, s, slot) in scan.track_scores for slot in STAGES.values())]
        if not observed:
            continue
        first = observed[0]
        for season in da[da.index(first):]:
            for slot in STAGES.values():
                values = scan.track_scores.get((unit, season, slot))
                if not values:
                    continue
                boss = bosses[season, slot]
                p25, p50, p75 = quartiles(values)
                base = room_median[season, slot]
                counter = boss["unit_is_counter"](unit)
                row = {"scope": "da_score_track", "unit_id": unit, "unit_zh": units_zh[unit], "mode": "deadly_assault",
                       "combat_season": season, "season_start": dates["deadly_assault", season].isoformat(),
                       "delta_t_seasons": da.index(season) - da.index(first),
                       "days_since_first": (dates["deadly_assault", season] - dates["deadly_assault", first]).days,
                       "stage_slot": slot, "boss_zh": boss["boss_zh"], "mech_generation": boss["mech_regime"][:2],
                       "mech_regime": boss["mech_regime"], "empirical_mech_regime": boss["empirical_mech_regime"],
                       "favored_group": boss["favored_group"], "unit_is_prespecified_counter": int(counter),
                       "investment_label": "0+1", "single_core": 1, "n": len(values),
                       "score_p25": p25, "score_p50": p50, "score_p75": p75, "room_median_all_valid": base,
                       "relative_p25": p25 / base, "relative_p50": p50 / base, "relative_p75": p75 / base,
                       "stable_descriptive_cell": int(len(values) >= minimum)}
                rows.append(row)
                if row["stable_descriptive_cell"]:
                    generation[unit, row["mech_generation"], counter].append(row)
    for (unit, gen, counter), cells in sorted(generation.items()):
        rel = [c["relative_p50"] for c in cells]
        summaries.append({"scope": "da_generation_summary", "unit_id": unit, "unit_zh": units_zh[unit],
                          "mode": "deadly_assault", "mech_generation": gen, "unit_is_prespecified_counter": int(counter),
                          "n_rooms": len(cells), "n": sum(c["n"] for c in cells),
                          "relative_p50_median_over_rooms": float(np.median(rel)),
                          "relative_p25_median_over_rooms": float(np.median([c["relative_p25"] for c in cells])),
                          "relative_p50_min": min(rel), "relative_p50_max": max(rel)})
    return summaries + rows, room_median


def event_rows(scan, dates, orders, units_zh, room_median):
    window = scan.spec["window"]
    minimum = scan.spec["stable_descriptive_min_per_arm"]
    rows, strata = [], []
    for event in scan.events:
        event_id, main, component = event["event_id"], event["main"], event["component"]
        main_debut = scan.catalog[main]["release_date"]
        event_type = "post_launch_support" if scan.release[event_id] > main_debut else "co_launch"
        phases = {}
        for mode, seasons in orders.items():
            hit = next((s for s in seasons if scan.appear[mode, s, component]), None)
            if hit is None:
                continue
            index = seasons.index(hit)
            pre = seasons[max(0, index - window["pre"]):index]
            post = seasons[index:index + window["post"]]
            phases[mode] = {"pre": pre, "post": post}
            share = {s: scan.appear[mode, s, main] / scan.valid[mode, s] for s in pre + post}
            pre_share = [share[s] for s in pre if scan.appear[mode, s, main]]
            post_share = [share[s] for s in post]
            pre_mean = sum(pre_share) / len(pre_share) if pre_share else None
            post_mean = sum(post_share) / len(post_share)
            attach = sum(scan.attach[mode, s, event_id] for s in post)
            main_post = sum(scan.appear[mode, s, main] for s in post)
            rows.append({"scope": "appearance_window", "event_id": event_id, "group": event["group"],
                         "event_type": event_type, "main_unit": main, "main_zh": units_zh[main],
                         "component": component, "component_zh": units_zh[component], "mode": mode,
                         "event_season": hit, "event_season_start": dates[mode, hit].isoformat(),
                         "pre_seasons": json_text(pre), "post_seasons": json_text(post),
                         "main_share_pre_mean": pre_mean, "main_share_post_mean": post_mean,
                         "main_share_change_pp": (post_mean - pre_mean) * 100 if pre_mean is not None else None,
                         "main_share_ratio_post_pre": post_mean / pre_mean if pre_mean else None,
                         "n_valid_3man_pre": sum(scan.valid[mode, s] for s in pre),
                         "n_valid_3man_post": sum(scan.valid[mode, s] for s in post),
                         "component_attach_share_post": attach / main_post if main_post else None,
                         "denominator": "valid_full_three_agent_records"})
        if "deadly_assault" not in phases:
            continue
        for design in ("main_build_matched", "fixed_third_matched"):
            for phase, seasons in phases["deadly_assault"].items():
                arms = {0: [], 1: []}
                ratios, n_strata, n_stable = [], 0, 0
                keys = sorted({key[:-1] for key in scan.event_scores
                               if key[0] == event_id and key[1] == design and key[2] in seasons})
                for key in keys:
                    _, _, season, slot, fixed, specialty = key
                    base = room_median[season, slot]
                    with_, legacy = scan.event_scores.get((*key, 1)), scan.event_scores.get((*key, 0))
                    for arm, values in ((1, with_), (0, legacy)):
                        if values:
                            arms[arm].extend(v / base for v in values)
                    if not with_ or not legacy:
                        continue
                    n_strata += 1
                    qw, ql = quartiles(with_), quartiles(legacy)
                    stable = len(with_) >= minimum and len(legacy) >= minimum
                    n_stable += stable
                    ratio = qw[1] / ql[1]
                    weight = 2 * len(with_) * len(legacy) / (len(with_) + len(legacy))
                    if stable:
                        ratios.append((ratio, weight))
                    strata.append({"scope": "score_stratum", "event_id": event_id, "event_type": event_type,
                                   "main_unit": main, "main_zh": units_zh[main], "component": component,
                                   "component_zh": units_zh[component], "comparison_design": design, "phase": phase,
                                   "combat_season": season, "stage_slot": slot, "fixed_third_unit": fixed,
                                   "fixed_third_zh": units_zh.get(fixed, ""), "replacement_specialty": specialty,
                                   "investment_label": "0+1", "n_with_support": len(with_), "n_legacy_support": len(legacy),
                                   "with_p25": qw[0], "with_p50": qw[1], "with_p75": qw[2],
                                   "legacy_p25": ql[0], "legacy_p50": ql[1], "legacy_p75": ql[2],
                                   "median_score_ratio": ratio, "overlap_weight": weight,
                                   "stable_descriptive_cell": int(stable)})
                qw, ql = quartiles(arms[1]), quartiles(arms[0])
                rows.append({"scope": "score_window_summary", "event_id": event_id, "group": event["group"],
                             "event_type": event_type, "main_unit": main, "main_zh": units_zh[main],
                             "component": component, "component_zh": units_zh[component], "mode": "deadly_assault",
                             "comparison_design": design, "phase": phase, "window_seasons": json_text(seasons),
                             "investment_label": "0+1", "n_with_support": len(arms[1]), "n_legacy_support": len(arms[0]),
                             "with_rel_p25": qw[0], "with_rel_p50": qw[1], "with_rel_p75": qw[2],
                             "legacy_rel_p25": ql[0], "legacy_rel_p50": ql[1], "legacy_rel_p75": ql[2],
                             "n_matched_strata": n_strata, "n_stable_strata": n_stable,
                             "matched_median_ratio_geomean": weighted_geomean(ratios),
                             "matched_ratio_min": min((r for r, _ in ratios), default=None),
                             "matched_ratio_max": max((r for r, _ in ratios), default=None),
                             "relative_definition": "score / median score of all valid boss-resolved records in the same season and room",
                             "causal_effect_identified": False})
    return rows + strata


def boss_lookup(out):
    from empirical_lock import LockAnalysis
    dims = read_csv(out / "dim_deadly_assault_bosses.csv")
    lock = LockAnalysis(out, dims)
    lookup = {}
    for dim in dims:
        def counter(unit, dim=dim):
            return lock._favored(dim, {"core_units": json.dumps([unit])}, [{"unit_id": unit}])[0]
        lookup[dim["combat_season"], dim["stage_slot"]] = {**dim, "unit_is_counter": counter}
    return lookup


def build(out):
    started = time.perf_counter()
    spec = json.loads((ROOT / "tools/empirical_longevity_spec.json").read_text("utf-8"))
    dates = season_dates(ROOT / "data/raw/SRC_SHIYU_meta_d930cec50bfd")
    units_zh = {r["unit_id"]: r["name_zh"] for r in read_csv(out / "dim_observed_archetype.csv")}
    scopes = defaultdict(int)
    for row in read_csv(out / "observations/obs_coverage.csv"):
        scopes[row["source_snapshot_id"], row["combat_season"], row["mode"]] += int(row["eligible_records"])
    scan = Scan(out, spec)
    connection = sqlite3.connect((out / "observations/observations.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA cache_size=-65536")
    try:
        for (snapshot, season, mode), expected in sorted(scopes.items()):
            if not expected:
                continue
            seen = 0
            for row in connection.execute(QUERY, (snapshot, season, mode)):
                scan.consume(snapshot, season, mode, row)
                seen += 1
            if seen != expected:
                raise ValueError(f"Coverage mismatch for {snapshot}/{mode}: {seen} != {expected}")
            print(f"{snapshot.rsplit(':', 1)[-1]} {mode}: {seen:,}", flush=True)
    finally:
        connection.close()
    expected_valid = sum(int(r["eligible_records"]) for r in read_csv(out / "observed_comp_archetype.csv")
                         if r["is_valid_3man"] == "1")
    if sum(scan.valid.values()) != expected_valid:
        raise ValueError("Full-roster records do not reconcile with the classified composition table")
    orders = mode_order(dates, scan.valid, spec["min_season_valid_records"])
    longevity, room_median = longevity_rows(scan, dates, orders, units_zh, boss_lookup(out))
    events = event_rows(scan, dates, orders, units_zh, room_median)
    write_csv(out / "empirical_real_dps_longevity.csv", longevity)
    write_csv(out / "empirical_support_event_window.csv", events)
    summary = {"status": "completed", "valid_3man_records": sum(scan.valid.values()),
               "da_known_score_boss_resolved": sum(len(v) for v in scan.room.values()),
               "quality_counts": dict(scan.quality),
               "season_order": orders, "longevity_rows": len(longevity), "event_rows": len(events),
               "wall_seconds": round(time.perf_counter() - started, 2)}
    (out / "empirical_longevity_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "season_order"}, ensure_ascii=False), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / "outputs/final_release")
    build(parser.parse_args().dir.resolve())
