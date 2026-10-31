"""Consumer-state integration: cash gates, retained rights, event timing and accounting."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from zzz_research import contracts as C
from zzz_research import simulation as SIM
from zzz_research.agents import spending as SP
from zzz_research.config import resolve_config
from zzz_research.economy import wallet as W
from zzz_research.storage.writer import SQLiteWriter


ENTRY = Path(__file__).resolve().parents[1] / "config" / "research_v2" / "experiment.json"
N_PLAYERS = 10


@pytest.fixture(scope="module")
def cfg():
    return resolve_config(ENTRY, overrides={"population.n_players": N_PLAYERS})


@pytest.fixture(scope="module")
def seed(cfg):
    return cfg.metadata["module_metadata"]["experiments"]["train_seeds"][0]


@pytest.fixture(scope="module")
def full_run(cfg, seed):
    writer = SQLiteWriter(":memory:")
    result = SIM.run_simulation(cfg, master_seed=seed, writer=writer)
    yield result, writer
    writer.close()


def _paid_cohort_config(cfg, *, alpha):
    tiers = C.deep_thaw(cfg.module("population"))["tiers"]
    for tier in tiers:
        tier["share"] = float(tier["id"] == "Budget_40")
    overrides = {"population.n_players": 2, "population.tiers": tiers}
    overrides.update({f"behavior.tier_policy.{t['id']}.alpha": alpha for t in tiers})
    return resolve_config(ENTRY, overrides=overrides)


def _decline_topup(params, prev, lifecycle, **kwargs):
    return SP.SpendingDecision(replace(prev, topup_allowed=False), 0.0, None,
                               False, "decline_topup")


def _pause_subscription(params, prev, lifecycle, **kwargs):
    return SP.SpendingDecision(replace(prev, subscription_active=False), 0.0, None,
                               False, "pause_renewal")


def test_cash_refusal_replans_without_disabling_free_pulls(cfg, seed, monkeypatch):
    controlled = _paid_cohort_config(cfg, alpha=20.0)
    monkeypatch.setattr(SP, "paid_decision", _decline_topup)
    with closing(SQLiteWriter(":memory:")) as writer:
        result = SIM.run_simulation(controlled, master_seed=seed, writer=writer)
        decisions = writer.query("SELECT allowed, capacity FROM fact_spending_decision "
                                 "WHERE decision_kind='topup'")
        assert decisions and all(not allowed and capacity > 0 for allowed, capacity in decisions)
        assert writer.query("SELECT COUNT(*) FROM fact_cash_event "
                            "WHERE purchase_kind='instant_topup'") == [(0,)]
        assert writer.query("SELECT COALESCE(SUM(topup_pulls_used),0) FROM agg_player_period") == [(0,)]
        assert writer.query("SELECT SUM(curr_free_used+hoarded_free_used) FROM agg_player_period")[0][0] > 0
        attempted = writer.query("SELECT remaining_paid_cap, expected_topup_pulls "
                                 "FROM fact_decision_log WHERE did_enter=1")
        assert attempted and all(cap == 0 and expected == 0 for cap, expected in attempted)
        assert result.summaries and all(s.topup_pulls == 0 for s in result.summaries)
        assert all(ps.tier_id == "Budget_40" for ps in result.players.values())


def test_pausing_renewal_preserves_previously_purchased_subscription_credit(cfg, seed, monkeypatch):
    controlled = _paid_cohort_config(cfg, alpha=-1000.0)
    runtime = SIM.Runtime(controlled, seed)
    player, _ = runtime.init_player(0)
    purchased = W.purchase_subscription(runtime.policy, player.wallet, day=0, event_id="existing_subscription")
    player.wallet = purchased.state
    player.batch_earned.update({b.batch_id: 0 for b in player.wallet.batches})
    retained = {b.batch_id: b.credit_minor for b in player.wallet.batches
                if b.source is C.CreditSource.SUBSCRIPTION}
    assert retained
    monkeypatch.setattr(SP, "subscription_decision", _pause_subscription)
    monkeypatch.setattr(SP, "paid_decision", _decline_topup)
    summary = runtime.step_period(player, runtime.world.periods[0], [])
    after = {b.batch_id: b.credit_minor for b in player.wallet.batches
             if b.source is C.CreditSource.SUBSCRIPTION}
    assert after == retained
    assert not player.spending.subscription_active
    assert player.tier_id == "Budget_40"
    assert summary.pulls == 0 and summary.cash_fen == 0


def test_spending_uses_prior_lifecycle_and_last_actual_paid_outcome(full_run, cfg):
    result, writer = full_run
    life = {(pid, t): (before, after, span, ci, paid, hoarding)
            for pid, t, before, after, span, ci, paid, hoarding in writer.query(
                "SELECT player_id,t_idx,F_before,F_after,failure_span,CI_post,paid_used,h_before "
                "FROM fact_player_lifecycle")}
    decisions = writer.query("SELECT player_id,t_idx,prior_F,prior_failure_span,prior_hoarding,"
                             "last_paid_failure FROM fact_spending_decision ORDER BY player_id,t_idx")
    assert decisions
    assert any(before != after for before, after, *_ in life.values())
    by_player = {}
    for pid, t, fatigue, failures, hoarding, last_paid_failure in decisions:
        current = life[pid, t]
        assert fatigue == current[0]
        assert hoarding == current[5]
        preceding = [(pt, row) for (p, pt), row in life.items() if p == pid and pt < t]
        if preceding:
            _, previous = max(preceding)
            assert fatigue == previous[1]
            assert failures == previous[2]
        else:
            assert failures == cfg.get("behavior.initial.failure_span")
        paid_preceding = [row for _, row in sorted(preceding) if row[4] > 0]
        expected_paid_failure = bool(paid_preceding and paid_preceding[-1][3] < 1.0)
        assert bool(last_paid_failure) == expected_paid_failure
        by_player.setdefault(pid, set()).add(t)
    assert any(len(periods) > 1 for periods in by_player.values())
    tiers = {pid: ps.tier_id for pid, ps in result.players.items()}
    assert all(tier == tiers[pid] for pid, tier in writer.query(
        "SELECT player_id,player_tier FROM fact_player_lifecycle"))


def test_cash_sources_and_wallet_balances_reconcile_under_spending_states(full_run, cfg):
    result, writer = full_run
    assert writer.query("SELECT status FROM run_manifest") == [("completed",)]
    assert writer.query("PRAGMA foreign_key_check") == []
    assert len(result.summaries) == N_PLAYERS * len(result.world.periods)
    rows = writer.query("""
        SELECT a.total_pulls,a.curr_free_used+a.hoarded_free_used+a.sub_pulls_used+a.topup_pulls_used,
               a.sub_revenue_fen,a.topup_revenue_fen,a.topup_pulls_used,
               (SELECT COUNT(*) FROM fact_pull_event p WHERE p.player_id=a.player_id AND p.t_idx=a.t_idx),
               COALESCE((SELECT SUM(amount_fen) FROM fact_cash_event c WHERE c.player_id=a.player_id
                         AND c.t_idx=a.t_idx AND c.purchase_kind='subscription'),0),
               COALESCE((SELECT SUM(amount_fen) FROM fact_cash_event c WHERE c.player_id=a.player_id
                         AND c.t_idx=a.t_idx AND c.purchase_kind='instant_topup'),0)
        FROM agg_player_period a""")
    assert rows and sum(row[2] for row in rows) > 0 and sum(row[3] for row in rows) > 0
    for total, sources, sub, topup, topup_pulls, pulls, sub_cash, topup_cash in rows:
        assert total == pulls == pytest.approx(sources)
        assert sub == sub_cash and topup == topup_cash
        assert topup == topup_pulls * cfg.get("economy.topup.price_fen_per_pull")
        assert sub % cfg.get("economy.subscription.price_fen") == 0
    balances = writer.query("""
        WITH keys AS (
          SELECT player_id,t_idx,provenance,earned_period FROM fact_wallet_snapshot
          UNION SELECT player_id,t_idx,provenance,earned_period FROM fact_resource_event
                WHERE provenance<>'instant_topup'),
        s AS (SELECT player_id,t_idx,provenance,earned_period,balance_minor b
              FROM fact_wallet_snapshot WHERE phase='start'),
        e AS (SELECT player_id,t_idx,provenance,earned_period,balance_minor b
              FROM fact_wallet_snapshot WHERE phase='end'),
        r AS (SELECT player_id,t_idx,provenance,earned_period,SUM(delta_minor) d
              FROM fact_resource_event GROUP BY 1,2,3,4)
        SELECT COALESCE(s.b,0),COALESCE(r.d,0),COALESCE(e.b,0) FROM keys
        LEFT JOIN s USING(player_id,t_idx,provenance,earned_period)
        LEFT JOIN e USING(player_id,t_idx,provenance,earned_period)
        LEFT JOIN r USING(player_id,t_idx,provenance,earned_period)""")
    assert balances and all(start + delta == end for start, delta, end in balances)
    assert writer.query("SELECT COUNT(*) FROM fact_player_lifecycle WHERE paid_used>paid_cap") == [(0,)]


@pytest.mark.parametrize("mode,reverse", [("full", True), ("summary", False)])
def test_spending_and_economic_outcomes_are_order_and_log_mode_independent(full_run, cfg, seed, mode, reverse):
    original, first = full_run
    with closing(SQLiteWriter(":memory:", log_mode=mode)) as writer:
        order = list(reversed(range(N_PLAYERS))) if reverse else None
        result = SIM.run_simulation(cfg, master_seed=seed, writer=writer, player_order=order)
        assert result.summaries == original.summaries
        assert result.players == original.players
        for table, order_by in (("fact_spending_decision", "player_id,t_idx,decision_kind,event_slot"),
                                ("agg_player_period", "player_id,t_idx"),
                                ("fact_player_lifecycle", "player_id,t_idx")):
            query = f"SELECT * FROM {table} ORDER BY {order_by}"
            assert writer.query(query) == first.query(query), table


def test_extending_observation_window_preserves_the_shared_history(full_run, cfg, seed):
    original, full_writer = full_run
    horizon = 10
    shortened = resolve_config(ENTRY, overrides={
        "population.n_players": N_PLAYERS,
        "scenarios.research_v2.observation_periods": horizon,
    })
    with closing(SQLiteWriter(":memory:")) as writer:
        short = SIM.run_simulation(shortened, master_seed=seed, writer=writer)
        assert len(short.world.periods) == horizon < len(original.world.periods)
        assert short.world.units == original.world.units
        assert short.world.catalog_id == original.world.catalog_id
        assert short.summaries == [row for row in original.summaries if row.t_idx <= horizon]
        for table, order_by in (
            ("fact_spending_decision", "player_id,t_idx,decision_kind,event_slot"),
            ("agg_player_period", "player_id,t_idx"),
            ("fact_player_lifecycle", "player_id,t_idx"),
            ("fact_decision_log", "player_id,t_idx,banner_id,decision_slot"),
            ("fact_wallet_snapshot", "player_id,t_idx,phase,provenance,earned_period"),
            ("fact_pity_snapshot", "player_id,t_idx,phase,pity_group"),
            ("fact_cash_event", "player_id,t_idx,cash_event_id"),
        ):
            query = f"SELECT * FROM {table} WHERE t_idx <= ? ORDER BY {order_by}"
            short_rows = writer.query(query, (horizon,))
            original_rows = full_writer.query(query, (horizon,))
            assert short_rows
            assert [row[1:] for row in short_rows] == [row[1:] for row in original_rows], table
