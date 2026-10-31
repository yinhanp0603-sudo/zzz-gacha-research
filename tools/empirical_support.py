"""Matched observed-build comparisons of prespecified team components."""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from empirical_common import (ROOT, Values, json_text, number, read_csv, ratio,
                              signature_state, track, weighted_geomean)


class SupportAnalyzer:
    def __init__(self, out):
        self.spec = json.loads((ROOT / "tools/empirical_support_pairs.json").read_text("utf-8"))
        self.pairs = {key: set(value) for key, value in self.spec["pairs"].items()}
        self.signatures = self.spec["signatures"]
        self.units = {row["unit_id"]: row for row in read_csv(out / "dim_observed_archetype.csv")}
        self.catalog = json.loads((ROOT / "data/raw/SRC_SHIYU_meta_d930cec50bfd/characters.json").read_text("utf-8"))
        self.cells = {}
        self.presence = defaultdict(lambda: {"with": Counter(), "without": Counter()})
        self.quality = Counter()

    def _legacy(self, unit, target):
        info = self.catalog[unit]
        before = min(self.catalog[t]["release_date"] for t in target)
        return (info["availability"] in ("A", "Standard S", "Free S")
                or (info["release_date"] < before and info["role"] in ("support", "subdps")))

    def process(self, record):
        if not record["valid_3man"]:
            return
        if record["mode"] == "deadly_assault" and not record["boss_association_valid"]:
            self.quality["unresolved_boss_record"] += 1
            return
        members = record["members"]
        names = {member["unit_id"] for member in members}
        for main in members:
            unit = main["unit_id"]
            if unit not in self.pairs:
                continue
            target = self.pairs[unit]
            has_target = bool(names & target)
            exposure = self.presence[unit, record["mode"], track(record)]
            exposure["with" if has_target else "without"][record["combat_season"]] += 1
            cinema, weapon = main["known_cinema"], main["known_weapon"]
            if cinema is None or not weapon:
                self.quality[unit + ":unknown_main_build"] += 1
                continue
            if main.get("level") != 60 or not main.get("artifacts"):
                self.quality[unit + ":main_level_or_discset_not_observed_endgame"] += 1
                continue
            if record["mode"] == "deadly_assault" or record["scoring_regime"] == "score":
                metric, value = "score", number(record["score_value"])
            else:
                metric, value = "time", number(record["clear_time_seconds"])
            if value is None or value <= 0:
                self.quality[unit + ":missing_or_nonpositive_metric"] += 1
                continue
            base = (record["snapshot_id"], record["combat_season"], record["mode"], record["stage_id"],
                    metric, unit, cinema, weapon, main["level"], main["artifacts"], main.get("potential"))
            others = [m["unit_id"] for m in members if m["unit_id"] != unit]
            legacy = [name for name in others if self._legacy(name, target)]
            if has_target or legacy:
                self._add(("main_build_matched", *base, "", ""), has_target, value, record["local_uid"])
            present = names & target
            if len(present) == 1:
                component = next(iter(present))
                fixed = next(name for name in others if name != component)
                specialty = self.catalog[component]["specialty"]
                self._add(("fixed_third_matched", *base, fixed, specialty), True, value, record["local_uid"])
            elif not present:
                allowed = {self.catalog[t]["specialty"] for t in target}
                for replacement in legacy:
                    specialty = self.catalog[replacement]["specialty"]
                    if specialty in allowed:
                        fixed = next(name for name in others if name != replacement)
                        self._add(("fixed_third_matched", *base, fixed, specialty), False, value, record["local_uid"])

    def _add(self, key, with_support, value, uid):
        arms = self.cells.setdefault(key, (Values(), Values()))
        arms[int(with_support)].add(value, uid)

    def finish(self):
        rows, summaries = [], defaultdict(list)
        minimum = self.spec["stable_descriptive_min_per_arm"]
        for key, (without, with_) in sorted(self.cells.items(), key=lambda item: str(item[0])):
            no, yes = without.row(), with_.row()
            if not no["n"] or not yes["n"]:
                continue
            design, snapshot, season, mode, stage, metric, unit, cinema, weapon, level, discs, potential, fixed, specialty = key
            sig = int(weapon == self.signatures[unit]["weapon"])
            score_ratio = ratio(yes["median"], no["median"]) if metric == "score" else None
            speed_ratio = ratio(no["median"], yes["median"]) if metric == "time" else None
            mean_ratio = ratio(yes["mean"], no["mean"]) if metric == "score" else ratio(no["mean"], yes["mean"])
            weight = 2 * no["n"] * yes["n"] / (no["n"] + yes["n"])
            stable = no["n"] >= minimum and yes["n"] >= minimum
            row = {"scope": "matched_stratum", "comparison_design": design, "main_unit": unit,
                   "main_zh": self.units[unit]["name_zh"], "target_components": json_text(sorted(self.pairs[unit])),
                   "snapshot_id": snapshot, "combat_season": season, "mode": mode, "stage_id": stage,
                   "stage_scope": "advanced" if mode == "deadly_assault" and stage == "2-1" else "normal",
                   "metric": metric, "known_cinema": cinema, "known_weapon": weapon,
                   "equipped_signature": sig, "investment_label": f"{cinema}+{sig}",
                   "main_level": level, "main_disc_sets": discs, "main_potential": potential,
                   "fixed_third_unit": fixed, "replacement_specialty": specialty,
                   "n_with_support": yes["n"], "n_without_support": no["n"],
                   "n_players_with_support": yes["n_players"], "n_players_without_support": no["n_players"],
                   "n_shared_players": len(with_.players & without.players),
                   "p25_with_support": yes["p25"], "median_with_support": yes["median"], "p75_with_support": yes["p75"],
                   "p25_without_support": no["p25"], "median_without_support": no["median"], "p75_without_support": no["p75"],
                   "mean_with_support": yes["mean"], "mean_without_support": no["mean"],
                   "median_score_ratio": score_ratio, "speed_ratio": speed_ratio,
                   "mean_lift_rate": mean_ratio - 1 if mean_ratio else None,
                   "median_score_difference": yes["median"] - no["median"] if metric == "score" else None,
                   "minimum_per_arm": minimum, "stable_descriptive_cell": int(stable),
                   "overlap_weight": weight, "whole_team_cost_controlled": False,
                   "causal_effect_identified": False, "estimated_a": None}
            rows.append(row)
            summary_key = design, unit, mode, row["stage_scope"], metric, cinema, sig
            summaries[summary_key].append(row)
        summary_rows = []
        for key, cells in sorted(summaries.items()):
            design, unit, mode, stage_scope, metric, cinema, sig = key
            selected = [row for row in cells if row["stable_descriptive_cell"]]
            ratios = [(row["median_score_ratio"] if metric == "score" else row["speed_ratio"], row["overlap_weight"]) for row in selected]
            result = weighted_geomean(ratios)
            row = {"scope": "matched_summary", "comparison_design": design,
                   "main_unit": unit, "main_zh": self.units[unit]["name_zh"],
                   "target_components": json_text(sorted(self.pairs[unit])),
                   "mode": mode, "stage_scope": stage_scope, "metric": metric,
                   "known_cinema": cinema, "equipped_signature": sig, "investment_label": f"{cinema}+{sig}",
                   "n_matched_strata": len(cells), "n_stable_strata": len(selected),
                   "n_with_support": sum(r["n_with_support"] for r in selected),
                   "n_without_support": sum(r["n_without_support"] for r in selected),
                   "matched_score_ratio_geomean": result if metric == "score" else None,
                   "matched_speed_ratio_geomean": result if metric == "time" else None,
                   "stratum_ratio_min": min((v for v, _ in ratios), default=None),
                   "stratum_ratio_max": max((v for v, _ in ratios), default=None),
                   "comparison_status": "conditional_observed_build_association" if selected else "insufficient_per_arm_overlap",
                   "summary_method": "harmonic_sample_overlap_weighted_geometric_mean_of_within_stratum_median_ratios",
                   "whole_team_cost_controlled": False, "causal_effect_identified": False, "estimated_a": None}
            summary_rows.append(row)
        presence_rows = []
        for (unit, mode, stage_scope), arms in sorted(self.presence.items()):
            yes, no = arms["with"], arms["without"]
            present_yes, present_no = sorted(yes), sorted(no)
            presence_rows.append({"scope": "observed_presence", "main_unit": unit,
                  "main_zh": self.units[unit]["name_zh"], "mode": mode, "stage_scope": stage_scope,
                  "n_with_support": sum(yes.values()), "n_without_support": sum(no.values()),
                  "observed_seasons_with_support": len(yes), "observed_seasons_without_support": len(no),
                  "observed_season_count_difference": len(yes) - len(no),
                  "season_list_with_support": json_text(present_yes), "season_list_without_support": json_text(present_no),
                  "survival_period_difference": None, "survival_identifiable": False,
                  "presence_definition": "At least one submitted eligible full-three-agent clear; absence is not failure or loss of ownership."})
        return summary_rows + rows + presence_rows, {"quality_counts": dict(self.quality),
                "strata_total": len(self.cells), "matched_strata": len(rows),
                "stable_strata": sum(r["stable_descriptive_cell"] for r in rows),
                "summary_rows": summary_rows, "presence_rows": presence_rows}
