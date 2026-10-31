"""Boss-lock prevalence, exclusive-bundle concentration and conditional score contrasts."""

from __future__ import annotations

import json
from array import array
from collections import Counter

from build_archetype_evolution import FAMILY_GROUP, alpha
from empirical_common import (ROOT, hhi, json_text, median, ratio, read_csv,
                              signature_state, track, write_csv)

STAGES = {"1-1": "S1", "1-2": "S2", "1-3": "S3", "2-1": "ADV"}


def prepare_dimension(out):
    definitions = json.loads((ROOT / "tools/empirical_boss_regimes.json").read_text("utf-8"))["regimes"]
    definitions = {(r["combat_season"], r["stage_slot"]): r for r in definitions}
    rows = read_csv(out / "dim_deadly_assault_bosses.csv")
    advanced = {"corruptive_barrier_anomaly": ("gen4_corruptive_barrier_anomaly", "蕾米/三异常"),
                "corruptive_barrier_control_skill": ("gen2_corruptive_control_skill", "异常/南宫羽"),
                "control_skill_sharp_dmg": ("gen4_control_sharp", "锋御/克拉蕾")}
    for row in rows:
        proposed = definitions.get((row["combat_season"], row["stage_slot"]))
        if proposed:
            if proposed["boss_zh"] != row["boss_zh"]:
                raise ValueError("Supplied mechanism definition and verified boss identity disagree")
            code, description, favored = (proposed[k] for k in ("empirical_mech_regime", "mech_desc", "favored_group"))
            description = description.replace("无视减防", "").replace("降125%暴伤", "降低125个百分点暴伤加成")
        else:
            code, favored = advanced[row["mechanism_code"]]
            description = row["mech_tag"]
        row.update(empirical_mech_regime=code, mech_desc=description, favored_group=favored,
                   empirical_definition_basis="prespecified_mechanism_counter_hypothesis")
    write_csv(out / "dim_deadly_assault_bosses.csv", rows)
    return rows


class LockCounts:
    def __init__(self):
        self.n_eligible = self.n = self.favored = self.mixed = self.missing_score = 0
        self.bundles, self.signatures = Counter(), Counter()
        self.scores = {False: array("d"), True: array("d")}

    def add(self, valid, bundle, signature, favored, mixed, score):
        self.n_eligible += 1
        if not valid:
            return
        self.n += 1
        self.bundles[bundle] += 1
        self.signatures[signature] += 1
        self.favored += favored
        self.mixed += mixed
        if score is None or score <= 0:
            self.missing_score += 1
        else:
            self.scores[favored].append(score)

    def row(self):
        off, favored = median(self.scores[False]), median(self.scores[True])
        return {"n_eligible_clears": self.n_eligible, "n_clears": self.n,
                "n_favored": self.favored, "n_off_meta": self.n-self.favored,
                "favored_share": self.favored/self.n if self.n else None,
                "n_mixed_core_rosters": self.mixed,
                "HHI": hhi(self.bundles), "HHI_core_signature": hhi(self.signatures),
                "n_exclusive_mechanism_bundles": len(self.bundles),
                "n_core_signatures": len(self.signatures),
                "n_favored_known_score": len(self.scores[True]),
                "n_off_meta_known_score": len(self.scores[False]),
                "median_score_favored": favored, "median_score_off_meta": off,
                "off_meta_score_ratio": ratio(off, favored),
                "median_score_gap_off_minus_favored": off-favored if off is not None and favored is not None else None,
                "n_missing_score": self.missing_score}


