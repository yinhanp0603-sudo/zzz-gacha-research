"""Independently audit local benchmark CSV exports; never simulate or import analytics helpers.

Usage:
    python tools/verify_benchmark_exports.py [OUTPUT_DIR] [--out AUDIT_JSON]

The default output is OUTPUT_DIR/verification/local_export_audit.json. Only this
JSON is written. Input CSV/manifest files and run databases are never modified.
All arithmetic uses Python's standard library, independently of production code.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_VERSION = "local_export_audit_v1"
BASE_CANDIDATE = "L1_A1"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "outputs" / "benchmark_n200"
FILES = (
    "search_results.csv", "seed_robustness.csv", "rank_stability.csv",
    "parameter_sensitivity.csv", "experiment_manifest.json",
)
PAIN_PARAMETERS = {
    "gap_weight": "behavior.fatigue.gap_weight",
    "failure_weight": "behavior.fatigue.failure_weight",
    "budget_weight": "behavior.fatigue.budget_weight",
}


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def finite(value: str | int | float, label: str) -> float:
    x = float(value)
    if not math.isfinite(x):
        raise ValueError(f"Non-finite number in {label}: {value!r}")
    return x


def optional_number(value: Any) -> float | None:
    return None if value in (None, "") else finite(value, "exported statistic")


def average_ranks(values: list[float]) -> list[float]:
    """Ascending 1-based ranks, average ranks for exact ties; no scipy helper."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    lo = 0
    while lo < len(order):
        hi = lo + 1
        while hi < len(order) and values[order[hi]] == values[order[lo]]:
            hi += 1
        rank = (lo + 1 + hi) / 2
        for i in order[lo:hi]:
            ranks[i] = rank
        lo = hi
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) != len(y) or not x:
        raise ValueError("Rank comparison requires equal non-empty vectors")
    a, b = average_ranks(x), average_ranks(y)
    am, bm = sum(a) / len(a), sum(b) / len(b)
    cross = sum((u - am) * (v - bm) for u, v in zip(a, b))
    ax = sum((u - am) ** 2 for u in a)
    by = sum((v - bm) ** 2 for v in b)
    return cross / math.sqrt(ax * by) if ax > 0 and by > 0 else None


def nondominated(points: dict[str, tuple[float, float]]) -> set[str]:
    """Maximize both objectives; equal points remain in the candidate set."""
    return {
        i for i, (rev_i, ret_i) in points.items()
        if not any(
            rev_j >= rev_i and ret_j >= ret_i and (rev_j > rev_i or ret_j > ret_i)
            for j, (rev_j, ret_j) in points.items() if j != i
        )
    }


class Checks:
    def __init__(self) -> None:
        self.counts: dict[str, dict[str, int]] = defaultdict(lambda: {"checked": 0, "failed": 0})
        self.failures: list[dict[str, Any]] = []

    def equal(self, category: str, name: str, actual: Any, expected: Any, *, numeric: bool = False) -> None:
        same = actual == expected
        if numeric and actual is not None and expected is not None:
            same = math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-10)
        self.counts[category]["checked"] += 1
        if not same:
            self.counts[category]["failed"] += 1
            self.failures.append({"category": category, "name": name, "actual": actual, "expected": expected})

    def yes(self, category: str, name: str, condition: bool) -> None:
        self.equal(category, name, bool(condition), True)

    def compare_rows(self, category: str, exported: list[dict], recomputed: list[dict],
                     keys: tuple[str, ...], fields: tuple[str, ...]) -> None:
        index = {tuple(r[k] for k in keys): r for r in exported}
        expected = {tuple(r[k] for k in keys): r for r in recomputed}
        self.equal(category, "unique_export_keys", len(index), len(exported))
        self.equal(category, "key_set", sorted(index), sorted(expected))
        for key in sorted(set(index) & set(expected)):
            for field in fields:
                self.equal(category, f"{key}/{field}", optional_number(index[key].get(field)),
                           expected[key].get(field), numeric=True)


def summary(values: list[float]) -> tuple[float, float, float, float]:
    return (statistics.mean(values), statistics.stdev(values) if len(values) > 1 else 0.0,
            min(values), max(values))


def relative_change(new: float, old: float) -> float | None:
    return (new - old) / old if old != 0 else None


