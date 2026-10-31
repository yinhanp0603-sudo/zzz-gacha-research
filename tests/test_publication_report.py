from zzz_research.publication_report import registered_early_pairs, survival_median, survival_steps


def test_reference_km_retains_same_time_censor_in_risk_set():
    rows = [
        {"first_failure_or_censor_days": 42, "first_failure_event": True},
        {"first_failure_or_censor_days": 42, "first_failure_event": False},
        {"first_failure_or_censor_days": 84, "first_failure_event": True},
    ]
    steps = survival_steps(rows)
    assert steps == [(0, 1), (42, 1 - 1 / 3), (84, 0)]
    assert survival_median(rows) == 84


def test_reference_km_reports_unreached_median_for_censored_cohort():
    rows = [
        {"first_failure_or_censor_days": 42, "first_failure_event": False},
        {"first_failure_or_censor_days": 84, "first_failure_event": False},
    ]
    assert survival_median(rows) is None
    assert all(survival == 1 for _, survival in survival_steps(rows))


def test_registered_pairs_keep_zero_effect_and_exclude_unregistered_positive_pair():
    base = {"policy_id": "L1_A1", "combat_variant": "v2", "horizon": 20,
            "anchor_unit_id": "DPS_03", "investment_plan": "0+1",
            "gear_policy": "growth_from_debut"}
    data = {
        "combat_pairs": {"DPS_03": {"S_A": {}, "S_B": {}}},
        "tables": {"fixed": {"l2_support_pair_effects": [
            {**base, "support_policy": "join_on_debut:S_A", "incremental_full_star_periods": 2},
            {**base, "support_policy": "join_on_debut:S_B", "incremental_full_star_periods": 0},
            {**base, "support_policy": "join_on_debut:S_C", "incremental_full_star_periods": 9},
        ]}},
    }
    selected = registered_early_pairs(data)
    assert [r["support_policy"] for r in selected] == ["join_on_debut:S_A", "join_on_debut:S_B"]
    assert selected[1]["incremental_full_star_periods"] == 0
