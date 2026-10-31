"""Seed-paired uncertainty, component ranks and non-dominated inclusion."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from research_v2_stability_stats import METRICS, compute_statistics


def _rows(train=(1, 2, 3, 4, 5), validation=(11, 12, 13)):
    rows = []
    for split, seeds in (("train", train), ("validation", validation)):
        for seed in seeds:
            for lam in (0, 1):
                for gain in (.8, 1):
                    cash = 1000 * seed + lam * (seed % 10) + 10 * gain
                    rows.append({
                        "horizon": 20, "seed": seed, "split": split,
                        "policy_id": f"L{lam}_A{gain:g}",
                        "mechanism_strength": lam, "support_gain_scale": gain,
                        "cash_yuan": cash, "new_main_topup_yuan": cash / 2,
                        "support_topup_yuan": 0, "subscription_yuan": cash / 2,
                        "retention_core": .8 - .01 * lam + .02 * gain,
                        "retention_all": .7 - .02 * lam + .01 * gain,
                    })
    return rows


def _find(rows, **values):
    return next(r for r in rows if all(r[k] == v for k, v in values.items()))


def test_seed_pairing_removes_shared_population_noise_and_uses_t_interval():
    tables = compute_statistics(_rows())
    pair = _find(tables["paired_baseline"], split="train", candidate_id="L0_A1", metric="cash_yuan")
    assert pair["n_pairs"] == 5
    assert pair["mean_diff"] == -3
    assert pair["sd_diff"] == pytest.approx(math.sqrt(2.5))
    assert pair["se_diff"] == pytest.approx(math.sqrt(.5))
    assert pair["ci95_low"] == pytest.approx(-3 - 2.7764451052 * math.sqrt(.5))
    assert pair["ci95_high"] == pytest.approx(-3 + 2.7764451052 * math.sqrt(.5))
    assert pair["ci_method"] == "student_t_95"
    assert pair["ci_status"] == "negative"
    assert pair["inference_scope"] == "pointwise_95_no_multiplicity_adjustment"


def test_three_seed_interval_uses_two_degrees_of_freedom():
    pair = _find(compute_statistics(_rows())["paired_baseline"],
                 split="validation", candidate_id="L0_A1", metric="cash_yuan")
    assert pair["n_pairs"] == 3
    assert pair["mean_diff"] == -2
    assert pair["se_diff"] == pytest.approx(1 / math.sqrt(3))
    assert pair["ci95_high"] == pytest.approx(-2 + 4.3026527299 / math.sqrt(3))
    assert pair["ci_status"] == "includes_zero"


def test_one_seed_has_no_estimated_standard_error_or_interval():
    pair = _find(compute_statistics(_rows(train=(1,), validation=(11,)))["paired_baseline"],
                 split="train", candidate_id="L0_A1", metric="cash_yuan")
    assert pair["n_pairs"] == 1
    assert pair["se_diff"] is None and pair["ci95_low"] is None and pair["ci95_high"] is None
    assert pair["ci_status"] == "not_estimable"


def test_retention_pair_units_are_percentage_points_but_grid_stays_fraction():
    tables = compute_statistics(_rows())
    pair = _find(tables["paired_baseline"], split="train", candidate_id="L0_A1", metric="retention_core")
    assert pair["unit"] == "百分点"
    assert pair["mean_diff"] == pytest.approx(1)
    base = _find(tables["grid_summary"], split="train", policy_id="L1_A1")
    assert base["retention_core"] == pytest.approx(.81)


def test_relative_difference_is_average_of_seed_ratios():
    rows = _rows(train=(1, 2), validation=(11, 12))
    for r in rows:
        if r["split"] == "train" and r["support_gain_scale"] == 1:
            r["cash_yuan"] = {("L1_A1", 1): 10, ("L1_A1", 2): 100,
                              ("L0_A1", 1): 20, ("L0_A1", 2): 110}[r["policy_id"], r["seed"]]
            r["new_main_topup_yuan"] = r["subscription_yuan"] = r["cash_yuan"] / 2
    pair = _find(compute_statistics(rows)["paired_baseline"],
                 split="train", candidate_id="L0_A1", metric="cash_yuan")
    assert pair["mean_relative_pct"] == pytest.approx(55)
    assert pair["mean_relative_pct"] != pytest.approx((130 / 110 - 1) * 100)


def test_zero_component_has_undefined_rank_and_relative_effect_not_zero_precision():
    tables = compute_statistics(_rows())
    rank = _find(tables["rank_stability"], metric="support_topup_yuan")
    assert rank["rho"] is None and rank["rho_status"] == "constant_both"
    pair = _find(tables["paired_baseline"], split="train", candidate_id="L0_A1", metric="support_topup_yuan")
    assert pair["mean_diff"] == 0
    assert pair["mean_relative_pct"] is None
    assert pair["relative_status"] == "nonpositive_reference"
    assert pair["ci_status"] == "includes_zero"


def test_axis_effect_averages_within_seed_before_counting_independent_repeats():
    tables = compute_statistics(_rows())
    effect = _find(tables["axis_effects"], split="train", axis="mechanism_strength", metric="cash_yuan")
    assert effect["other_axis_levels"] == 2
    assert effect["n_pairs"] == 5
    assert effect["mean_diff"] == 3
    assert effect["se_diff"] == pytest.approx(math.sqrt(.5))


def test_all_pairs_are_once_per_unordered_candidate_pair():
    tables = compute_statistics(_rows())
    assert len(tables["paired_all_candidates"]) == 2 * 6 * len(METRICS)
    pairs = {(r["candidate_id"], r["reference_id"]) for r in tables["paired_all_candidates"]}
    assert len(pairs) == 6
    assert all(candidate != reference for candidate, reference in pairs)


def test_frontier_inclusion_is_seed_frequency_with_exact_ties_preserved():
    rows = _rows()
    for row in rows:
        row["retention_core"] = 1 - row["cash_yuan"] / 100000
    tables = compute_statistics(rows)
    assert all(r["inclusion_frequency"] == 1 for r in tables["inclusion_frequency"])
    assert all(r["included_seeds"] == r["n_seeds"] for r in tables["inclusion_frequency"])


def test_missing_candidate_in_one_seed_is_rejected_instead_of_unpaired_comparison():
    rows = _rows()
    rows.pop()
    with pytest.raises(ValueError, match="未对齐"):
        compute_statistics(rows)


def test_reusing_same_seed_as_training_and_validation_is_rejected():
    with pytest.raises(ValueError, match="同一种子"):
        compute_statistics(_rows(train=(1, 2), validation=(1, 3)))


def test_observation_horizon_does_not_pool_seed_replicates():
    rows = _rows()
    rows += [{**row, "horizon": 10} for row in rows]
    tables = compute_statistics(rows)
    assert len(tables["rank_stability"]) == 2 * len(METRICS)
    assert all(row["n_pairs"] in (3, 5) for row in tables["paired_baseline"])


def test_missing_entire_seed_at_one_horizon_is_rejected():
    rows = _rows()
    rows += [{**row, "horizon": 10} for row in rows if row["seed"] != 3]
    with pytest.raises(ValueError, match="观察窗口.*未对齐"):
        compute_statistics(rows)


def _offsetting_cash_rows():
    rows = _rows()
    for row in rows:
        shifted = row["support_gain_scale"] == 1
        delta = 10 * (row["seed"] % 10) if shifted else 0
        row["new_main_topup_yuan"] = 600 + 10 * row["mechanism_strength"] - delta
        row["support_topup_yuan"] = 100 + delta + (5 if shifted else 0)
        row["subscription_yuan"] = 300
        row["cash_yuan"] = sum(row[m] for m in
                               ("new_main_topup_yuan", "support_topup_yuan", "subscription_yuan"))
    return rows


def test_cash_contributions_share_total_baseline_and_reconcile_for_every_seed():
    tables = compute_statistics(_offsetting_cash_rows())
    selected = [r for r in tables["axis_cash_decomposition"]
                if r["split"] == "train" and r["axis"] == "support_gain_scale"]
    total = _find(selected, metric="cash_yuan")
    components = [r for r in selected if r["metric"] != "cash_yuan"]
    assert total["n_pairs"] == 5 and total["other_axis_levels"] == 2
    assert total["mean_diff_yuan"] == 5
    assert sum(r["mean_diff_yuan"] for r in components) == pytest.approx(total["mean_diff_yuan"])
    assert sum(r["mean_contribution_pp"] for r in components) == pytest.approx(total["mean_contribution_pp"])
    support = _find(selected, metric="support_topup_yuan")
    assert support["mean_contribution_pp"] == pytest.approx(35 / 1005 * 100)
    assert support["mean_contribution_pp"] != pytest.approx(35 / 100 * 100)
    observations = [r for r in tables["cash_decomposition_seed"]
                    if r["split"] == "train" and r["axis"] == "support_gain_scale"]
    for seed in range(1, 6):
        paired = [r for r in observations if r["seed"] == seed]
        whole = _find(paired, metric="cash_yuan")
        parts = [r for r in paired if r["metric"] != "cash_yuan"]
        assert all(r["base_total_yuan"] == 1005 for r in paired)
        assert sum(r["delta_yuan"] for r in parts) == pytest.approx(whole["delta_yuan"])
        assert sum(r["contribution_pp"] for r in parts) == pytest.approx(whole["contribution_pp"])


def test_total_interval_keeps_covariance_instead_of_summing_component_endpoints():
    selected = [r for r in compute_statistics(_offsetting_cash_rows())["axis_cash_decomposition"]
                if r["split"] == "train" and r["axis"] == "support_gain_scale"]
    total = _find(selected, metric="cash_yuan")
    parts = [r for r in selected if r["metric"] != "cash_yuan"]
    assert total["se_diff_yuan"] == 0
    assert total["ci95_low_yuan"] == total["ci95_high_yuan"] == 5
    assert sum(r["ci95_low_yuan"] for r in parts) != pytest.approx(total["ci95_low_yuan"])
    assert sum(r["ci95_high_yuan"] for r in parts) != pytest.approx(total["ci95_high_yuan"])
    assert total["contribution_ci95_low_pp"] == pytest.approx(5 / 1005 * 100)
    assert total["contribution_ci95_high_pp"] == pytest.approx(5 / 1005 * 100)


def test_decomposed_total_matches_existing_paired_axis_estimates():
    tables = compute_statistics(_rows())
    for row in tables["axis_cash_decomposition"]:
        if row["metric"] != "cash_yuan":
            continue
        axis = _find(tables["axis_effects"], **{key: row[key] for key in
            ("horizon", "split", "axis", "low_level", "high_level", "contrast_kind", "metric")})
        assert row["n_pairs"] == axis["n_pairs"]
        assert row["mean_diff_yuan"] == pytest.approx(axis["mean_diff"])
        assert row["se_diff_yuan"] == pytest.approx(axis["se_diff"])
        assert row["ci95_low_yuan"] == pytest.approx(axis["ci95_low"])
        assert row["ci95_high_yuan"] == pytest.approx(axis["ci95_high"])
        assert row["mean_contribution_pp"] == pytest.approx(axis["mean_relative_pct"])
        assert row["contribution_ci95_low_pp"] == pytest.approx(axis["relative_ci95_low"])
        assert row["contribution_ci95_high_pp"] == pytest.approx(axis["relative_ci95_high"])


def test_inconsistent_cash_components_are_rejected_before_reporting_contributions():
    rows = _rows()
    rows[0]["subscription_yuan"] += 1
    with pytest.raises(ValueError, match="现金分账不守恒"):
        compute_statistics(rows)


def test_zero_total_baseline_preserves_yuan_difference_without_percentage():
    rows = _rows()
    for row in rows:
        row["new_main_topup_yuan"] = 15 if row["support_gain_scale"] == 1 else 0
        row["support_topup_yuan"] = row["subscription_yuan"] = 0
        row["cash_yuan"] = row["new_main_topup_yuan"]
    effect = _find(compute_statistics(rows)["axis_cash_decomposition"], split="train",
                   axis="support_gain_scale", metric="cash_yuan")
    assert effect["mean_diff_yuan"] == 15
    assert effect["mean_contribution_pp"] is None
    assert effect["contribution_ci95_low_pp"] is None
    assert effect["contribution_status"] == "nonpositive_reference"
