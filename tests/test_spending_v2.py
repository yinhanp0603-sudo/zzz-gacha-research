"""Consumer-state decisions preserve capacity and use only pre-purchase information."""

from __future__ import annotations

from dataclasses import replace

import pytest

from zzz_research.agents.spending import (SpendingError, SpendingParams, SpendingState,
                                         paid_decision, subscription_decision)
from zzz_research.contracts import LifecycleState


@pytest.fixture
def params():
    channel = {"intercept": 1.0, "fatigue_weight": 1.2, "failure_weight": .4,
               "hoarding_weight": .2, "paid_failure_weight": .8, "persistence_bonus": .5}
    return SpendingParams.from_config({"model_version": "behavior_v2_spending", "research_v2": {"spending": {
        "subscription": channel,
        "topup": {**channel, "gain_weight": 1.0, "cost_weight": 1.0}}}})


@pytest.fixture
def lifecycle():
    return LifecycleState(True, True, hoarding=0.0, fatigue=0.0, failure_span=0.0)


def paid(params, lifecycle, *, state=None, u=.5, cap=40, gain=1.0, cost=20):
    return paid_decision(params, state or SpendingState(True, True), lifecycle,
                         paid_cap=cap, expected_gain=gain, expected_topup_pulls=cost, u=u)


def test_legacy_configuration_has_no_dynamic_spending():
    assert SpendingParams.from_config({"model_version": "behavior_v1"}) is None
    assert SpendingParams.from_config({"model_version": "behavior_v1", "research_v2": {"spending": {}}}) is None


def test_fatigue_and_repeated_failures_reduce_cash_willingness(params, lifecycle):
    reference = paid(params, lifecycle)
    fatigued = paid(params, replace(lifecycle, fatigue=3.0, failure_span=2.0))
    assert reference.allowed and not fatigued.allowed
    assert fatigued.probability < reference.probability
    renewal = subscription_decision(params, SpendingState(True, True), lifecycle,
                                    tier_subscribed=True, u=.5)
    fatigued_renewal = subscription_decision(params, SpendingState(True, True),
                replace(lifecycle, fatigue=3, failure_span=2), tier_subscribed=True, u=.5)
    assert renewal.allowed and not fatigued_renewal.allowed


def test_recovery_can_resume_both_channels(params, lifecycle):
    paused = SpendingState(False, False, last_paid_failure=True)
    topup = paid(params, lifecycle, state=paused)
    renewal = subscription_decision(params, paused, lifecycle, tier_subscribed=True, u=.5)
    assert topup.allowed and renewal.allowed


def test_subscription_and_topup_states_are_independent(params, lifecycle):
    old = SpendingState(True, False)
    renewal = subscription_decision(params, old, lifecycle, tier_subscribed=True, u=.1)
    assert renewal.state.subscription_active and not renewal.state.topup_allowed
    old = SpendingState(False, True)
    declined = paid(params, replace(lifecycle, fatigue=4), state=old)
    assert not declined.state.topup_allowed and not declined.state.subscription_active


def test_failed_paid_attempt_has_additional_resistance(params, lifecycle):
    previous_success = paid(params, lifecycle, state=SpendingState(True, True, False))
    previous_failure = paid(params, lifecycle, state=SpendingState(True, True, True))
    assert previous_failure.probability < previous_success.probability


def test_benefit_and_expected_cash_cost_have_distinct_effects(params, lifecycle):
    base = paid(params, lifecycle)
    assert paid(params, lifecycle, gain=2).probability > base.probability
    assert paid(params, lifecycle, cost=35).probability < base.probability


def test_paid_capacity_is_not_created_by_willingness(params, lifecycle):
    result = paid(params, lifecycle, u=None, cap=0)
    assert not result.allowed and result.probability == 0
    assert result.reason == "no_paid_capacity"
    result = subscription_decision(params, SpendingState(False, False), lifecycle,
                                   tier_subscribed=False, u=None)
    assert not result.allowed and result.reason == "no_subscription_capacity"


def test_churn_stops_payment_decisions(params, lifecycle):
    churned = replace(lifecycle, active_at_end=False, exit_day=42, exit_reason="churn")
    assert paid(params, churned, u=None).reason == "inactive"
    assert subscription_decision(params, SpendingState(True, True), churned,
                                  tier_subscribed=True, u=None).reason == "inactive"


def test_same_state_and_event_value_are_reproducible(params, lifecycle):
    assert paid(params, lifecycle, u=.73) == paid(params, lifecycle, u=.73)


@pytest.mark.parametrize("u", [None, -0.01, 1.0, float("nan")])
def test_stochastic_decision_requires_a_valid_event_uniform(params, lifecycle, u):
    with pytest.raises(SpendingError):
        paid(params, lifecycle, u=u)


def test_nonfinite_forecast_is_not_interpreted_as_no_demand(params, lifecycle):
    with pytest.raises(SpendingError):
        paid(params, lifecycle, gain=float("nan"))
