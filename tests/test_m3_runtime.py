"""M3：DP 规划、行为决策、生命周期、六步编排、SQLite 落库与小人群端到端对账。

数值全部经 resolve_config 读取；小人群通过显式 overrides 改变 n_players（因此 config_id 随之变化）。
字面量仅有 gacha.md 冻结的 DP 校验参考 93.445998 / 155.812230。
"""

from __future__ import annotations

import inspect
import math
from fractions import Fraction
from pathlib import Path

import pytest

from zzz_research import contracts as C
from zzz_research.agents import decision as DEC
from zzz_research.agents import lifecycle as LC
from zzz_research.agents import roster as R
from zzz_research.config import resolve_config
from zzz_research.economy import gacha as G
from zzz_research.economy import planner as PL
from zzz_research.economy import wallet as W
from zzz_research import simulation as SIM
from zzz_research.storage.writer import SQLiteWriter

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "config" / "experiment.json"
N_SMALL = 12  # 小人群规模（测试负载，不是模型参数）

FACT_TABLES = (
    "fact_pull_event", "fact_asset_event", "fact_resource_event", "fact_cash_event", "fact_ownership",
    "fact_player_roster_panel", "fact_decision_log", "fact_wallet_snapshot", "fact_pity_snapshot",
    "fact_player_lifecycle", "fact_frozen_state", "fact_unit_cohort", "agg_player_period",
)


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def cfg():
    return resolve_config(ENTRY)


@pytest.fixture(scope="module")
def small_cfg():
    return resolve_config(ENTRY, overrides={"population.n_players": N_SMALL})


@pytest.fixture(scope="module")
def seed(cfg):
    return cfg.metadata["module_metadata"]["experiments"]["train_seeds"][0]


@pytest.fixture(scope="module")
def run_a(small_cfg, seed):
    w = SQLiteWriter(":memory:")
    res = SIM.run_simulation(small_cfg, master_seed=seed, writer=w)
    return res, w


