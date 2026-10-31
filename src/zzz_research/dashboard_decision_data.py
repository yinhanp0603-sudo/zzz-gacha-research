"""策划调参决策台使用的已完成候选、复核与敏感性结果。"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from zzz_research.analytics.insights import axis_paired_effects, largest_step
from zzz_research.analytics.search import nondominated
from zzz_research.dashboard_data import budget_effect_data, perturbation_data
from zzz_research.report import fnum, read_json, search_metadata, search_records


def _coordinates(candidate_id: str) -> tuple[float, float] | None:
    match = re.fullmatch(r"L([0-9.]+)_A([0-9.]+)", candidate_id)
    return (float(match[1]), float(match[2])) if match else None


def _cliff(levels: list[dict]) -> dict | None:
    step = largest_step(levels, "mean_ret")
    if step is None:
        return None
    start, end, change, other = step
    return {"start": start, "end": end, "change": change, "other_step_mean": other,
            "relative_step_size": abs(change) / other if other else None}


def decision_data(out_dir: Path | str) -> dict:
    """只读导出表；均值非支配集合与逐种子入选频率分别保留。"""
    out = Path(out_dir)
    metadata = search_metadata(out) or {}
    robustness = [row for row in search_records(out, "seed_robustness.csv")
                  if row.get("perturbation_id") == "P0"]
    by_candidate: dict[str, dict] = {}
    for row in robustness:
        coordinates = _coordinates(row["candidate_id"])
        if coordinates is None or row["split"] not in ("train", "validation"):
            continue
        lam, gain = coordinates
        result = by_candidate.setdefault(row["candidate_id"], {
            "candidate_id": row["candidate_id"], "lambda": lam, "support_a": gain,
            "train_retention": None, "validation_retention": None,
            "train_revenue_fen": None, "validation_revenue_fen": None,
            "train_n_seeds": 0, "validation_n_seeds": 0,
            "train_nondominated": False, "validation_nondominated": False,
            "validation_inclusion_frequency": None, "validation_high_frequency": False})
        split = row["split"]
        result[split + "_retention"] = fnum(row.get("retention_mean"))
        result[split + "_revenue_fen"] = fnum(row.get("revenue_mean"))
        result[split + "_n_seeds"] = int(row["n_seeds"])
        if split == "validation":
            frequency = fnum(row.get("inclusion_frequency"))
            result["validation_inclusion_frequency"] = frequency
            result["validation_high_frequency"] = frequency is not None and frequency >= 2 / 3
    candidates = sorted(by_candidate.values(), key=lambda row: (row["lambda"], row["support_a"]))
    frontier = {}
    for split in ("train", "validation"):
        points = {row["candidate_id"]: (row[split + "_revenue_fen"], row[split + "_retention"])
                  for row in candidates
                  if row[split + "_revenue_fen"] is not None and row[split + "_retention"] is not None}
        members = nondominated(points)
        frontier[split] = sorted(members, key=lambda key: (by_candidate[key]["lambda"], by_candidate[key]["support_a"]))
        for row in candidates:
            row[split + "_nondominated"] = row["candidate_id"] in members

    rank_rows = search_records(out, "rank_stability.csv")
    rank = next((row for row in rank_rows
                 if row.get("perturbation_id") == "P0"
                 and row.get("comparison") == "train_mean_vs_validation_mean"), None)
    rho = ({"rho_rev": fnum(rank.get("rho_rev")), "rho_ret": fnum(rank.get("rho_ret")),
            "n_candidates": int(rank["n_candidates"])} if rank else {})

    search_rows = search_records(out, "search_results.csv")
    axis_levels = {split: axis_paired_effects(search_rows, axis=0, split=split)
                   for split in ("train", "validation")}
    cliffs = {split: _cliff(levels) for split, levels in axis_levels.items()}
    sensitivity = perturbation_data(out)
    grouped = defaultdict(list)
    for row in sensitivity["summary"]:
        grouped[row["perturbation_id"].split("_x")[0]].append(row)
    sensitivity_families = []
    for family in ("failure_weight", "gap_weight", "budget_weight"):
        rows = grouped.get(family)
        if not rows:
            continue
        low = [row["retention_change_min"] for row in rows if row["retention_change_min"] is not None]
        high = [row["retention_change_max"] for row in rows if row["retention_change_max"] is not None]
        means = [row["retention_change_mean"] for row in rows if row["retention_change_mean"] is not None]
        sensitivity_families.append({
            "family": family, "relative_min": min(low) if low else None,
            "relative_max": max(high) if high else None,
            "mean_min": min(means) if means else None, "mean_max": max(means) if means else None,
            "perturbations": [{"perturbation_id": row["perturbation_id"],
                               "n_candidates": row["n_candidates"],
                               "mean": row["retention_change_mean"],
                               "minimum": row["retention_change_min"],
                               "maximum": row["retention_change_max"]} for row in rows]})
    snapshot = out / "config_snapshot/modules"
    combat = read_json(snapshot / "combat.json") or {}
    shield = combat.get("shield", {})
    support = combat.get("support", {})
    knobs = {"shield_defense_bonus_base": shield.get("defense_bonus"),
             "shield_damage_reduction_base": shield.get("damage_reduction"),
             "support_base_gains": support.get("gains", {}),
             "support_formula": "1 + a × g"}
    return {"candidates": candidates,
            "lambda_levels": sorted({row["lambda"] for row in candidates}),
            "a_levels": sorted({row["support_a"] for row in candidates}),
            "frontier": frontier, "rho": rho, "axis_levels": axis_levels, "cliff": cliffs,
            "sensitivity_families": sensitivity_families, "sensitivity": sensitivity,
            "budget": budget_effect_data(out), "knobs": knobs,
            "metadata": {"experiment_id": metadata.get("experiment_id"),
                         "base_config_id": metadata.get("base_config_id"),
                         "train_seeds": metadata.get("train_seeds", []),
                         "validation_seeds": metadata.get("validation_seeds", []),
                         "scale": metadata.get("scale"),
                         "high_frequency_threshold": 2 / 3}}
