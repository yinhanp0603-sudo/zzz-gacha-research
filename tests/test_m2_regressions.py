"""M2面板身份、同条件配对和钱包来源一致性的回归。"""

from dataclasses import replace
from pathlib import Path

import pytest

from zzz_research import contracts as C
from zzz_research.combat import benchmark as BM
from zzz_research.combat import proxy as P
from zzz_research.config import resolve_config
from zzz_research.economy import wallet as W


@pytest.fixture(scope="module")
def cfg():
    return resolve_config(Path(__file__).resolve().parents[1] / "config" / "experiment.json")


def _panel(cfg, *, hp=None, **options):
    model = P.ProxyCombatModel(cfg.module("combat"))
    starter = cfg.module("scenarios")["initial_assets"]["starter_dps"]
    n1, n2 = cfg.module("scenarios")["initial_assets"]["neutral_units"]
    defaults = {
        "investments": [BM.Investment(0, False)],
        "supports": [None, sorted(model.params.support_gains)[0]],
        "kappas": [model.params.alignment_values[0]],
    }
    defaults.update(options)
    return BM.build_fixed_benchmark_panel(
        model,
        [BM.BenchmarkAnchor("starter_dps", starter["B_ref"], starter["pierce_share"])],
        [BM.BenchmarkEnvironment("same_label", model.params.hp_index if hp is None else hp, 0.3, 1.0)],
        neutral_fixed=n1, neutral_flex=n2, **defaults,
    )


@pytest.mark.parametrize("axis", ["investments", "supports", "kappas"])
def test_benchmark_identity_includes_actual_options(cfg, axis):
    choices = {
        "investments": [BM.Investment(1, False)],
        "supports": [None],
        "kappas": [cfg.get("combat.shield.alignment_values")[1]],
    }
    assert _panel(cfg)[0].benchmark_id != _panel(cfg, **{axis: choices[axis]})[0].benchmark_id


def test_benchmark_identity_ignores_option_order(cfg):
    options = {
        "investments": [BM.Investment(0, False), BM.Investment(1, True)],
        "supports": [None, *sorted(cfg.get("combat.support.gains"))],
        "kappas": list(cfg.get("combat.shield.alignment_values")),
    }
    a = _panel(cfg, **options)
    b = _panel(cfg, **{k: list(reversed(v)) for k, v in options.items()})
    assert a == b


def test_pairs_do_not_cross_benchmark_identity(cfg):
    a = _panel(cfg)
    b = _panel(cfg, hp=2 * cfg.get("combat.reference.hp_index"))
    sup = sorted(cfg.get("combat.support.gains"))[0]
    mixed = [r for r in a if r.support is None] + [r for r in b if r.support == sup]
    assert BM.paired_differences(mixed, "support", None, sup) == []


def test_pairs_reject_duplicate_rows(cfg):
    rows = _panel(cfg)
    sup = sorted(cfg.get("combat.support.gains"))[0]
    with pytest.raises(P.CombatInputError):
        BM.paired_differences([*rows, rows[0]], "support", None, sup)


def _policy(cfg):
    return W.WalletPolicy.from_config(cfg.module("economy"))


def test_conservation_rejects_wrong_source_label(cfg):
    policy = _policy(cfg)
    batch = C.CreditBatch("free:t1", C.CreditSource.CURRENT_FREE, policy.credit_per_pull, 0)
    before = C.WalletState(batches=(batch,))
    change = W.prepare_payment(policy, before, pull_id="p", day=1)
    bad = tuple(replace(d, source=C.CreditSource.SUBSCRIPTION) for d in change.resource_deltas)
    with pytest.raises(W.WalletError):
        W.check_conservation(before, change.state, bad)


def test_conservation_rejects_unregistered_free_batch(cfg):
    credit = _policy(cfg).credit_per_pull
    state = C.WalletState()
    deltas = (
        C.ResourceDelta("grant:fake", "fake", C.CreditSource.CURRENT_FREE, credit, 0),
        C.ResourceDelta("spend:fake", "fake", C.CreditSource.CURRENT_FREE, -credit, 0, "p"),
    )
    with pytest.raises(W.WalletError):
        W.check_conservation(state, state, deltas)


def test_paid_grant_matches_cash_product(cfg):
    before = C.WalletState()
    change = W.purchase_subscription(_policy(cfg), before, day=0, event_id="subscription")
    bad_cash = replace(change.cash_events[0], product=W.PRODUCT_TOPUP)
    after = replace(change.state, cash_events=(bad_cash,))
    with pytest.raises(W.WalletError):
        W.check_conservation(before, after, change.resource_deltas)


def test_roll_preserves_batch_identity_and_is_idempotent(cfg):
    credit = _policy(cfg).credit_per_pull
    batches = (
        C.CreditBatch("old", C.CreditSource.HISTORICAL_FREE, credit, 0),
        C.CreditBatch("current", C.CreditSource.CURRENT_FREE, credit, 42),
        C.CreditBatch("sub", C.CreditSource.SUBSCRIPTION, credit, 10),
    )
    before = C.WalletState(batches=batches, direct_purchase_remaining_pulls=2)
    after = W.roll_period(before)
    assert before.total_credit_minor == after.total_credit_minor
    assert W.roll_period(after) == after
    assert after.direct_purchase_remaining_pulls == before.direct_purchase_remaining_pulls
    for a, b in zip(before.batches, after.batches):
        assert (a.batch_id, a.credit_minor, a.acquired_day) == (b.batch_id, b.credit_minor, b.acquired_day)
        assert b.source is (C.CreditSource.HISTORICAL_FREE if a.source is C.CreditSource.CURRENT_FREE else a.source)
    payment = W.prepare_payment(_policy(cfg), after, pull_id="p", day=43)
    W.check_conservation(after, payment.state, payment.resource_deltas)


def test_conservation_rejects_duplicate_batch_ids(cfg):
    batch = C.CreditBatch("same", C.CreditSource.CURRENT_FREE, _policy(cfg).credit_per_pull, 0)
    state = C.WalletState(batches=(batch, batch))
    with pytest.raises(W.WalletError):
        W.check_conservation(state, state, ())