@pytest.fixture(scope="module")
def stress(cfg, seed):
    """高进入意愿 + 低免费供给：必然出现预算截断后的部分获取。"""
    tiers = sorted(cfg.module("behavior")["tier_policy"])
    ov = {f"behavior.tier_policy.{t}.alpha": 8.0 for t in tiers}
    ov["economy.free_pulls_per_standard_span"] = max(1, cfg.get("economy.free_pulls_per_standard_span") // 4)
    ov["population.n_players"] = 8
    c = resolve_config(ENTRY, overrides=ov)
    w = SQLiteWriter(":memory:")
    return SIM.run_simulation(c, master_seed=seed, writer=w), w, c


@pytest.fixture(scope="module")
def gm(cfg):
    return G.GachaModel.from_config(cfg.module("gacha"))


# =========================================================================== planner


def _plan(gm, path):
    return G.build_target_plan(gm, path, "U", {"character": "BC", "weapon": "BW"})


def test_planner_dp_reference_means(cfg, gm):
    pl = PL.DPPlanner(gm)
    r = C.RosterState()
    pity = {p.pity_group: C.PityState(p.pity_group) for p in gm.pools.values()}
    body = cfg.module("behavior")["support_target"]
    assert list(cfg.module("gacha")["target_paths"][body]) == ["body"]
    for path, ref in ((body, 93.445998), ("0+1", 155.812230)):
        plan = _plan(gm, path)
        H = pl.worst_case(plan.stages, pity)
        fc = pl.forecast_target(plan, r, C.BudgetView(H, 0), pity)
        assert fc.budget_pulls == H
        assert fc.expected_spent == pytest.approx(ref, abs=5e-6)
        assert fc.expected_topup == 0.0
        assert fc.k_probability[-1] == pytest.approx(1.0)


def test_planner_truncation_matches_brute_force(gm):
    pl = PL.DPPlanner(gm)
    plan = _plan(gm, "0+1")
    ch, wp = gm.pool("character"), gm.pool("weapon")
    pity = {ch.pity_group: C.PityState(ch.pity_group, 40, False), wp.pity_group: C.PityState(wp.pity_group, 10, True)}
    # 独立全长卷积
    a = G.up_cost_distribution(ch, pity[ch.pity_group])
    b = G.up_cost_distribution(wp, pity[wp.pity_group])
    full = {}
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            full[i + j + 2] = full.get(i + j + 2, 0.0) + x * y
    for F, P in ((30, 20), (100, 0), (60, 80)):
        fc = pl.forecast_target(plan, C.RosterState(), C.BudgetView(F, P), pity)
        B = fc.budget_pulls
        assert B == min(F + P, fc.worst_case_pulls)
        assert fc.expected_spent == pytest.approx(sum(min(c, B) * p for c, p in full.items()), rel=1e-12)
        e_top = sum(max(0, min(c, B) - F) * p for c, p in full.items()) if P > 0 else 0.0
        assert fc.expected_topup == pytest.approx(e_top, rel=1e-12, abs=1e-12)
        assert sum(fc.k_probability) == pytest.approx(1.0)
        p_body = sum(p for i, p in enumerate(a) if i + 1 <= B)
        assert fc.reach_probability[0] == pytest.approx(p_body)
        assert fc.reach_probability[0] >= fc.reach_probability[1]


def test_planner_uses_current_pity_only_for_first_stage(gm):
    pl = PL.DPPlanner(gm)
    ch = gm.pool("character")
    plan = _plan(gm, "2+1")
    pity = {ch.pity_group: C.PityState(ch.pity_group, ch.hard_pity - 1, True)}
    H = pl.worst_case(plan.stages, pity)
    fc = pl.forecast_target(plan, C.RosterState(), C.BudgetView(H, 0), pity)
    fresh = G.expected_up_pulls(ch)
    expected = 1 + G.expected_up_pulls(gm.pool("weapon")) + 2 * fresh
    assert fc.expected_spent == pytest.approx(expected, rel=1e-9)


def test_planner_skips_owned_and_zero_budget(gm):
    pl = PL.DPPlanner(gm)
    plan = _plan(gm, "0+1")
    r = R.apply_asset_deltas(C.RosterState(), [
        C.AssetDelta("a", C.AssetKind.CHARACTER, "U", 1, 0),
        C.AssetDelta("b", C.AssetKind.WEAPON, C.signature_weapon_id("U"), 1, 0)])
    fc = pl.forecast_target(plan, r, C.BudgetView(500, 0), {})
    assert fc.target_owned and fc.budget_pulls == 0
    fc = pl.forecast_target(plan, C.RosterState(), C.BudgetView(0, 0), {})
    assert fc.budget_pulls == 0 and fc.k_probability[0] == 1.0


def test_planner_ci_k_uses_potential_roster_and_returns_forecast(gm):
    pl = PL.DPPlanner(gm)
    plan = _plan(gm, "0+1")
    seen = []

    def ci_eval(r):
        seen.append((r.character("U"), r.weapon_count(C.signature_weapon_id("U"))))
        return C.EvalStatus.OK, float(len(seen))

    base = C.RosterState()
    fc = pl.forecast_target(plan, base, C.BudgetView(50, 50), {}, ci_evaluator=ci_eval, day=84)
    assert fc.ci_k == (1.0, 2.0, 3.0)
    assert seen[0] == (None, 0) and seen[1][0].first_acquired_day == 84 and seen[2][1] == 1
    assert base.character("U") is None  # 潜在库存不写回实际状态
    f = fc.to_forecast()
    assert isinstance(f, C.Forecast) and len(f.stages) == 2

    def bad(r):
        return C.EvalStatus.UNSUPPORTED, None

    fc = pl.forecast_target(plan, base, C.BudgetView(50, 0), {}, ci_evaluator=bad)
    assert fc.eval_status is C.EvalStatus.UNSUPPORTED and all(c is None for c in fc.ci_k)


def test_planner_does_not_touch_rng_or_wallet():
    src = inspect.getsource(PL)
    for forbidden in ("rng", "HashCounterRng", "draw_with_rng", "prepare_payment", "wallet"):
        assert f"import {forbidden}" not in src and f"from ..{forbidden}" not in src
    assert "draw_u" not in src and "prepare_payment" not in src


# =========================================================================== decision


def _fc(gm, *, k_prob, ci_k, F=10, P=5, spent=6.0, topup=1.0):
    plan = _plan(gm, "0+1")
    return PL.PlanForecast(plan, plan.stages, tuple(1 - sum(k_prob[: j + 1]) for j in range(len(plan.stages))),
                           tuple(k_prob), tuple(ci_k), C.EvalStatus.OK, F, P, 99, F + P, spent, topup)


def test_decision_utility_formula(cfg, gm):
    dp = DEC.DecisionParams.from_config(cfg.module("behavior"))
    tier = sorted(dp.tiers)[1]
    fc = _fc(gm, k_prob=[0.2, 0.3, 0.5], ci_k=[0.9, 1.0, 1.1])
    h = 0.3
    Gf = lambda c: dp.beta_gain * c + dp.beta_comfort / (1 + math.exp(-(c - dp.comfort_threshold) / dp.temperature))  # noqa: E731
    gain = 0.2 * Gf(0.9) + 0.3 * Gf(1.0) + 0.5 * Gf(1.1) - Gf(0.9)
    u_exp = (dp.tiers[tier].alpha - dp.beta_hoarding * h + gain
             - dp.beta_resource_cost * 6.0 / 15 - dp.beta_paid_cost * 1.0 / 5)
    u, g = DEC.utility(dp, tier, h, fc)
    assert u == pytest.approx(u_exp) and g == pytest.approx(gain)
    p = 1 / (1 + math.exp(-u_exp))
    intent, rec = DEC.decide(dp, tier_id=tier, hoarding=h, path_id="0+1", forecast=fc, u_decision=p * 0.999)
    assert intent.attempt and rec.did_enter and rec.p_enter == pytest.approx(p)
    assert intent.max_spend_pulls == fc.budget_pulls
    intent, rec = DEC.decide(dp, tier_id=tier, hoarding=h, path_id="0+1", forecast=fc, u_decision=min(0.999999, p + 1e-6))
    assert not intent.attempt and rec.skip_reason == DEC.SKIP_DECLINED


def test_decision_skips(cfg, gm):
    dp = DEC.DecisionParams.from_config(cfg.module("behavior"))
    tier = sorted(dp.tiers)[0]
    fc = _fc(gm, k_prob=[1, 0, 0], ci_k=[0.9, 1.0, 1.1], F=0, P=0)
    fc = PL.PlanForecast(**{**fc.__dict__, "budget_pulls": 0})
    _, rec = DEC.decide(dp, tier_id=tier, hoarding=0, path_id="0+1", forecast=fc, u_decision=None)
    assert rec.skip_reason == DEC.SKIP_NO_BUDGET and rec.p_enter is None
    fc2 = PL.PlanForecast(**{**_fc(gm, k_prob=[0.2, 0.3, 0.5], ci_k=[0.9, None, 1.1]).__dict__,
                             "eval_status": C.EvalStatus.UNSUPPORTED})
    _, rec = DEC.decide(dp, tier_id=tier, hoarding=0, path_id="0+1", forecast=fc2, u_decision=0.5)
    assert rec.skip_reason == DEC.SKIP_CI_UNAVAILABLE  # NULL 不当 0


# =========================================================================== lifecycle


def test_lifecycle_formulas(cfg):
    lp = LC.LifecycleParams.from_config(cfg.module("behavior"))
    std = C.contract_value("economy.standard_span_days")
    span = cfg.get("scenarios.timeline.days_per_period")
    prev = C.LifecycleState(True, True, 0.4, 1.0, 0.5)
    ci, top, cap = 0.8, 3, 10
    st = LC.step(lp, prev, ci_post=ci, elapsed_days=span, topup_pulls_used=top, paid_cap=cap,
                 u_churn=0.999999, exit_day=span)
    d = span / std
    g = lp.full_star_threshold - ci
    s_new = prev.failure_span + d
    h = lp.h_decay_base ** d * 0.4 + lp.h_gap_weight * d * g
    F = math.exp(-lp.f_decay_rate * d) * 1.0 + d * (lp.f_gap_weight * g + lp.f_failure_weight * min(s_new, lp.f_failure_span_cap)
                                                     + lp.f_budget_weight * top / cap)
    h, F = min(lp.h_max, max(lp.h_min, h)), min(lp.f_max, max(lp.f_min, F))
    p = 1 - math.exp(-lp.churn_base_hazard * d * math.exp(lp.churn_fatigue_sensitivity * F))
    assert st.state.hoarding == pytest.approx(h) and st.state.fatigue == pytest.approx(F)
    assert st.state.failure_span == pytest.approx(s_new) and st.p_churn == pytest.approx(p)
    assert not st.churned and st.state.active_at_end
    st2 = LC.step(lp, prev, ci_post=ci, elapsed_days=span, topup_pulls_used=0, paid_cap=0,
                  u_churn=p * 0.5, exit_day=span)
    assert st2.churned and st2.state.exit_reason == LC.EXIT_CHURN and st2.budget_utilization == 0.0
    ok = LC.step(lp, prev, ci_post=lp.full_star_threshold, elapsed_days=span, topup_pulls_used=0, paid_cap=5,
                 u_churn=0.9, exit_day=span)
    assert ok.state.failure_span == 0.0 and ok.gap == 0.0
    assert ok.state.fatigue >= lp.f_min and ok.p_churn > 0  # 满星也有基线流失
    with pytest.raises(LC.LifecycleError):
        LC.step(lp, prev, ci_post=None, elapsed_days=span, topup_pulls_used=0, paid_cap=0, u_churn=0.5, exit_day=0)
    with pytest.raises(LC.LifecycleError):
        LC.step(lp, LC.inactive_state(st2.state), ci_post=1, elapsed_days=span, topup_pulls_used=0,
                paid_cap=0, u_churn=0.5, exit_day=0)


# =========================================================================== 情景解析


def test_world_resolution(small_cfg):
    rt = SIM.Runtime(small_cfg, 1)
    w = rt.world
    tl = small_cfg.module("scenarios")["timeline"]
    assert len(w.periods) == tl["n_periods"]
    ref = small_cfg.module("scenarios")["reference_scenarios"][small_cfg.module("experiments")["base_scenario"]]
    hp0 = rt.model.params.hp_index
    for p in w.periods:
        assert p.scenario.hp == pytest.approx(hp0 * (1 + ref["hp_growth"]) ** (p.t_idx - 1))
        assert 0 <= p.scenario.theta <= 1
        assert p.start_day == tl["days_per_period"] * (p.t_idx - 1)
    assert set(w.supports) == set(rt.model.params.support_gains)
    assert SIM.STARTER_ID in w.anchors and not set(w.supports) & set(w.anchors)


# =========================================================================== 端到端对账


def _q(w, sql, *a):
    return w.query(sql, a)


def test_run_completed_and_fk_clean(run_a):
    res, w = run_a
    assert _q(w, "SELECT status FROM run_manifest WHERE run_id=?", res.run_id) == [("completed",)]
    assert _q(w, "SELECT status, log_mode FROM run_attempt") == [("completed", "full")]
    assert _q(w, "PRAGMA foreign_key_check") == []
    n_periods = len(res.world.periods)
    assert _q(w, "SELECT COUNT(*) FROM fact_player_lifecycle")[0][0] == N_SMALL * n_periods
    assert _q(w, "SELECT COUNT(*) FROM fact_pull_event")[0][0] > 0


def test_pull_counts_and_four_sources(run_a):
    _, w = run_a
    rows = _q(w, """
        SELECT a.player_id, a.t_idx, a.total_pulls,
               a.curr_free_used + a.hoarded_free_used + a.sub_pulls_used + a.topup_pulls_used,
               (SELECT COUNT(*) FROM fact_pull_event p WHERE p.run_id=a.run_id AND p.player_id=a.player_id AND p.t_idx=a.t_idx)
        FROM agg_player_period a""")
    assert rows
    for _, _, total, four, n in rows:
        assert total == n and four == pytest.approx(total, abs=1e-9)


def test_cash_reconciliation(run_a, cfg):
    _, w = run_a
    price = cfg.get("economy.topup.price_fen_per_pull")
    sub_price = cfg.get("economy.subscription.price_fen")
    rows = _q(w, """
        SELECT a.player_id, a.t_idx, a.sub_revenue_fen, a.topup_revenue_fen, a.topup_pulls_used,
          COALESCE((SELECT SUM(amount_fen) FROM fact_cash_event c WHERE c.run_id=a.run_id AND c.player_id=a.player_id
                    AND c.t_idx=a.t_idx AND c.purchase_kind='subscription'),0),
          COALESCE((SELECT SUM(amount_fen) FROM fact_cash_event c WHERE c.run_id=a.run_id AND c.player_id=a.player_id
                    AND c.t_idx=a.t_idx AND c.purchase_kind='instant_topup'),0),
          (SELECT COUNT(*) FROM fact_pull_event p WHERE p.run_id=a.run_id AND p.player_id=a.player_id
                    AND p.t_idx=a.t_idx AND p.paid_cash_event_id IS NOT NULL)
        FROM agg_player_period a""")
    for _, _, sub, top, top_pulls, cs, ct, paid_pulls in rows:
        assert (sub, top) == (cs, ct)
        assert top == price * top_pulls and top_pulls == paid_pulls
        assert sub % sub_price == 0
    # 直购不超过本期额度
    assert _q(w, "SELECT COUNT(*) FROM fact_player_lifecycle WHERE paid_used > paid_cap")[0][0] == 0


def test_wallet_snapshot_conservation(run_a):
    """期初快照 + 本期资源事件 = 期末快照（按来源 × 获得期）。"""
    _, w = run_a
    rows = _q(w, """
        WITH k AS (
          SELECT player_id, t_idx, provenance, earned_period FROM fact_wallet_snapshot
          UNION SELECT player_id, t_idx, provenance, earned_period FROM fact_resource_event
                WHERE provenance <> 'instant_topup'),
        s AS (SELECT player_id, t_idx, provenance, earned_period, balance_minor b FROM fact_wallet_snapshot WHERE phase='start'),
        e AS (SELECT player_id, t_idx, provenance, earned_period, balance_minor b FROM fact_wallet_snapshot WHERE phase='end'),
        r AS (SELECT player_id, t_idx, provenance, earned_period, SUM(delta_minor) d FROM fact_resource_event
              GROUP BY 1,2,3,4)
        SELECT k.*, COALESCE(s.b,0), COALESCE(r.d,0), COALESCE(e.b,0) FROM k
          LEFT JOIN s USING (player_id, t_idx, provenance, earned_period)
          LEFT JOIN r USING (player_id, t_idx, provenance, earned_period)
          LEFT JOIN e USING (player_id, t_idx, provenance, earned_period)""")
    assert rows
    for *_, s, d, e in rows:
        assert s + d == e
    top = _q(w, "SELECT COALESCE(SUM(delta_minor),0) FROM fact_resource_event WHERE provenance='instant_topup'")
    assert top == [(0,)]  # 直购即买即用，无待用余额


def test_pity_continuity(run_a):
    _, w = run_a
    bad = _q(w, """
        SELECT a.player_id, a.t_idx FROM fact_pity_snapshot a JOIN fact_pity_snapshot b
          ON a.player_id=b.player_id AND a.pity_group=b.pity_group AND b.t_idx=a.t_idx+1
        WHERE a.phase='end' AND b.phase='start' AND (a.pity<>b.pity OR a.guaranteed<>b.guaranteed)""")
    assert bad == []
    last = _q(w, """
        SELECT p.player_id, p.t_idx, p.pity_group, p.pity_after, p.guaranteed_after, s.pity, s.guaranteed
        FROM fact_pull_event p JOIN fact_pity_snapshot s
          ON s.player_id=p.player_id AND s.t_idx=p.t_idx AND s.pity_group=p.pity_group AND s.phase='end'
        WHERE p.rowid IN (SELECT MAX(rowid) FROM fact_pull_event GROUP BY player_id, t_idx, pity_group)""")
    assert last and all(r[3] == r[5] and r[4] == r[6] for r in last)
    # 同一保底组内逐抽衔接
    chain = _q(w, "SELECT player_id, pity_group, pity_before, guaranteed_before, pity_after, guaranteed_after "
                  "FROM fact_pull_event ORDER BY player_id, pity_group, t_idx, rowid")
    prev = {}
    for pid, g, pb, gb, pa, ga in chain:
        if (pid, g) in prev:
            assert prev[(pid, g)] == (pb, gb)
        prev[(pid, g)] = (pa, ga)


def test_ownership_matches_asset_events(run_a):
    _, w = run_a
    last_t = _q(w, "SELECT MAX(t_idx) FROM fact_ownership")[0][0]
    own = {(p, k, a): c for p, k, a, c in _q(
        w, "SELECT player_id, asset_kind, asset_id, copies FROM fact_ownership WHERE t_idx=?", last_t)}
    ev = {(p, k, a): c for p, k, a, c in _q(
        w, "SELECT player_id, asset_kind, asset_id, SUM(copies_delta) FROM fact_asset_event GROUP BY 1,2,3")}
    assert own.keys() == ev.keys()
    for key, n in ev.items():
        if key[1] == "character":
            assert own[key] == min(n, C.MAX_CINEMA + 1)
        else:
            assert own[key] == n
    up = _q(w, "SELECT COUNT(*) FROM fact_pull_event WHERE is_S=1")[0][0]
    assert up == _q(w, "SELECT COUNT(*) FROM fact_asset_event WHERE event_kind='pull'")[0][0]


def test_lifecycle_and_inactive_rules(run_a):
    res, w = run_a
    rows = _q(w, "SELECT player_id, t_idx, active_start, active_end FROM fact_player_lifecycle ORDER BY 1,2")
    last = {}
    for pid, t, s, e in rows:
        if pid in last:
            assert s == last[pid]  # 期初活跃 = 上期末活跃
        last[pid] = e
    inactive = "(SELECT player_id, t_idx FROM fact_player_lifecycle WHERE active_start=0)"
    for tb in ("fact_pull_event", "fact_cash_event", "fact_decision_log"):
        assert _q(w, f"SELECT COUNT(*) FROM {tb} WHERE (player_id, t_idx) IN {inactive}")[0][0] == 0
    churned = {r[0] for r in _q(w, "SELECT player_id FROM fact_player_lifecycle WHERE churn_this_period=1")}
    assert churned == {r[0] for r in _q(w, "SELECT player_id FROM fact_frozen_state")}
    n_frozen_rows = _q(w, f"SELECT COUNT(*) FROM fact_player_roster_panel WHERE eval_phase='frozen_post' "
                          f"AND (player_id, t_idx) IN {inactive}")[0][0]
    n_inactive = _q(w, f"SELECT COUNT(*) FROM {inactive}")[0][0]
    assert (n_inactive == 0) == (n_frozen_rows == 0)
    assert _q(w, "SELECT COUNT(*) FROM fact_player_roster_panel WHERE eval_phase='post' "
                 "AND eval_status='inactive' AND clear_index IS NOT NULL")[0][0] == 0


def test_pre_post_panels(run_a):
    _, w = run_a
    # pre 只含决策前持有角色；post ⊇ pre；CI_post ≥ CI_pre
    extra = _q(w, """
        SELECT pre.player_id, pre.t_idx, pre.unit_id FROM fact_player_roster_panel pre
        WHERE pre.eval_phase='pre' AND NOT EXISTS (SELECT 1 FROM fact_player_roster_panel post
          WHERE post.eval_phase='post' AND post.player_id=pre.player_id AND post.t_idx=pre.t_idx AND post.unit_id=pre.unit_id)""")
    assert extra == []
    new_in_pre = _q(w, """
        SELECT c.player_id, c.unit_id FROM fact_unit_cohort c JOIN fact_player_roster_panel p
          ON p.player_id=c.player_id AND p.t_idx=c.acquisition_period AND p.unit_id=c.unit_id AND p.eval_phase='pre'""")
    assert new_in_pre == []
    assert _q(w, "SELECT COUNT(*) FROM fact_player_lifecycle WHERE active_start=1 AND CI_post < CI_pre - 1e-12")[0][0] == 0
    dep = _q(w, "SELECT player_id, t_idx, eval_phase, SUM(is_deployed_main) FROM fact_player_roster_panel "
                "WHERE eval_phase IN ('pre','post') AND active_start=1 GROUP BY 1,2,3")
    assert dep and all(r[3] == 1 for r in dep)
    m = _q(w, """SELECT l.CI_post, MAX(p.clear_index) FROM fact_player_lifecycle l JOIN fact_player_roster_panel p
                 ON p.player_id=l.player_id AND p.t_idx=l.t_idx AND p.eval_phase='post'
                 WHERE l.active_start=1 GROUP BY l.player_id, l.t_idx""")
    assert all(a == pytest.approx(b) for a, b in m)


def test_decision_log_consistency(run_a):
    _, w = run_a
    rows = _q(w, "SELECT p_enter, did_enter, skip_reason, available_pulls, remaining_paid_cap FROM fact_decision_log")
    assert rows
    for p, did, reason, F, P in rows:
        if p is None:
            assert not did and reason in (DEC.SKIP_NO_BUDGET, DEC.SKIP_TARGET_OWNED, DEC.SKIP_CI_UNAVAILABLE)
        else:
            assert 0 < p < 1 and (did == 1) == (reason is None)
    entered_no_pull = _q(w, """SELECT COUNT(*) FROM fact_decision_log d WHERE did_enter=1 AND NOT EXISTS
        (SELECT 1 FROM fact_pull_event p WHERE p.player_id=d.player_id AND p.t_idx=d.t_idx AND p.banner_id=d.banner_id)""")
    assert entered_no_pull[0][0] == 0
    declined_pull = _q(w, """SELECT COUNT(*) FROM fact_pull_event p JOIN fact_decision_log d
        ON p.player_id=d.player_id AND p.t_idx=d.t_idx AND p.banner_id=d.banner_id WHERE d.did_enter=0""")
    assert declined_pull[0][0] == 0


def test_partial_acquisition_kept(stress):
    res, w, c = stress
    partial = sum(s.partial for s in res.summaries)
    assert partial > 0
    rows = _q(w, """
        SELECT d.player_id, d.t_idx, d.banner_id, COUNT(p.pull_event_id), SUM(p.is_UP)
        FROM fact_decision_log d JOIN fact_pull_event p
          ON p.player_id=d.player_id AND p.t_idx=d.t_idx AND p.banner_id=d.banner_id
        WHERE d.did_enter=1 GROUP BY 1,2,3""")
    paths = c.module("gacha")["target_paths"]
    some_partial_with_up = False
    for pid, t, banner, n, ups in rows:
        dl = _q(w, "SELECT target_path FROM fact_decision_log WHERE player_id=? AND t_idx=? AND banner_id=?",
                pid, t, banner)[0][0]
        if ups < len(paths[dl]):
            # 未完成：钱包或额度已无法再付一抽，已获部件保留在持有表
            end = res.players[pid]
            if ups > 0:
                some_partial_with_up = True
                unit = _q(w, "SELECT target_unit_id FROM dim_banner WHERE banner_id=?", banner)[0][0]
                held = _q(w, "SELECT COUNT(*) FROM fact_ownership WHERE player_id=? AND t_idx=? AND asset_id IN (?,?)",
                          pid, t, unit, C.signature_weapon_id(unit))[0][0]
                assert held >= 1
    assert some_partial_with_up or partial > 0
    for ps in res.players.values():
        pol = W.WalletPolicy.from_config(c.module("economy"))
        assert ps.wallet.total_credit_minor >= 0 and pol.credit_per_pull > 0


def test_reproducible_and_order_independent(run_a, small_cfg, seed):
    res, w = run_a
    w2 = SQLiteWriter(":memory:")
    SIM.run_simulation(small_cfg, master_seed=seed, writer=w2, player_order=list(reversed(range(N_SMALL))))
    for tb in FACT_TABLES:
        assert w.table_digest(tb) == w2.table_digest(tb), tb


def test_seed_changes_outcomes(run_a, small_cfg, cfg):
    res, w = run_a
    other = cfg.metadata["module_metadata"]["experiments"]["train_seeds"][1]
    w2 = SQLiteWriter(":memory:")
    r2 = SIM.run_simulation(small_cfg, master_seed=other, writer=w2)
    assert r2.run_id != res.run_id and r2.config_id == res.config_id
    assert w.table_digest("fact_pull_event", exclude=("run_id",)) != w2.table_digest("fact_pull_event", exclude=("run_id",))


def test_summary_mode_same_aggregates(run_a, small_cfg, seed):
    res, w = run_a
    w2 = SQLiteWriter(":memory:", log_mode="summary")
    SIM.run_simulation(small_cfg, master_seed=seed, writer=w2)
    assert w2.query("SELECT COUNT(*) FROM fact_pull_event") == [(0,)]
    for tb in ("agg_player_period", "fact_player_lifecycle", "fact_wallet_snapshot", "fact_pity_snapshot"):
        assert w.table_digest(tb) == w2.table_digest(tb), tb


def test_single_pull_atomic_on_failure(small_cfg, seed):
    rt = SIM.Runtime(small_cfg, seed)
    ps, _ = rt.init_player(0)
    ps.wallet = W.grant_free(rt.policy, W.roll_period(ps.wallet), t_idx=1, start_day=0,
                             end_day=rt.world.periods[0].end_day, grant_day=0).state
    ps.batch_earned["free:t1"] = 1
    period = rt.world.periods[0]
    banner = period.banners[0]
    plan = G.build_target_plan(rt.gacha, "0+1", banner.unit_id, {"character": banner.banner_id, "weapon": banner.banner_id})
    before = (ps.wallet, ps.roster, dict(ps.pities))
    orig = R.apply_asset_deltas
    try:
        def boom(*a, **k):
            raise RuntimeError("roster failure")
        SIM.R.apply_asset_deltas = boom
        with pytest.raises(RuntimeError):
            rt._one_pull(ps, period, banner, plan.stages[0], 0)
    finally:
        SIM.R.apply_asset_deltas = orig
    assert (ps.wallet, ps.roster, ps.pities) == before  # 无半提交
    ok = rt._one_pull(ps, period, banner, plan.stages[0], 0)
    assert ok is not None and ps.wallet != before[0]


def test_failed_run_marked(small_cfg, seed):
    w = SQLiteWriter(":memory:")
    rt = SIM.Runtime(small_cfg, seed, w)
    orig = rt.step_period

    def bad(ps, period, out):
        if period.t_idx == 2:
            raise SIM.SimulationError("injected")
        return orig(ps, period, out)

    rt.step_period = bad
    with pytest.raises(SIM.SimulationError):
        rt.run()
    assert w.query("SELECT status FROM run_manifest") == [("failed",)]
    assert w.query("SELECT status, error_code FROM run_attempt") == [("failed", "SimulationError")]
