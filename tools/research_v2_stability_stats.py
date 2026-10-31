"""共同随机数下的种子配对差、分项收入排序与有限候选非支配集合。"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from itertools import combinations
from typing import Any, Iterable, Mapping

from zzz_research.analytics.search import nondominated, spearman


METRICS = {
    "cash_yuan": ("累计现金", "元", 1.0),
    "new_main_topup_yuan": ("新主C直购", "元", 1.0),
    "support_topup_yuan": ("专拐直购", "元", 1.0),
    "subscription_yuan": ("月卡续订", "元", 1.0),
    "retention_core": ("核心层留存", "百分点", 100.0),
    "retention_all": ("全队列留存", "百分点", 100.0),
}
CASH_COMPONENTS = ("new_main_topup_yuan", "support_topup_yuan", "subscription_yuan")

# Student t 的双侧 95% 临界值（0.975 分位数），索引为自由度。
_T975 = (
    None, 12.7062047364, 4.3026527299, 3.1824463053, 2.7764451052,
    2.5705818356, 2.4469118511, 2.3646242516, 2.3060041352, 2.2621571629,
    2.2281388520, 2.2009851601, 2.1788128297, 2.1603686565, 2.1447866879,
    2.1314495456, 2.1199052992, 2.1098155778, 2.1009220402, 2.0930240544,
    2.0859634473, 2.0796138447, 2.0738730679, 2.0686576104, 2.0638985616,
    2.0595385528, 2.0555294386, 2.0518305165, 2.0484071418, 2.0452296421,
    2.0422724563,
)


def _estimate(values: Iterable[float]) -> dict[str, Any]:
    values = list(values)
    if not values:
        raise ValueError("均值估计需要至少一个独立种子")
    n = len(values)
    mean = statistics.mean(values)
    if n < 2:
        return {"n": n, "mean": mean, "sd": None, "se": None,
                "low": None, "high": None, "ci_method": "not_estimable_n_lt2"}
    sd = statistics.stdev(values)
    se = sd / math.sqrt(n)
    df = n - 1
    critical = _T975[df] if df < len(_T975) else 1.959963984540054
    method = "student_t_95" if df < len(_T975) else "normal_approx_df_gt30"
    return {"n": n, "mean": mean, "sd": sd, "se": se,
            "low": mean - critical * se, "high": mean + critical * se,
            "ci_method": method}


def _paired(values: list[float], reference: list[float], metric: str) -> dict[str, Any]:
    if len(values) != len(reference) or not values:
        raise ValueError("配对差需要相同且非空的种子集合")
    label, unit, scale = METRICS[metric]
    estimate = _estimate([(v - b) * scale for v, b in zip(values, reference)])
    if estimate["low"] is None:
        ci_status = "not_estimable"
    elif estimate["low"] > 0:
        ci_status = "positive"
    elif estimate["high"] < 0:
        ci_status = "negative"
    else:
        ci_status = "includes_zero"
    relative = (_estimate([(v / b - 1) * 100 for v, b in zip(values, reference)])
                if all(b > 0 for b in reference) else None)
    return {
        "metric": metric, "metric_label": label, "unit": unit,
        "n_pairs": estimate["n"], "mean_diff": estimate["mean"],
        "sd_diff": estimate["sd"], "se_diff": estimate["se"],
        "ci95_low": estimate["low"], "ci95_high": estimate["high"],
        "ci_status": ci_status, "ci_method": estimate["ci_method"],
        "mean_relative_pct": relative["mean"] if relative else None,
        "relative_se_pct": relative["se"] if relative else None,
        "relative_ci95_low": relative["low"] if relative else None,
        "relative_ci95_high": relative["high"] if relative else None,
        "relative_status": "ok" if relative else "nonpositive_reference",
        "inference_scope": "pointwise_95_no_multiplicity_adjustment",
    }


def _cash_decomposition(seeds: list[int], paired_metrics: Mapping[str, tuple[list[float], list[float]]],
                        common: Mapping[str, Any]) -> tuple[list[dict], list[dict]]:
    """分项共用同种子的基期总现金，区间按各自的种子配对差估计。"""
    base_totals = paired_metrics["cash_yuan"][1]
    summaries, observations = [], []
    for metric in ("cash_yuan", *CASH_COMPONENTS):
        values, references = paired_metrics[metric]
        differences = [value - base for value, base in zip(values, references)]
        contributions = [(difference / total * 100 if total > 0 else None)
                         for difference, total in zip(differences, base_totals)]
        estimate = _estimate(differences)
        relative = (_estimate(contributions) if all(v is not None for v in contributions) else None)
        summaries.append({**common, "metric": metric, "metric_label": METRICS[metric][0],
            "n_pairs": estimate["n"], "mean_diff_yuan": estimate["mean"],
            "sd_diff_yuan": estimate["sd"], "se_diff_yuan": estimate["se"],
            "ci95_low_yuan": estimate["low"], "ci95_high_yuan": estimate["high"],
            "mean_contribution_pp": relative["mean"] if relative else None,
            "sd_contribution_pp": relative["sd"] if relative else None,
            "se_contribution_pp": relative["se"] if relative else None,
            "contribution_ci95_low_pp": relative["low"] if relative else None,
            "contribution_ci95_high_pp": relative["high"] if relative else None,
            "contribution_status": "ok" if relative else "nonpositive_reference",
            "ci_method": estimate["ci_method"],
            "inference_scope": "pointwise_95_no_multiplicity_adjustment"})
        for seed, total, base, value, difference, contribution in zip(
                seeds, base_totals, references, values, differences, contributions):
            observations.append({**common, "seed": seed, "metric": metric,
                "metric_label": METRICS[metric][0], "base_total_yuan": total,
                "base_component_yuan": base, "changed_component_yuan": value,
                "delta_yuan": difference, "contribution_pp": contribution,
                "contribution_status": "ok" if total > 0 else "nonpositive_reference"})
    return summaries, observations


def _validate(rows: list[dict], baseline_id: str) -> tuple[dict, dict]:
    groups: dict[tuple, dict[str, dict]] = defaultdict(dict)
    coordinates: dict[str, tuple[float, float]] = {}
    seed_splits: dict[int, str] = {}
    for row in rows:
        key = (row["horizon"], row["split"], row["seed"])
        candidate = row["policy_id"]
        if candidate in groups[key]:
            raise ValueError(f"重复的候选—种子记录: {key}/{candidate}")
        groups[key][candidate] = row
        point = (row["mechanism_strength"], row["support_gain_scale"])
        if candidate in coordinates and coordinates[candidate] != point:
            raise ValueError(f"候选坐标不一致: {candidate}")
        coordinates[candidate] = point
        if row["seed"] in seed_splits and seed_splits[row["seed"]] != row["split"]:
            raise ValueError("同一种子不能同时属于筛选与复核样本")
        seed_splits[row["seed"]] = row["split"]
        for metric in METRICS:
            value = row[metric]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key}/{candidate}/{metric} 缺少有限数值")
            if metric.startswith("retention_") and not 0 <= value <= 1:
                raise ValueError(f"{key}/{candidate}/{metric} 不在 [0,1]")
        if not math.isclose(math.fsum(row[m] for m in CASH_COMPONENTS), row["cash_yuan"],
                            rel_tol=1e-12, abs_tol=1e-8):
            raise ValueError(f"{key}/{candidate}: 三类现金分账不守恒")
    if not groups:
        raise ValueError("种子统计需要运行结果")
    candidate_set = set(coordinates)
    if baseline_id not in candidate_set:
        raise ValueError(f"缺少配对基准 {baseline_id}")
    for key, candidates in groups.items():
        if set(candidates) != candidate_set:
            raise ValueError(f"候选与种子集合未对齐: {key}")
    points = set(coordinates.values())
    if len(points) != len(coordinates):
        raise ValueError("多个候选使用相同的机制锁与支援坐标")
    lambdas = {p[0] for p in points}
    gains = {p[1] for p in points}
    if len(points) != len(lambdas) * len(gains):
        raise ValueError("候选集合不是完整参数网格")
    by_split: dict[tuple, list[int]] = defaultdict(list)
    for horizon, split, seed in groups:
        by_split[horizon, split].append(seed)
    for seeds in by_split.values():
        seeds.sort()
    horizons = {h for h, _ in by_split}
    for split in {s for _, s in by_split}:
        expected_seeds = {seed for (h, s), seeds in by_split.items() if s == split for seed in seeds}
        if any(set(by_split.get((h, split), [])) != expected_seeds for h in horizons):
            raise ValueError("观察窗口之间的种子集合未对齐")
    return dict(groups), dict(by_split)


def compute_statistics(run_rows: Iterable[Mapping[str, Any]],
                       baseline_id: str = "L1_A1") -> dict[str, list[dict]]:
    """种子是独立重复；先在种子内作候选或旋钮配对，再估计均值与标准误。

    网格均值及其区间保留原指标单位（留存为 0–1）；配对留存差以百分点表示。
    所有配对区间是逐项 95% 区间，不对整张候选表实施多重比较错误率控制。
    """
    rows = [dict(r) for r in run_rows]
    groups, by_split = _validate(rows, baseline_id)
    candidate_ids = sorted(next(iter(groups.values())))
    first_group = next(iter(groups.values()))
    coordinates = {c: (first_group[c]["mechanism_strength"], first_group[c]["support_gain_scale"])
                   for c in candidate_ids}
    coordinate_ids = {point: c for c, point in coordinates.items()}
    tables: dict[str, list[dict]] = {name: [] for name in (
        "grid_summary", "rank_stability", "paired_baseline", "paired_all_candidates",
        "axis_effects", "inclusion_frequency", "axis_cash_decomposition", "cash_decomposition_seed")}
    mean_lookup = {}

    for (horizon, split), seeds in sorted(by_split.items()):
        mean_rows = []
        seed_frontiers = {
            seed: nondominated({c: (groups[horizon, split, seed][c]["cash_yuan"],
                                   groups[horizon, split, seed][c]["retention_core"])
                                for c in candidate_ids}) for seed in seeds}
        for candidate in candidate_ids:
            lam, gain = coordinates[candidate]
            row = {"horizon": horizon, "split": split, "policy_id": candidate,
                   "mechanism_strength": lam, "support_gain_scale": gain, "n_seeds": len(seeds)}
            for metric in METRICS:
                estimate = _estimate(groups[horizon, split, seed][candidate][metric] for seed in seeds)
                row[metric] = estimate["mean"]
                for target, source in (("sd", "sd"), ("se", "se"),
                                       ("ci95_low", "low"), ("ci95_high", "high")):
                    row[f"{metric}_{target}"] = estimate[source]
            row["ci_method"] = estimate["ci_method"]
            mean_rows.append(row)
            mean_lookup[horizon, split, candidate] = row
        mean_frontier = nondominated({r["policy_id"]: (r["cash_yuan"], r["retention_core"])
                                     for r in mean_rows})
        for row in mean_rows:
            candidate = row["policy_id"]
            included = sum(candidate in seed_frontiers[seed] for seed in seeds)
            row.update(mean_nondominated=candidate in mean_frontier,
                       inclusion_frequency=included / len(seeds))
            tables["grid_summary"].append(row)
            tables["inclusion_frequency"].append({
                key: row[key] for key in ("horizon", "split", "policy_id", "mechanism_strength",
                                           "support_gain_scale", "n_seeds", "mean_nondominated",
                                           "inclusion_frequency")} | {"included_seeds": included})

        for reference, candidate in combinations(candidate_ids, 2):
            common = {"horizon": horizon, "split": split,
                      "candidate_id": candidate, "reference_id": reference}
            for metric in METRICS:
                values = [groups[horizon, split, seed][candidate][metric] for seed in seeds]
                refs = [groups[horizon, split, seed][reference][metric] for seed in seeds]
                tables["paired_all_candidates"].append({**common, **_paired(values, refs, metric)})
        for candidate in candidate_ids:
            if candidate == baseline_id:
                continue
            common = {"horizon": horizon, "split": split,
                      "candidate_id": candidate, "reference_id": baseline_id}
            for metric in METRICS:
                values = [groups[horizon, split, seed][candidate][metric] for seed in seeds]
                refs = [groups[horizon, split, seed][baseline_id][metric] for seed in seeds]
                tables["paired_baseline"].append({**common, **_paired(values, refs, metric)})

        for axis_index, axis in enumerate(("mechanism_strength", "support_gain_scale")):
            levels = sorted({p[axis_index] for p in coordinates.values()})
            other_levels = sorted({p[1 - axis_index] for p in coordinates.values()})
            contrasts = [(low, high, "adjacent") for low, high in zip(levels, levels[1:])]
            if len(levels) > 2:
                contrasts.append((levels[0], levels[-1], "endpoint"))
            for low, high, kind in contrasts:
                common = {"horizon": horizon, "split": split, "axis": axis,
                          "low_level": low, "high_level": high, "contrast_kind": kind,
                          "other_axis_levels": len(other_levels)}
                paired_metrics = {}
                for metric in METRICS:
                    paired_values = []
                    paired_references = []
                    for seed in seeds:
                        values, references = [], []
                        for other in other_levels:
                            lo_point = (low, other) if axis_index == 0 else (other, low)
                            hi_point = (high, other) if axis_index == 0 else (other, high)
                            lo = coordinate_ids[lo_point]
                            hi = coordinate_ids[hi_point]
                            values.append(groups[horizon, split, seed][hi][metric])
                            references.append(groups[horizon, split, seed][lo][metric])
                        paired_values.append(statistics.mean(values))
                        paired_references.append(statistics.mean(references))
                    paired_metrics[metric] = (paired_values, paired_references)
                    tables["axis_effects"].append({**common, **_paired(paired_values, paired_references, metric)})
                decomposed, decomposed_seeds = _cash_decomposition(seeds, paired_metrics, common)
                tables["axis_cash_decomposition"].extend(decomposed)
                tables["cash_decomposition_seed"].extend(decomposed_seeds)

    horizons = sorted({r["horizon"] for r in rows})
    for horizon in horizons:
        train_seeds = by_split.get((horizon, "train"), [])
        validation_seeds = by_split.get((horizon, "validation"), [])
        if not train_seeds or not validation_seeds:
            continue
        for metric, (label, unit, _) in METRICS.items():
            training = [mean_lookup[horizon, "train", c][metric] for c in candidate_ids]
            validation = [mean_lookup[horizon, "validation", c][metric] for c in candidate_ids]
            constant_train = len(set(training)) < 2
            constant_validation = len(set(validation)) < 2
            status = ("constant_both" if constant_train and constant_validation else
                      "constant_train" if constant_train else
                      "constant_validation" if constant_validation else "ok")
            tables["rank_stability"].append({
                "horizon": horizon, "metric": metric, "metric_label": label, "unit": unit,
                "n_candidates": len(candidate_ids), "train_seeds": len(train_seeds),
                "validation_seeds": len(validation_seeds), "rho": spearman(training, validation),
                "rho_status": status,
            })
    return tables
