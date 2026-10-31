"""缺失依赖、首次事件保留与 CRN 配对差值的独立小样例；不运行仿真。"""

from __future__ import annotations

import math
import sqlite3
import statistics

import pytest

from zzz_research.analytics import cohorts as CO
from zzz_research.analytics import survival as SV
from zzz_research.analytics.paired import paired_summary
from zzz_research.analytics.reference import ReferenceObservation, ReferenceSubject


class QueryStore:
    def __init__(self, connection):
        self.connection = connection

    def query(self, sql, parameters=()):
        return self.connection.execute(sql, parameters).fetchall()


@pytest.fixture
def cohort_store():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE fact_unit_cohort(run_id,unit_id,acquisition_period,player_id);
        CREATE TABLE fact_player_lifecycle(run_id,player_id,t_idx,active_start);
        CREATE TABLE fact_player_roster_panel(
            run_id,player_id,t_idx,unit_id,eval_phase,is_full_star,eval_status);
    """)
    connection.executemany("INSERT INTO fact_unit_cohort VALUES ('r','U',1,?)", [(0,), (1,)])
    yield QueryStore(connection)
    connection.close()


def _life(store, player, active):
    store.connection.execute("INSERT INTO fact_player_lifecycle VALUES ('r',?,1,?)", (player, active))


def _panel(store, player, phase, star, status="ok"):
    store.connection.execute("INSERT INTO fact_player_roster_panel VALUES ('r',?,1,'U',?,?,?)",
                             (player, phase, star, status))


def test_frozen_unknown_does_not_erase_observed_active_rates(cohort_store):
    w = cohort_store
    _life(w, 0, 1)
    _life(w, 1, 0)
    _panel(w, 0, "post", 1)
    _panel(w, 1, "frozen_post", None, "unsupported")
    row, = CO.cohort_availability(w, "r")
    assert row.avail_rate_active == 1.0
    assert row.cohort_active_clear_share == 0.5
    assert row.avail_rate_counterfactual_frozen is None
    assert row.churned_holder_share == 0.5
    assert (row.unknown_active, row.unknown_counterfactual, row.unknown_lifecycle) == (0, 1, 0)
    assert CO.check_identity([row]) == []


def test_all_churned_composite_is_zero_even_if_frozen_unknown(cohort_store):
    w = cohort_store
    for p in (0, 1):
        _life(w, p, 0)
    _panel(w, 0, "frozen_post", None, "unsupported")
    _panel(w, 1, "frozen_post", 1)
    row, = CO.cohort_availability(w, "r")
    assert row.avail_rate_active is None
    assert row.cohort_active_clear_share == 0.0
    assert row.avail_rate_counterfactual_frozen is None
    assert row.churned_holder_share == 1.0


def test_active_unknown_does_not_make_lifecycle_unknown(cohort_store):
    w = cohort_store
    _life(w, 0, 1)
    _life(w, 1, 0)
    _panel(w, 0, "post", None, "error")
    _panel(w, 1, "frozen_post", 1)
    row, = CO.cohort_availability(w, "r")
    assert row.avail_rate_active is None
    assert row.cohort_active_clear_share is None
    assert row.avail_rate_counterfactual_frozen is None
    assert row.churned_holder_share == 0.5
    assert row.unknown_lifecycle == 0


def test_missing_lifecycle_makes_churn_share_unknown(cohort_store):
    w = cohort_store
    _life(w, 0, 1)
    _panel(w, 0, "post", 1)
    _panel(w, 1, "post", 1)  # 有战斗记录仍不能反推缺失的 active_start
    row, = CO.cohort_availability(w, "r")
    assert row.churned_holder_share is None
    assert row.avail_rate_active is None
    assert row.cohort_active_clear_share is None
    assert row.avail_rate_counterfactual_frozen is None
    assert row.unknown_lifecycle == 1 and row.unknown_share == 0.5


def test_all_lifecycle_missing_can_still_report_unknown(cohort_store):
    _panel(cohort_store, 0, "post", 1)
    row, = CO.cohort_availability(cohort_store, "r")
    assert row.unknown_lifecycle == 2
    assert row.churned_holder_share is None and row.unknown_share == 1.0


def _obs(sid, values):
    subject = ReferenceSubject(sid, "U", "0+1", 0, True, "fixed_G1", "none", None, 0.0, 1)
    return [ReferenceObservation(subject, i + 1, f"P{i + 1}", "S", age, None, 1.0, ci,
                                 None if ci is None else ci >= 1.0,
                                 "missing_model" if ci is None else "ok", None, None)
            for i, (age, ci) in enumerate(values)]


def test_unknown_after_first_failure_does_not_erase_event():
    observations = _obs("a", [(0, 1.1), (42, 0.8), (84, None), (126, 1.1)])
    diagnostics = []
    outcome, = SV.first_failure(observations, ("investment_plan",), diagnostics=diagnostics)
    assert outcome.event and outcome.time == 42
    assert outcome.reason == "first_observed_failure" and outcome.n_obs == 2
    assert diagnostics[0]["included"] and diagnostics[0]["n_obs_total"] == 4
    km, = SV.km_by_group(observations, ("investment_plan",))
    assert km.n_events == 1 and km.median == 42
    dynamic = SV.dynamic_availability(observations, ("investment_plan",))
    assert next(r for r in dynamic if r["age_days"] == 126)["share"] == 1.0


def test_failure_at_age_zero_survives_later_missing():
    outcome, = SV.first_failure(_obs("a", [(0, 0.8), (42, None)]), ("investment_plan",))
    assert outcome.event and outcome.time == 0


def test_unknown_before_failure_censors_at_last_valid_observation():
    diagnostics = []
    outcome, = SV.first_failure(_obs("a", [(0, 1.2), (42, 1.1), (84, None), (126, 0.8)]),
                                ("investment_plan",), diagnostics=diagnostics)
    assert not outcome.event and outcome.time == 42
    assert outcome.reason == "censored_at_last_valid_before_unknown"
    assert diagnostics[0]["reason"] == outcome.reason
    assert diagnostics[0]["n_obs_used"] == 2


def test_first_unknown_is_excluded_with_explicit_reason():
    diagnostics = []
    assert SV.first_failure(_obs("a", [(0, None), (42, 0.8)]), ("investment_plan",),
                            diagnostics=diagnostics) == []
    assert diagnostics == [{"subject_id": "a", "group": ("0+1",), "included": False,
                            "reason": "excluded_first_observation_unknown", "n_obs_total": 2,
                            "n_obs_used": 0, "time": None, "event": False}]


def _row(revenue=None, retention=None, **overrides):
    return {"experiment_id": "e", "candidate_id": "c", "perturbation_id": "P0", "split": "validation",
            "delta_revenue_vs_base": revenue, "delta_retention_vs_base": retention, **overrides}


def test_crn_pairwise_difference_can_be_constant_despite_large_marginal_sd():
    baseline = [100, 108, 92]
    candidate = [103, 111, 95]
    assert statistics.stdev(baseline) == statistics.stdev(candidate) == 8
    result, = paired_summary([_row(b - a, 0.03) for a, b in zip(baseline, candidate)])
    assert result["delta_revenue_n"] == 3
    assert result["delta_revenue_mean"] == 3.0
    assert result["delta_revenue_sd"] == result["delta_revenue_se"] == 0.0
    assert result["delta_revenue_min"] == result["delta_revenue_max"] == 3.0
    assert result["delta_retention_mean"] == pytest.approx(0.03)


def test_paired_csv_missing_targets_are_counted_independently():
    result, = paired_summary([_row("1", ""), _row("3", "0.1"), _row("", None)])
    assert result["n_rows"] == 3
    assert result["delta_revenue_n"] == 2 and result["delta_revenue_mean"] == 2.0
    assert result["delta_revenue_sd"] == pytest.approx(math.sqrt(2))
    assert result["delta_revenue_se"] == pytest.approx(1.0)
    assert result["delta_retention_n"] == 1 and result["delta_retention_mean"] == 0.1
    assert result["delta_retention_sd"] is None and result["delta_retention_se"] is None


def test_paired_all_missing_statistics_remain_null():
    result, = paired_summary([_row("NULL", "nan"), _row(None, float("nan"))])
    for target in ("delta_revenue", "delta_retention"):
        assert result[f"{target}_n"] == 0
        for statistic in ("mean", "sd", "se", "min", "max"):
            assert result[f"{target}_{statistic}"] is None


def test_paired_groups_do_not_mix_experiments_candidates_perturbations_or_splits():
    rows = [_row(1, 0), _row(2, 0, experiment_id="e2"), _row(3, 0, candidate_id="c2"),
            _row(4, 0, perturbation_id="P1"), _row(5, 0, split="train")]
    result = paired_summary(rows)
    assert len(result) == 5 and all(r["n_rows"] == 1 for r in result)
    assert paired_summary(list(reversed(rows))) == result
    assert paired_summary([]) == []


@pytest.mark.parametrize("bad", [True, "not-a-number", float("inf")])
def test_paired_rejects_invalid_numeric_values(bad):
    with pytest.raises(ValueError):
        paired_summary([_row(bad, 0)])