class LockAnalysis:
    def __init__(self, out, dimensions):
        self.units = {r["unit_id"]: r for r in read_csv(out/"dim_observed_archetype.csv")}
        self.names = {r["name_zh"]: r["unit_id"] for r in self.units.values()}
        self.names.update({"蕾米": "Remielle", "星见雅": "Miyabi", "叶瞬光": "Ye Shunguang"})
        self.signatures = json.loads((ROOT/"tools/empirical_support_pairs.json").read_text("utf-8"))["signatures"]
        self.dimensions = {(r["combat_season"],r["stage_slot"]):r for r in dimensions}
        self.comps = {alpha(json.loads(r["comp_units"])): r for r in read_csv(out/"observed_comp_archetype.csv")}
        self.buckets, self.metadata = {}, {}
        self.quality = Counter()

    def _favored(self, dim, comp, members):
        cores = [self.units[unit] for unit in json.loads(comp["core_units"])]
        names = {m["unit_id"] for m in members}
        favored = dim["favored_group"].split("/")
        triple_anomaly = sum(self.units[m["unit_id"]]["specialty"] == "Anomaly" for m in members) == 3
        profession = {"强攻":"Attack","异常":"Anomaly","命破":"Rupture","锋御":"Armorer","击破":"Stun"}
        flags = []
        for core in cores:
            matched = any((word in profession and core["specialty"] == profession[word])
                          or (word in self.names and core["unit_id"] == self.names[word])
                          or (word == "三异常" and triple_anomaly)
                          or (word == "以太帷幕" and core["mechanism_family"] == "帷幕无视失衡")
                          or (word == "异放" and "异放" in core["mechanism_tags"]) for word in favored)
            flags.append(matched)
        named_component = any(self.names.get(word) in names for word in favored if word in self.names)
        return bool(any(flags) or named_component or ("三异常" in favored and triple_anomaly)), bool(any(flags) and not all(flags))

    def consume(self, record):
        if record["mode"] != "deadly_assault":
            return
        if not record["boss_association_valid"]:
            self.quality["unassigned_boss_records"] += 1
            return
        dim = self.dimensions[record["combat_season"], STAGES[record["stage_id"]]]
        comp = self.comps[alpha(m["unit_id"] for m in record["members"])]
        valid = record["valid_3man"]
        favored, mixed = self._favored(dim, comp, record["members"]) if valid else (False,False)
        groups = sorted({FAMILY_GROUP[self.units[u]["mechanism_family"]] for u in json.loads(comp["core_units"])}) if valid else []
        bundle = json_text(groups)
        metadata = {"mech_regime": dim["empirical_mech_regime"], "history_regime":dim["mech_regime"],
                    "boss_zh":dim["boss_zh"],"boss_en":dim["boss_en"],"mech_desc":dim["mech_desc"],
                    "favored_group":dim["favored_group"],"stage_scope":track(record)}
        keys = [("mechanism",dim["empirical_mech_regime"],track(record)),
                ("boss",dim["boss_zh"],dim["empirical_mech_regime"],track(record)),
                ("season_room",record["combat_season"],record["stage_id"])]
        for key in keys:
            row = {**metadata,"scope":key[0],"score_comparison_design":"unadjusted_successful_clear_cohorts"}
            if key[0] == "season_room":
                row.update(combat_season=record["combat_season"], stage_id=record["stage_id"])
            else:
                row.update(history_regime="跨时期汇总", combat_season="", stage_id="ALL")
            if key[0] == "mechanism":
                row.update(boss_zh="同类机制首领汇总",boss_en="",mech_desc="按预登记机制分类汇总",favored_group="逐房间预登记对策组")
            self._add(key,row,valid,bundle,comp["archetype_key"],favored,mixed,record["score_value"])
        if valid and int(comp["core_count"]) == 1:
            unit=json.loads(comp["core_units"])[0]
            main=next(m for m in record["members"] if m["unit_id"]==unit)
            sig=signature_state(main,self.signatures)
            if main["known_cinema"] is not None and sig is not None and main.get("level")==60:
                key=("matched_main_investment",record["snapshot_id"],record["combat_season"],record["stage_id"],main["known_cinema"],sig)
                row={**metadata,"scope":"matched_main_investment","snapshot_id":record["snapshot_id"],
                     "combat_season":record["combat_season"],"stage_id":record["stage_id"],
                     "known_cinema":main["known_cinema"],"equipped_signature":sig,"main_level":60,
                     "score_comparison_design":"same_room_snapshot_main_cinema_signature_tier_level_different_anchor_identities"}
                self._add(key,row,valid,bundle,comp["archetype_key"],favored,mixed,record["score_value"])
            else:
                self.quality["single_core_missing_matched_build"]+=1
        elif valid:
            self.quality["multi_or_no_core_not_assigned_single_main_investment"]+=1

    def _add(self,key,row,*values):
        self.metadata[key]=row
        self.buckets.setdefault(key,LockCounts()).add(*values)

    def finish(self):
        rows=[]
        for key in sorted(self.buckets,key=str):
            counts=self.buckets[key]
            summary=counts.row()
            if key[0]=="matched_main_investment" and not (summary["n_favored_known_score"] and summary["n_off_meta_known_score"]):
                continue
            rows.append({**self.metadata[key],**summary,
                "HHI_definition":"sum of squared shares of mutually exclusive sets of broad core-mechanism groups",
                "favored_definition":"prespecified core professions, named components, or explicit three-anomaly condition; generic stun flex alone does not define a core",
                "whole_team_cost_controlled":False,"identical_character_configuration":False,
                "lambda_identifiable":False,"estimated_lambda":None,
                "identification_limit":"Scores are nonlinear boss-specific outcomes conditional on successful submitted clears; different rosters, player skill and support investment remain uncontrolled."})
        return rows,{"quality_counts":dict(self.quality),"rows":len(rows),
                     "matched_main_investment_rows":sum(r["scope"]=="matched_main_investment" for r in rows)}


def lambda_reference(out):
    cfg=json.loads((out/"config_snapshot/modules/combat.json").read_text("utf-8"))
    k=cfg["reference"]["attacker_level_coefficient"]
    defense=cfg["reference"]["enemy_defense"]
    b=cfg["shield"]["defense_bonus"];d=cfg["shield"]["damage_reduction"]
    rows=[]
    for lam in [0,.5,1,1.5,2]:
        regular=(k+defense)/(k+defense*(1+b*lam))
        for pierce in [0,1]:
            retained=(1-d*lam)*((1-pierce)*regular+pierce)
            for theta in [.4,1.0]:
                rows.append({"lambda":lam,"pierce_share":pierce,"assumed_theta":theta,"kappa":1,
                             "shield_only_retained_output":retained,
                             "window_retained_output_vs_lambda_zero":(1-theta)+theta*retained,
                             "calculation":"closed-form reference, not an L2/L3 run or an observed damage estimate",
                             "observed_score_ratio_equivalent":False})
    return rows