def audit_exports(output_dir: Path) -> dict:
    search = output_dir / "search"
    paths = {name: search / name for name in FILES}
    hashes = {}
    for name, path in paths.items():
        data = path.read_bytes()
        hashes[name] = {"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
                        "byte_size": len(data)}
    manifest = json.loads(paths["experiment_manifest.json"].read_text("utf-8"))
    raw = csv_rows(paths["search_results.csv"])
    exported_seed = csv_rows(paths["seed_robustness.csv"])
    exported_rank = csv_rows(paths["rank_stability.csv"])
    exported_pain = csv_rows(paths["parameter_sensitivity.csv"])
    checks = Checks()

    candidates = manifest["candidates"]
    perturbations = manifest["perturbations"]
    train_seeds, validation_seeds = manifest["train_seeds"], manifest["validation_seeds"]
    expected_perturbations = {"P0"} | {f"{p}_x{f}" for p in PAIN_PARAMETERS for f in (0.75, 1.25)}
    expected_candidates = {f"L{l:g}_A{a:g}" for l in (0, 0.5, 1, 1.5, 2) for a in (0.8, 1, 1.2, 1.4, 1.6)}
    checks.equal("coverage", "manifest_25_candidates", set(candidates), expected_candidates)
    checks.equal("coverage", "manifest_7_perturbations", set(perturbations), expected_perturbations)
    checks.equal("coverage", "train_seed_count", len(set(train_seeds)), 5)
    checks.equal("coverage", "validation_seed_count", len(set(validation_seeds)), 3)
    checks.yes("coverage", "independent_seed_sets", not set(train_seeds) & set(validation_seeds))

    expected_keys = {(c, "P0", "train", s) for c, s in itertools.product(candidates, train_seeds)}
    expected_keys |= {(c, p, "validation", s)
                      for c, p, s in itertools.product(candidates, perturbations, validation_seeds)}
    runs = []
    for r in raw:
        row = dict(r)
        row["master_seed"] = int(r["master_seed"])
        row["revenue"] = int(r["total_revenue_fen"])
        row["retention"] = finite(r["core_retention_end"], "core_retention_end")
        row["core_initial"] = int(r["core_initial"])
        runs.append(row)
    keys = [(r["candidate_id"], r["perturbation_id"], r["split"], r["master_seed"]) for r in runs]
    checks.equal("coverage", "expected_run_count", len(expected_keys), 25 * 5 + 25 * 7 * 3)
    checks.equal("coverage", "exported_run_count", len(runs), len(expected_keys))
    checks.equal("coverage", "unique_logical_keys", len(set(keys)), len(runs))
    checks.equal("coverage", "complete_logical_keys", sorted(set(keys)), sorted(expected_keys))
    checks.equal("identities", "unique_config_seed_pairs",
                 len({(r["config_id"], r["master_seed"]) for r in runs}), len(runs))
    checks.equal("identities", "unique_run_ids", len({r["run_id"] for r in runs}), len(runs))
    checks.equal("identities", "manifest_logical_runs", manifest["summary"]["logical_runs"], len(runs))
    checks.equal("identities", "manifest_unique_runs", manifest["summary"]["unique_runs"], len(runs))
    checks.equal("identities", "experiment_ids", {r["experiment_id"] for r in runs}, {manifest["experiment_id"]})
    config_by_candidate: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in runs:
        config_by_candidate[(r["candidate_id"], r["perturbation_id"])].add(r["config_id"])
        checks.equal("identities", f"replicate/{r['run_id']}", r["replicate_id"], f"seed:{r['master_seed']}")
        checks.yes("raw_domains", f"revenue/{r['run_id']}", r["revenue"] >= 0)
        checks.yes("raw_domains", f"retention/{r['run_id']}", 0 <= r["retention"] <= 1)
        checks.yes("raw_domains", f"core_initial/{r['run_id']}", r["core_initial"] > 0)
        n = r["retention"] * r["core_initial"]
        checks.yes("raw_domains", f"integer_core_survivors/{r['run_id']}", abs(n - round(n)) < 1e-9)
    for key, ids in config_by_candidate.items():
        checks.equal("identities", f"one_config_per_candidate_perturbation/{key}", len(ids), 1)

    event_groups: dict[tuple[str, str, int], list[dict]] = defaultdict(list)
    for r in runs:
        event_groups[(r["split"], r["perturbation_id"], r["master_seed"])].append(r)
    frontiers = []
    actual_nd: dict[tuple[str, str, str, int], bool] = {}
    paired_deltas: dict[tuple[str, str, str, int], tuple[int, float]] = {}
    for group, group_rows in sorted(event_groups.items()):
        checks.equal("coverage", f"candidate_coverage/{group}", {r["candidate_id"] for r in group_rows}, set(candidates))
        checks.equal("paired_inputs", f"same_core_initial/{group}", len({r["core_initial"] for r in group_rows}), 1)
        by_candidate = {r["candidate_id"]: r for r in group_rows}
        base = by_candidate[BASE_CANDIDATE]
        nd = nondominated({c: (r["revenue"], r["retention"]) for c, r in by_candidate.items()})
        frontiers.append({"split": group[0], "perturbation_id": group[1], "master_seed": group[2],
                          "candidate_ids": sorted(nd)})
        for r in group_rows:
            key = (r["candidate_id"], r["perturbation_id"], r["split"], r["master_seed"])
            d_rev, d_ret = r["revenue"] - base["revenue"], r["retention"] - base["retention"]
            paired_deltas[key] = (d_rev, d_ret)
            actual_nd[key] = r["candidate_id"] in nd
            checks.equal("paired_deltas", f"revenue/{key}", finite(r["delta_revenue_vs_base"], "delta"), d_rev, numeric=True)
            checks.equal("paired_deltas", f"retention/{key}", finite(r["delta_retention_vs_base"], "delta"), d_ret, numeric=True)
            checks.equal("nondominated", str(key), int(r["nondominated"]), int(actual_nd[key]))

    summary_groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for r in runs:
        summary_groups[(r["candidate_id"], r["perturbation_id"], r["split"])].append(r)
    recomputed_seed = []
    for (candidate, perturbation, split), group in sorted(summary_groups.items()):
        rev_mean, rev_sd, rev_min, rev_max = summary([r["revenue"] for r in group])
        ret_mean, ret_sd, ret_min, ret_max = summary([r["retention"] for r in group])
        group_keys = [(candidate, perturbation, split, r["master_seed"]) for r in group]
        inclusion_count = sum(actual_nd[k] for k in group_keys)
        recomputed_seed.append({
            "candidate_id": candidate, "perturbation_id": perturbation, "split": split,
            "n_seeds": len(group), "revenue_mean": rev_mean, "revenue_sd": rev_sd,
            "revenue_min": rev_min, "revenue_max": rev_max, "retention_mean": ret_mean,
            "retention_sd": ret_sd, "retention_min": ret_min, "retention_max": ret_max,
            "delta_revenue_mean": statistics.mean(paired_deltas[k][0] for k in group_keys),
            "delta_retention_mean": statistics.mean(paired_deltas[k][1] for k in group_keys),
            "inclusion_count": inclusion_count, "inclusion_frequency": inclusion_count / len(group),
        })
    summary_fields = (
        "n_seeds", "revenue_mean", "revenue_sd", "revenue_min", "revenue_max", "retention_mean",
        "retention_sd", "retention_min", "retention_max", "delta_revenue_mean", "delta_retention_mean",
        "inclusion_count", "inclusion_frequency",
    )
    checks.compare_rows("seed_robustness", exported_seed, recomputed_seed,
                        ("candidate_id", "perturbation_id", "split"), summary_fields)
    # New report-only table: independently recompute paired dispersion as well.
    paired_path = search / "paired_differences.csv"
    paired_groups = 0
    if paired_path.is_file():
        data = paired_path.read_bytes()
        hashes[paired_path.name] = {"path": str(paired_path.resolve()),
                                    "sha256": hashlib.sha256(data).hexdigest(), "byte_size": len(data)}
        expected_pairs = []
        for (candidate, perturbation, split), group in sorted(summary_groups.items()):
            row = dict(experiment_id=manifest["experiment_id"], candidate_id=candidate,
                       perturbation_id=perturbation, split=split, n_rows=len(group))
            for target_index, prefix in ((0, "delta_revenue"), (1, "delta_retention")):
                values = [paired_deltas[(candidate, perturbation, split, r["master_seed"])][target_index]
                          for r in group]
                sd = statistics.stdev(values) if len(values) > 1 else None
                row.update({prefix + "_n": len(values), prefix + "_mean": statistics.mean(values),
                            prefix + "_sd": sd, prefix + "_se": sd / math.sqrt(len(values)) if sd is not None else None,
                            prefix + "_min": min(values), prefix + "_max": max(values)})
            expected_pairs.append(row)
        pair_fields = ("n_rows",) + tuple(prefix + "_" + suffix
                       for prefix in ("delta_revenue", "delta_retention")
                       for suffix in ("n", "mean", "sd", "se", "min", "max"))
        checks.compare_rows("paired_summary", csv_rows(paired_path), expected_pairs,
                            ("experiment_id", "candidate_id", "perturbation_id", "split"), pair_fields)
        paired_groups = len(expected_pairs)
    summaries = {(r["candidate_id"], r["perturbation_id"], r["split"]): r for r in recomputed_seed}

    def vector(perturbation: str, split: str, metric: str) -> list[float]:
        return [summaries[(c, perturbation, split)][metric] for c in candidates]

    recomputed_rank = []
    for p in perturbations:
        baseline_split, baseline_perturbation = ("train", "P0") if p == "P0" else ("validation", "P0")
        comparison = "train_mean_vs_validation_mean" if p == "P0" else "validation_vs_P0_validation"
        recomputed_rank.append({
            "perturbation_id": p, "comparison": comparison, "n_candidates": len(candidates),
            "rho_rev": spearman(vector(baseline_perturbation, baseline_split, "revenue_mean"),
                                vector(p, "validation", "revenue_mean")),
            "rho_ret": spearman(vector(baseline_perturbation, baseline_split, "retention_mean"),
                                vector(p, "validation", "retention_mean")),
        })
    checks.compare_rows("rank_stability", exported_rank, recomputed_rank,
                        ("perturbation_id", "comparison"), ("n_candidates", "rho_rev", "rho_ret"))
    checks.compare_rows("manifest_rank_stability", manifest["rank_stability"], recomputed_rank,
                        ("perturbation_id", "comparison"), ("n_candidates", "rho_rev", "rho_ret"))

    recomputed_pain = []
    for p in perturbations:
        if p == "P0":
            continue
        parameter, factor = p.rsplit("_x", 1)
        for c in candidates:
            baseline, current = summaries[(c, "P0", "validation")], summaries[(c, p, "validation")]
            recomputed_pain.append({
                "perturbation_id": p, "parameter_name": PAIN_PARAMETERS[parameter],
                "factor": float(factor), "candidate_id": c,
                "revenue_mean": current["revenue_mean"], "retention_mean": current["retention_mean"],
                "rel_change_revenue_vs_P0": relative_change(current["revenue_mean"], baseline["revenue_mean"]),
                "rel_change_retention_vs_P0": relative_change(current["retention_mean"], baseline["retention_mean"]),
                "nondominated_freq": current["inclusion_frequency"],
            })
    checks.compare_rows("parameter_sensitivity", exported_pain, recomputed_pain,
                        ("perturbation_id", "parameter_name", "candidate_id"),
                        ("factor", "revenue_mean", "retention_mean", "rel_change_revenue_vs_P0",
                         "rel_change_retention_vs_P0", "nondominated_freq"))

    mean_frontiers = []
    for split, p in sorted({(r["split"], r["perturbation_id"]) for r in recomputed_seed}):
        points = {c: (summaries[(c, p, split)]["revenue_mean"], summaries[(c, p, split)]["retention_mean"])
                  for c in candidates}
        mean_frontiers.append({"split": split, "perturbation_id": p,
                               "candidate_ids": sorted(nondominated(points))})

    axes = {}
    for c in candidates:
        match = re.fullmatch(r"L([\d.]+)_A([\d.]+)", c)
        if not match:
            raise ValueError(f"Cannot decode candidate axes: {c}")
        axes[c] = (float(match[1]), float(match[2]))
    marginals = {}
    for name, position in (("lambda", 0), ("support_gain_scale_a", 1)):
        points = []
        for level in sorted({x[position] for x in axes.values()}):
            members = [c for c in candidates if axes[c][position] == level]
            rev = statistics.mean(summaries[(c, "P0", "validation")]["revenue_mean"] for c in members)
            ret = statistics.mean(summaries[(c, "P0", "validation")]["retention_mean"] for c in members)
            points.append({"level": level, "n_candidates": len(members),
                           "revenue_mean_fen": rev, "revenue_mean_cny": rev / 100,
                           "retention_mean": ret})
        first, last = points[0], points[-1]
        marginals[name] = {"points": points, "endpoint_difference": {
            "from": first["level"], "to": last["level"],
            "revenue_change_cny": last["revenue_mean_cny"] - first["revenue_mean_cny"],
            "revenue_relative_change": relative_change(last["revenue_mean_fen"], first["revenue_mean_fen"]),
            "retention_change_percentage_points": (last["retention_mean"] - first["retention_mean"]) * 100,
        }}

    counts = {k: dict(v) for k, v in sorted(checks.counts.items())}
    run_db_files = list((search / "runs").glob("*.sqlite")) if (search / "runs").exists() else []
    return {
        "audit_version": AUDIT_VERSION,
        "status": "passed" if not checks.failures else "failed",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "output_dir": str(output_dir.resolve()), "input_sha256": hashes,
        "scope": {"experiment_id": manifest["experiment_id"], "scale": manifest.get("scale"),
                  "common_overrides": manifest.get("common_overrides"), "baseline_candidate": BASE_CANDIDATE,
                  "expected_formula": "25*5 + 25*7*3 = 650", "expected_runs": len(expected_keys),
                  "actual_runs": len(runs), "summary_groups": len(recomputed_seed),
                  "seed_frontier_groups": len(frontiers), "mean_frontier_groups": len(mean_frontiers),
                  "rank_comparisons": len(recomputed_rank), "parameter_comparisons": len(recomputed_pain),
                  "paired_summary_groups": paired_groups,
                  "unique_configs": len({r["config_id"] for r in runs}),
                  "unique_config_seed_pairs": len({(r["config_id"], r["master_seed"]) for r in runs}),
                  "unique_run_ids": len({r["run_id"] for r in runs}), "available_run_db_files": len(run_db_files)},
        "check_counts": counts,
        "total_checks": sum(c["checked"] for c in counts.values()),
        "failed_checks": sum(c["failed"] for c in counts.values()),
        "failures": checks.failures,
        "numeric_comparison_tolerance": {"relative": 1e-12, "absolute": 1e-10},
        "recomputed_candidate_summaries": recomputed_seed,
        "recomputed_seed_frontiers": frontiers, "recomputed_mean_frontiers": mean_frontiers,
        "recomputed_rank_stability": recomputed_rank,
        "recomputed_parameter_sensitivity": recomputed_pain,
        "validation_P0_axis_marginals": {
            "definition": "P0 validation: equal weight to seeds and uniform weight to candidates on the other axis; descriptive grid contrasts.",
            **marginals,
        },
        "limitations": [
            "本工具只独立复核本地CSV/manifest数值与覆盖，不调用生产analytics helpers，不模拟。",
            ("run DB未转移，不能逐库FK验证；未验证逐抽、钱包、资产或保底明细。" if not run_db_files else
             "本工具未读取run DB，未执行逐库FK或逐抽/钱包/资产/保底明细验证。"),
            "config_id/run_id核对唯一性及对应关系；缺各run完整配置时不重建科学配置哈希。",
            "n=200为缩减规模的假设情景；不是实际玩家、真实流水或现实因果证据。",
            "3组复核种子的入选频率仅描述这3次重复，不是总体最优概率。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--out", type=Path, help="Destination audit JSON; defaults to OUTPUT_DIR/verification/local_export_audit.json")
    args = parser.parse_args()
    destination = args.out or args.output_dir / "verification" / "local_export_audit.json"
    result = audit_exports(args.output_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False, default=list) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "runs": result["scope"]["actual_runs"],
                      "checks": result["total_checks"], "failed_checks": result["failed_checks"],
                      "rank_stability": result["recomputed_rank_stability"],
                      "axis_marginals": result["validation_P0_axis_marginals"],
                      "audit_json": str(destination.resolve())}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
