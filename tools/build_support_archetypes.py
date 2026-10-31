"""Support attach rates, rule-based archetype categories, the anomaly ecosystem and Armorer-lock rooms.

One read-only pass over the finalized observation database. A support's attach rate to a core is the share of
that core's valid full-three-agent records that include the support, counted only in seasons that start after
the support's release.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from build_archetype_evolution import FAMILY_GROUP
from build_empirical_longevity import STAGES, season_dates
from empirical_common import ROOT, read_csv, write_csv

QUERY = """
SELECT b.stage_id,b.source_row_locator,m1.unit_id,m2.unit_id,m3.unit_id FROM obs_battle b
LEFT JOIN obs_team_member m1 ON m1.battle_id=b.battle_id AND m1.team_slot=1
LEFT JOIN obs_team_member m2 ON m2.battle_id=b.battle_id AND m2.team_slot=2
LEFT JOIN obs_team_member m3 ON m3.battle_id=b.battle_id AND m3.team_slot=3
WHERE b.snapshot_id=? AND b.combat_season=? AND b.mode=? AND b.is_eligible=1
"""
HYBRIDS = {"Remielle": "主C兼体系辅助（耀变三异常）"}
STRONG, WEAK, MIN_RECORDS = .30, .10, 2000
ECOSYSTEM_UNITS = ("Remielle", "Velina", "Yuzuha", "Nangong Yu", "Astra Yao", "Jane", "Burnice", "Miyabi",
                   "Alice", "Aria", "Promeia", "Vivian", "Yanagi", "Claret", "Roxy")
ECOSYSTEM_FROM = "2026-03-01"


def categorize(rows):
    """单核／体系／跨体系／泛用：只看搭配率≥30%的主核及其机制流派组；没有强搭配时退回≥10%。"""
    strong = [r for r in rows if r["attach_rate"] >= STRONG]
    weak = [r for r in rows if r["attach_rate"] >= WEAK]
    groups = sorted({r["core_group"] for r in strong})
    if len(strong) == 1:
        return "单核专拐", groups
    if len(strong) >= 2 and len(groups) == 1:
        return "体系专拐", groups
    if len(strong) >= 8:
        return "泛用", groups
    if len(strong) >= 2:
        return "跨体系", groups
    if len(weak) >= 5:
        return "泛用", sorted({r["core_group"] for r in weak})
    if len(weak) == 1:
        return "单核专拐", [weak[0]["core_group"]]
    return "未定型", sorted({r["core_group"] for r in weak})


def build(out):
    meta = json.loads((ROOT / "data/raw/SRC_SHIYU_meta_d930cec50bfd/characters.json").read_text("utf-8"))
    release = {u: datetime.fromtimestamp(v["release_date"], timezone.utc).date() for u, v in meta.items()}
    dates = season_dates(ROOT / "data/raw/SRC_SHIYU_meta_d930cec50bfd")
    dim = {r["unit_id"]: r for r in read_csv(out / "dim_observed_archetype.csv")}
    comps = {tuple(sorted(json.loads(r["comp_units"]))): r for r in read_csv(out / "observed_comp_archetype.csv")
             if r["is_valid_3man"] == "1"}
    issues = {(r["snapshot_id"], r["source_row_locator"]) for r in read_csv(out / "boss_association_issues.csv")}
    bosses = {(r["combat_season"], r["stage_slot"]): r for r in read_csv(out / "dim_deadly_assault_bosses.csv")}
    flex = [u for u, r in dim.items() if r["is_archetype_core"] != "1"] + list(HYBRIDS)
    anomaly_cores = {u for u, r in dim.items() if r["is_archetype_core"] == "1" and r["specialty"] == "Anomaly"}
    core_total, pair = Counter(), Counter()
    eco_total, eco_anomaly, eco_three = Counter(), Counter(), Counter()
    eco_seen, eco_in_anomaly = defaultdict(Counter), defaultdict(Counter)
    eco_with_core = defaultdict(Counter)
    room_total, room_anomaly, room_armorer = Counter(), Counter(), Counter()
    scopes = defaultdict(int)
    for r in read_csv(out / "observations/obs_coverage.csv"):
        scopes[r["source_snapshot_id"], r["combat_season"], r["mode"]] += int(r["eligible_records"])
    con = sqlite3.connect((out / "observations/observations.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    try:
        for (snapshot, season, mode), expected in sorted(scopes.items()):
            if not expected:
                continue
            day = dates[mode, season]
            local = Counter()
            for stage, locator, *team in con.execute(QUERY, (snapshot, season, mode)):
                key = tuple(sorted(u for u in team if u and u.strip()))
                if key not in comps:
                    continue
                local[key] += 1
                if mode == "deadly_assault" and day.isoformat() >= ECOSYSTEM_FROM and (snapshot, locator) not in issues:
                    room = season, STAGES[stage]
                    cores = set(json.loads(comps[key]["core_units"]))
                    room_total[room] += 1
                    room_anomaly[room] += bool(cores & anomaly_cores)
                    room_armorer[room] += any(dim[c]["specialty"] == "Armorer" for c in cores)
            for key, n in local.items():
                comp = comps[key]
                cores = json.loads(comp["core_units"])
                for core in cores:
                    core_total[core, day] += n
                    for unit in key:
                        if unit in flex and unit != core:
                            pair[unit, core, day] += n
                if day.isoformat() >= ECOSYSTEM_FROM:
                    scope = mode, season
                    is_anomaly = bool(set(cores) & anomaly_cores)
                    eco_total[scope] += n
                    eco_anomaly[scope] += n * is_anomaly
                    eco_three[scope] += n * (comp["anomaly_member_count"] == "3")
                    for unit in ECOSYSTEM_UNITS:
                        if unit in key:
                            eco_seen[scope][unit] += n
                            eco_in_anomaly[scope][unit] += n * is_anomaly
                    for unit in HYBRIDS:
                        if unit in key:
                            eco_with_core[scope][unit] += n * bool((set(cores) - {unit}) & anomaly_cores)
            print(f"{snapshot.rsplit(':', 1)[-1]} {mode}", flush=True)
    finally:
        con.close()

    attach = []
    for unit in flex:
        for core in sorted({c for c, _ in core_total}):
            if core == unit:
                continue
            seasons = [d for c, d in core_total if c == core and d >= release[unit]]
            total = sum(core_total[core, d] for d in seasons)
            if not total:
                continue
            hit = sum(pair[unit, core, d] for d in seasons)
            attach.append({"unit": unit, "unit_zh": dim[unit]["name_zh"], "unit_release": release[unit].isoformat(),
                           "unit_kind": "hybrid" if unit in HYBRIDS else "support",
                           "core": core, "core_zh": dim[core]["name_zh"], "core_specialty": dim[core]["specialty_zh"],
                           "core_group": FAMILY_GROUP[dim[core]["mechanism_family"]],
                           "core_release": release[core].isoformat(),
                           "relation": "post_sale" if release[unit] > release[core] else
                                       ("co_launch" if release[unit] == release[core] else "base"),
                           "core_records_after_unit": total, "with_unit": hit, "attach_rate": hit / total,
                           "denominator": "valid full-three-agent records of the core in seasons starting after the unit's release"})
    categories = []
    by_unit = defaultdict(list)
    for r in attach:
        if r["core_records_after_unit"] >= MIN_RECORDS:
            by_unit[r["unit"]].append(r)
    for unit in flex:
        rows = by_unit.get(unit, [])
        category, groups = categorize(rows)
        if unit in HYBRIDS:
            category = "混合型"
        strong = sorted((r for r in rows if r["attach_rate"] >= STRONG), key=lambda r: -r["attach_rate"])
        categories.append({"unit": unit, "unit_zh": dim[unit]["name_zh"], "specialty_zh": dim[unit]["specialty_zh"],
                           "release": release[unit].isoformat(), "category": category,
                           "family_groups": json.dumps(groups, ensure_ascii=False),
                           "n_strong_cores": len(strong),
                           "n_weak_cores": sum(r["attach_rate"] >= WEAK for r in rows),
                           "max_attach_rate": max((r["attach_rate"] for r in rows), default=None),
                           "strong_cores": "、".join(f"{r['core_zh']}{r['attach_rate']:.0%}{'*' if r['relation'] == 'post_sale' else ''}"
                                                    for r in strong),
                           "post_sale_strong_cores": sum(r["relation"] == "post_sale" for r in strong),
                           "rule": f"strong ≥{STRONG:.0%}, weak ≥{WEAK:.0%}, cores with ≥{MIN_RECORDS} records; * = post-sale"})
    ecosystem = []
    for (mode, season) in sorted(eco_total, key=lambda k: (k[0], dates[k])):
        total = eco_total[mode, season]
        anomaly = eco_anomaly[mode, season]
        row = {"mode": mode, "combat_season": season, "season_start": dates[mode, season].isoformat(),
               "valid_3man": total, "anomaly_core_team_share": anomaly / total,
               "three_anomaly_share": eco_three[mode, season] / total}
        for unit in ECOSYSTEM_UNITS:
            row[f"appear_{unit}"] = eco_seen[mode, season][unit] / total
            row[f"in_anomaly_{unit}"] = eco_in_anomaly[mode, season][unit] / anomaly if anomaly else None
        for unit in HYBRIDS:
            seen = eco_seen[mode, season][unit]
            row[f"with_other_anomaly_core_{unit}"] = eco_with_core[mode, season][unit] / seen if seen else None
        ecosystem.append(row)
    rooms = []
    for room in sorted(room_total, key=lambda r: (dates["deadly_assault", r[0]], r[1])):
        boss = bosses[room]
        rooms.append({"combat_season": room[0], "stage_slot": room[1], "season_start": dates["deadly_assault", room[0]].isoformat(),
                      "boss_zh": boss["boss_zh"], "mech_tag": boss["mech_tag"], "favored_group": boss["favored_group"],
                      "armorer_lock": int("锋御" in boss["favored_group"]),
                      "anomaly_penalty": int("异常伤害-40%" in boss["mech_tag"]),
                      "valid_3man": room_total[room], "anomaly_core_team_share": room_anomaly[room] / room_total[room],
                      "armorer_core_team_share": room_armorer[room] / room_total[room]})
    write_csv(out / "empirical_support_attach.csv", attach)
    write_csv(out / "empirical_support_archetypes.csv", categories)
    write_csv(out / "empirical_anomaly_ecosystem.csv", ecosystem)
    write_csv(out / "empirical_room_archetype_share.csv", rooms)
    print(json.dumps({"attach_rows": len(attach), "units": len(categories), "ecosystem_rows": len(ecosystem),
                      "rooms": len(rooms)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / "outputs/final_release")
    build(parser.parse_args().dir.resolve())
