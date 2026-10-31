"""M2：战斗代理、基准面板、抽卡内核、钱包与库存。

数值全部经 resolve_config 从 config/ 读取；仅有的字面量是设计文档冻结的
v1.0 校验例（0.5221518987、340/700、DP 均值），用于确认当前配置复现它们。
"""

from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

import pytest

from zzz_research import contracts as C
from zzz_research.agents import roster as R
from zzz_research.combat import benchmark as BM
from zzz_research.combat import proxy as P
from zzz_research.config import resolve_config
from zzz_research.economy import gacha as G
from zzz_research.economy import wallet as W
from zzz_research.rng import HashCounterRng

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "config" / "experiment.json"


@pytest.fixture(scope="module")
def cfg():
    return resolve_config(ENTRY)


@pytest.fixture(scope="module")
def params(cfg):
    return P.ProxyParams.from_config(cfg.module("combat"))


@pytest.fixture(scope="module")
def gm(cfg):
    return G.GachaModel.from_config(cfg.module("gacha"))


@pytest.fixture(scope="module")
def policy(cfg):
    return W.WalletPolicy.from_config(cfg.module("economy"))


@pytest.fixture(scope="module")
def rng(cfg):
    seed = cfg.metadata["module_metadata"]["experiments"]["train_seeds"][0]
    return HashCounterRng.from_config(seed, cfg.module("randomness"))


def _starter(cfg):
    return cfg.module("scenarios")["initial_assets"]["starter_dps"]


def _neutrals(cfg):
    return list(cfg.module("scenarios")["initial_assets"]["neutral_units"])


# ===========================================================================
# combat/proxy
# ===========================================================================


def test_normal_shield_retention_v1_reference(params):
    """v1.0 校验例：794/(794+1.8·952.8) ÷ 794/(794+952.8) × (1−0.25) = 0.5221518987。"""
    r = P.normal_shield_retention(params)
    assert r == pytest.approx(0.5221518987, abs=5e-11)
    k, d, b, ds = (params.level_coefficient, params.reference_defense,
                   params.shield_defense_bonus, params.shield_damage_reduction)
    assert r == pytest.approx((k / (k + (1 + b) * d)) / (k / (k + d)) * (1 - ds), rel=1e-15)


def test_full_pierce_retention_only_damage_reduction(params):
    assert P.normal_shield_retention(params, pierce_share=1.0) == pytest.approx(
        1 - params.shield_damage_reduction)


def test_shield_share_bounds(params):
    for kappa in params.alignment_values:
        assert P.shield_damage_share(0.0, kappa) == 0.0
        assert P.shield_damage_share(1.0, kappa) == pytest.approx(1.0)
    assert P.shield_damage_share(0.3, 1.0) == pytest.approx(0.3)
    lo, hi = min(params.alignment_values), max(params.alignment_values)
    assert P.shield_damage_share(0.4, lo) < P.shield_damage_share(0.4, hi)


def test_effective_defense_branches(params):
    mods = P.DefenseModifiers(pen_ratio=1.0)
    assert P.effective_defense(params.reference_defense, params.shield_defense_bonus, 1, mods) == 0.0
    mods = P.DefenseModifiers(flat_reduction=10 * params.reference_defense)
    assert P.effective_defense(params.reference_defense, params.shield_defense_bonus, 0, mods) == 0.0
    assert P.defense_multiplier(params.level_coefficient, 0.0) == 1.0


def test_gear_growth(params):
    acq = 5.0
    assert params.gear(acq, acq) == 1.0
    assert params.gear(acq - 10, acq) == 1.0  # 获取前不成长
    g1, g2 = params.gear(acq + 10, acq), params.gear(acq + 100, acq)
    assert 1.0 < g1 < g2 < 1.0 + params.gear_max_bonus
    assert params.gear(acq + 1e6, acq) == pytest.approx(1.0 + params.gear_max_bonus)
    tau = params.gear_time_constant_days
    assert params.gear(acq + tau, acq) == pytest.approx(1 + params.gear_max_bonus * (1 - math.exp(-1)))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.1])
def test_domain_errors(params, bad):
    with pytest.raises(P.CombatInputError):
        P.shield_damage_share(bad, 1.0)
    with pytest.raises(P.CombatInputError):
        P.relative_output(1.0, 1.0, bad)


def test_cinema_and_support_factor(params):
    n = len(params.cinema_multipliers)
    assert P.cinema_multiplier(n - 1, params.cinema_multipliers) == params.cinema_multipliers[-1]
    with pytest.raises(P.CombatInputError):
        P.cinema_multiplier(n, params.cinema_multipliers)
    sid, g = sorted(params.support_gains.items())[0]
    assert P.support_factor(g, 2.0) == pytest.approx(1 + 2.0 * g)  # a 作用于增益
    assert P.support_factor(g, 0.0) == 1.0


def _scenario(params, sid, theta, uptime, **kw):
    return P.reference_scenario(params, sid, theta=theta, uptime=uptime, **kw)


def _req(params, sid, team, b_ref, *, v=0.0, cinema=0, sig=False, g=1.0, mv=None):
    return C.CombatRequest(team=tuple(team), anchor_unit_id=team[0] if team else "X",
                           investment={"B_ref": b_ref, "pierce_share": v, "cinema": cinema,
                                       "signature_weapon": sig},
                           actual_g=g, scenario_id=sid, model_version=mv or params.model_version)


def test_window_damage_formula(cfg, params):
    s = _starter(cfg)
    ev = cfg.module("scenarios")["events"][4]
    sup = sorted(params.support_gains)[-1]
    kappa = max(params.alignment_values)
    model = P.ProxyCombatModel(cfg.module("combat"),
                               [_scenario(params, "S", ev["theta"], ev["uptime"], kappa=kappa)])
    neutral = _neutrals(cfg)[0]
    res = model.evaluate(_req(params, "S", ["A", neutral, sup], s["B_ref"], v=0.3, cinema=1, sig=True, g=1.05))
    assert res.eval_status is C.EvalStatus.OK
    q = P.shield_damage_share(ev["theta"], kappa)
    k, d, b = params.level_coefficient, params.reference_defense, params.shield_defense_bonus
    mref = k / (k + d)
    q0 = 0.7 * (k / (k + d)) / mref + 0.3
    q1 = 0.7 * (k / (k + d * (1 + b))) / mref + 0.3
    expected = (s["B_ref"] * 1.05 * params.cinema_multipliers[1] * params.signature_weapon_multiplier
                * (1 + params.default_gain_scale * params.support_gains[sup]) * ev["uptime"]
                * ((1 - q) * q0 + q * (1 - params.shield_damage_reduction) * q1)) / params.hp_index
    assert res.ci == pytest.approx(expected, rel=1e-12)
    assert res.meets_threshold == (res.ci >= C.contract_value("behavior.full_star_threshold"))
    assert res.damage_breakdown["gap"] == pytest.approx(max(0.0, 1 - res.ci))


def test_retention_not_applied_twice(cfg, params):
    """θ=1、v=0：CI = B·uptime·r_normal/HP，而不是再乘一次 0.5222。"""
    model = P.ProxyCombatModel(cfg.module("combat"), [_scenario(params, "F", 1.0, 1.0)])
    b = _starter(cfg)["B_ref"]
    res = model.evaluate(_req(params, "F", ["A", "n1", "n2"], b))
    r = P.normal_shield_retention(params)
    assert res.ci == pytest.approx(b * r / params.hp_index)
    assert res.ci != pytest.approx(b * r * r / params.hp_index)


def test_evaluate_status_paths(cfg, params):
    model = P.ProxyCombatModel(cfg.module("combat"), [_scenario(params, "S", 0.2, 1.0)])
    b = _starter(cfg)["B_ref"]
    sups = sorted(params.support_gains)
    assert model.evaluate(_req(params, "nope", ["A", "n", "m"], b)).eval_status is C.EvalStatus.MISSING_MODEL
    assert model.evaluate(_req(params, "S", ["A", "n", "m"], b, mv="other")).eval_status is C.EvalStatus.UNSUPPORTED
    two = model.evaluate(_req(params, "S", ["A", sups[0], sups[1]], b))
    assert two.eval_status is C.EvalStatus.UNSUPPORTED and two.ci is None  # 不叠加两个支援
    nf = model.evaluate(_req(params, "S", [], b))
    assert nf.eval_status is C.EvalStatus.NO_FEASIBLE_TEAM and nf.ci == 0.0
    missing_g = C.CombatRequest(("A",), "A", {"B_ref": b, "pierce_share": 0, "cinema": 0,
                                              "signature_weapon": False}, None, "S", params.model_version)
    assert model.evaluate(missing_g).ci is None
    with pytest.raises(P.CombatInputError):
        model.evaluate(_req(params, "S", ["A", "n", "m"], b, g=0.9))
    assert isinstance(model, C.CombatEngine)


# ===========================================================================
# combat/benchmark
# ===========================================================================


def test_fixed_benchmark_panel(cfg, params):
    model = P.ProxyCombatModel(cfg.module("combat"))
    s = _starter(cfg)
    anchors = [BM.BenchmarkAnchor("starter_dps", s["B_ref"], s["pierce_share"]),
               BM.BenchmarkAnchor("pierce_x", cfg.module("scenarios")["product_power"]["B_ref_first"], 1.0)]
    envs = [BM.BenchmarkEnvironment(f"t{e['t']}", params.hp_index, e["theta"], e["uptime"])
            for e in cfg.module("scenarios")["events"][:3]]
    n1, n2 = _neutrals(cfg)
    gears = (1.0, params.gear(84, 0))
    rows = BM.build_fixed_benchmark_panel(model, anchors, envs, neutral_fixed=n1, neutral_flex=n2, gears=gears)
    inv = BM.investment_grid(model)
    assert {i.label for i in inv} == {f"{c}+{w}" for c in range(len(params.cinema_multipliers)) for w in (0, 1)}
    expected_n = len(envs) * len(params.alignment_values) * len(anchors) * len(inv) \
        * (1 + len(params.support_gains)) * len(gears)
    assert len(rows) == expected_n
    assert len({r.key for r in rows}) == expected_n
    assert all(r.eval_status is C.EvalStatus.OK for r in rows)
    assert any(r.meets_threshold is False for r in rows)  # 低于达标线如实记录
    assert rows == sorted(rows, key=lambda r: r.key)
    # 配对：只增加支援 → ΔCI > 0
    sup = sorted(params.support_gains)[0]
    pairs = BM.paired_differences(rows, "support", None, sup)
    assert len(pairs) == expected_n // (1 + len(params.support_gains))
    assert all(d > 0 for _, _, d in pairs)
    # 完全贯穿的锚定在不同 κ 下不受盾防御影响，但仍受盾减伤
    bid2 = BM.build_fixed_benchmark_panel(P.ProxyCombatModel(cfg.module("combat")), anchors, envs,
                                          neutral_fixed=n1, neutral_flex=n2, gears=gears)[0].benchmark_id
    assert bid2 == rows[0].benchmark_id
    assert BM.Investment.parse("2+1") == BM.Investment(2, True)
    with pytest.raises(P.CombatInputError):
        BM.Investment.parse("2+2")


# ===========================================================================
# economy/gacha
# ===========================================================================


def test_probability_curve(gm):
    for pool in gm.pools.values():
        ps = [pool.s_probability(n) for n in range(1, pool.hard_pity + 1)]
        assert ps[-1] == 1.0
        assert all(a <= b for a, b in zip(ps, ps[1:]))
        assert ps[0] == pool.base_s_probability
        assert ps[pool.soft_pity_start - 1] == pytest.approx(pool.base_s_probability + pool.soft_increment)
        assert sum(G.next_s_distribution(pool)) == pytest.approx(1.0)
        assert sum(G.up_cost_distribution(pool)) == pytest.approx(1.0)
        with pytest.raises(G.GachaError):
            pool.s_probability(pool.hard_pity + 1)


def test_dp_means_match_design_reference(gm):
    """gacha.md 6.3 独立 DP 校验参考（当前假设曲线的数学结果）。"""
    r = C.RosterState()
    ch, wp = gm.pool("character"), gm.pool("weapon")
    assert G.expected_up_pulls(ch) == pytest.approx(93.445998, abs=5e-7)
    assert G.expected_up_pulls(wp) == pytest.approx(62.366232, abs=5e-7)
    banners = {"character": "BC", "weapon": "BW"}
    assert G.expected_plan_pulls(gm, G.build_target_plan(gm, "0+1", "U", banners), r) == pytest.approx(155.812230, abs=5e-6)
    assert G.expected_plan_pulls(gm, G.build_target_plan(gm, "2+1", "U", banners), r) == pytest.approx(342.704226, abs=5e-6)


def test_worst_case_capacity(gm):
    r = C.RosterState()
    ch, wp = gm.pool("character"), gm.pool("weapon")
    banners = {"character": "BC", "weapon": "BW"}
    plan01 = G.build_target_plan(gm, "0+1", "U", banners)
    plan21 = G.build_target_plan(gm, "2+1", "U", banners)
    assert G.worst_case_plan_pulls(gm, plan01, r) == 2 * ch.hard_pity + 2 * wp.hard_pity
    assert G.worst_case_plan_pulls(gm, plan21, r) == 6 * ch.hard_pity + 2 * wp.hard_pity
    # v1.0 校验例
    assert (G.worst_case_plan_pulls(gm, plan01, r), G.worst_case_plan_pulls(gm, plan21, r)) == (340, 700)
    # 已有状态缩短上界
    pity = {ch.pity_group: C.PityState(ch.pity_group, 30, True)}
    assert G.worst_case_plan_pulls(gm, plan01, r, pity) == (ch.hard_pity - 30) + 2 * wp.hard_pity
    assert G.worst_case_up_pulls(ch, C.PityState(ch.pity_group, 30, False)) == ch.hard_pity - 30 + ch.hard_pity


def test_transition_rules(gm):
    ch = gm.pool("character")
    p0 = gm.fresh_pity("character")
    t = G.transition(ch, p0, 0.999, 0.0)
    assert not t.is_s and t.pity_after == C.PityState(ch.pity_group, 1, False)
    t = G.transition(ch, p0, 0.0, 0.999)  # S 但歪
    assert t.is_s and not t.is_up and t.pity_after == C.PityState(ch.pity_group, 0, True)
    t = G.transition(ch, C.PityState(ch.pity_group, 5, True), 0.0, 0.999)  # 保证 → 必 UP
    assert t.is_up and t.pity_after == C.PityState(ch.pity_group, 0, False)
    t = G.transition(ch, C.PityState(ch.pity_group, ch.hard_pity - 1, False), 0.9999999, 0.0)
    assert t.is_s  # 硬保底必出
    with pytest.raises(G.GachaError):
        G.transition(ch, C.PityState(ch.pity_group, ch.hard_pity, False), 0.0, 0.0)
    with pytest.raises(G.GachaError):
        G.transition(ch, gm.fresh_pity("weapon"), 0.0, 0.0)


def test_resolve_one_pull_assets(gm):
    ch = gm.pool("character")
    out = G.resolve_one_pull(gm, "weapon", gm.fresh_pity("weapon"), u_s=0.0, u_up=0.0, pull_id="x",
                             banner_id="BW", draw_slot=0, up_unit_id="U", day=1)
    assert out.is_up and out.asset_deltas[0].asset_id == C.signature_weapon_id("U")
    out = G.resolve_one_pull(gm, "character", gm.fresh_pity("character"), u_s=0.0, u_up=0.999,
                             pull_id="y", banner_id="BC", draw_slot=0, up_unit_id="U", day=1)
    assert out.result_asset_id == G.OFFRATE_CATEGORY[C.PoolKind.CHARACTER]
    assert out.asset_deltas[0].asset_kind is C.AssetKind.CATEGORY
    out = G.resolve_one_pull(gm, "character", gm.fresh_pity("character"), u_s=0.999, u_up=0.0,
                             pull_id="z", banner_id="BC", draw_slot=0, up_unit_id="U", day=1)
    assert out.result_asset_id == gm.non_s_result and out.asset_deltas == ()
    assert ch.pity_group == out.pity_after.pity_group


def test_monte_carlo_matches_dp(gm, rng):
    """用公共随机数实际抽到 UP 的平均抽数与 DP 均值一致；pity 永不越界。"""
    ch = gm.pool("character")
    n_players, total = 2000, 0
    for pid in range(n_players):
        pity, slot = gm.fresh_pity("character"), 0
        while True:
            out = G.draw_with_rng(gm, rng, player_id=pid, t_idx=1, banner_id="BC", pool_kind="character",
                                  draw_slot=slot, pity=pity, up_unit_id="U", day=42)
            assert out.pity_after.pulls_since_s < ch.hard_pity
            pity, slot = out.pity_after, slot + 1
            if out.is_up:
                break
        total += slot
    mean = G.expected_up_pulls(ch)
    sd = math.sqrt(sum((i + 1 - mean) ** 2 * p for i, p in enumerate(G.up_cost_distribution(ch))))
    assert abs(total / n_players - mean) < 4 * sd / math.sqrt(n_players)


def test_draw_slots_are_stable(gm, rng):
    pity = C.PityState(gm.pool("character").pity_group, 0, True)
    a = G.draw_with_rng(gm, rng, player_id=1, t_idx=2, banner_id="B", pool_kind="character",
                        draw_slot=7, pity=pity, up_unit_id="U", day=0)
    b = G.draw_with_rng(gm, rng, player_id=1, t_idx=2, banner_id="B", pool_kind="character",
                        draw_slot=7, pity=pity, up_unit_id="U", day=0)
    assert a == b


def test_target_plan_skips_owned(cfg, gm):
    plan = G.build_target_plan(gm, "2+1", "U", {"character": "BC", "weapon": "BW"})
    assert [s.component for s in plan.stages] == list(cfg.module("gacha")["target_paths"]["2+1"])
    r = C.RosterState()
    assert G.next_stage(plan, r).component == plan.stages[0].component
    r = R.apply_asset_delta(r, C.AssetDelta("e1", C.AssetKind.CHARACTER, "U", 2, 1))  # 本体+1影
    r = R.apply_asset_delta(r, C.AssetDelta("e2", C.AssetKind.WEAPON, C.signature_weapon_id("U"), 1, 1))
    assert [s.component for s in G.pending_stages(plan, r)] == ["cinema_2"]
    assert G.worst_case_plan_pulls(gm, plan, r) == 2 * gm.pool("character").hard_pity
    with pytest.raises(G.GachaError):
        G.build_target_plan(gm, "0+1", "U", {"character": "BC"})


# ===========================================================================
# economy/wallet
# ===========================================================================


def test_free_grant_has_no_rounding_drift(cfg, policy):
    tl = cfg.module("scenarios")["timeline"]
    span, n = tl["days_per_period"], tl["n_periods"]
    per = [policy.free_credit_for(span * (t - 1), span * t) for t in range(1, n + 1)]
    total = policy.free_pulls_per_span * policy.credit_per_pull
    assert sum(per) == math.floor(Fraction(total) * span * n / policy.standard_span_days)
    odd = [0, 5, 17, 40, 41, 77, 100]
    parts = [policy.free_credit_for(a, b) for a, b in zip(odd, odd[1:])]
    assert sum(parts) == policy.free_credit_for(0, 100)
    assert policy.free_credit_for(0, policy.standard_span_days) == total


def test_paid_cap_and_renewals(cfg, policy):
    for tier in cfg.module("population")["tiers"]:
        B = tier["paid_pulls_per_standard_span"]
        assert policy.paid_cap_for(B, 0, policy.standard_span_days) == B
        assert sum(policy.paid_cap_for(B, a, a + 7) for a in range(0, 84, 7)) == policy.paid_cap_for(B, 0, 84)
    rd = policy.sub_renewal_days
    assert policy.renewal_days_in(0, rd) == [0]
    assert policy.renewal_days_in(1, rd) == []
    assert policy.renewal_days_in(rd, 2 * rd) == [rd]  # 区间 [start, end)


def _wallet_with(policy, hist=0, curr=0, sub=0):
    b = []
    if hist:
        b.append(C.CreditBatch("free:t0", C.CreditSource.HISTORICAL_FREE, hist, 0))
    if curr:
        b.append(C.CreditBatch("free:t1", C.CreditSource.CURRENT_FREE, curr, 42))
    if sub:
        b.append(C.CreditBatch("sub:s0", C.CreditSource.SUBSCRIPTION, sub, 10))
    return C.WalletState(batches=tuple(b))


def test_payment_order_and_split(policy):
    cpp = policy.credit_per_pull
    st = _wallet_with(policy, hist=cpp // 4, curr=cpp // 2, sub=cpp)
    ch = W.prepare_payment(policy, st, pull_id="p1", day=42)
    take = {d.batch_id: -d.delta_credit_minor for d in ch.resource_deltas}
    assert take == {"free:t0": cpp // 4, "free:t1": cpp // 2, "sub:s0": cpp - cpp // 4 - cpp // 2}
    assert [d.source for d in ch.resource_deltas] == [C.CreditSource.HISTORICAL_FREE,
                                                       C.CreditSource.CURRENT_FREE,
                                                       C.CreditSource.SUBSCRIPTION]
    assert all(d.related_pull_id == "p1" for d in ch.resource_deltas)
    W.check_conservation(st, ch.state, ch.resource_deltas)
    u = W.summarize_usage(policy, ch.resource_deltas)
    assert u.total == 1 and u.hoarded_free_used == Fraction(1, 4)


def test_fragments_kept_and_topup_whole_pull(policy):
    cpp = policy.credit_per_pull
    st = W.set_period_paid_cap(policy, _wallet_with(policy, curr=cpp - 1), 1)
    ch = W.prepare_payment(policy, st, pull_id="p1", day=42)
    assert len(ch.cash_events) == 1
    assert ch.cash_events[0].amount_fen == policy.topup_price_fen_per_pull
    assert ch.state.batches == st.batches  # 零碎余额不动
    assert ch.state.direct_purchase_remaining_pulls == 0
    W.check_conservation(st, ch.state, ch.resource_deltas)
    assert W.summarize_usage(policy, ch.resource_deltas).topup_pulls_used == 1
    assert W.prepare_payment(policy, ch.state, pull_id="p2", day=42) is None  # 额度用尽
    assert W.prepare_payment(policy, st, pull_id="p3", day=42, allow_topup=False) is None


def test_paid_cap_does_not_roll_over(policy):
    st = W.set_period_paid_cap(policy, C.WalletState(), 5)
    st = W.set_period_paid_cap(policy, st, 3)
    assert st.direct_purchase_remaining_pulls == (8 if policy.paid_budget_rollover else 3)


def test_conservation_detects_tampering(policy):
    cpp = policy.credit_per_pull
    st = _wallet_with(policy, curr=2 * cpp)
    ch = W.prepare_payment(policy, st, pull_id="p1", day=1)
    bad = C.WalletState(batches=(C.CreditBatch("free:t1", C.CreditSource.CURRENT_FREE, 2 * cpp, 42),))
    with pytest.raises(W.WalletError):
        W.check_conservation(st, bad, ch.resource_deltas)
    with pytest.raises(W.WalletError):
        W.check_conservation(st, ch.state, ch.resource_deltas + ch.resource_deltas)


def test_multi_period_wallet_conservation_and_revenue(cfg, policy, gm, rng):
    """10 期：免费发放 + 订阅 + 直购额度 + 实际抽取；逐批次守恒、四来源合计=总抽数、收入=现金聚合。"""
    tiers = {t["id"]: t for t in cfg.module("population")["tiers"]}
    tier = max(tiers.values(), key=lambda t: t["paid_pulls_per_standard_span"])
    tl = cfg.module("scenarios")["timeline"]
    span = tl["days_per_period"]
    state, all_deltas, pity, pulls = C.WalletState(), [], gm.fresh_pity("character"), 0
    for t in range(1, tl["n_periods"] + 1):
        start, end = span * (t - 1), span * t
        before = W.roll_period(state)
        ch = W.grant_free(policy, before, t_idx=t, start_day=start, end_day=end, grant_day=start)
        deltas, state = list(ch.resource_deltas), ch.state
        if tier["subscribed"]:
            for d in policy.renewal_days_in(start, end):
                ch = W.purchase_subscription(policy, state, day=d, event_id=f"sub:t{t}:{d}")
                deltas += ch.resource_deltas
                state = ch.state
        state = W.set_period_paid_cap(policy, state,
                                      policy.paid_cap_for(tier["paid_pulls_per_standard_span"], start, end))
        slot = 0
        target = 0 if t == 1 else 100 + (t * 37) % 200  # 首期囤抽，后续覆盖历史免费消费
        while slot < target:
            pid = f"t{t}:s{slot}"
            pay = W.prepare_payment(policy, state, pull_id=pid, day=end)
            if pay is None:
                break
            out = G.draw_with_rng(gm, rng, player_id=0, t_idx=t, banner_id=f"B{t}", pool_kind="character",
                                  draw_slot=slot, pity=pity, up_unit_id="U", day=end, pull_id=pid)
            pity = out.pity_after
            deltas += pay.resource_deltas
            state, slot, pulls = pay.state, slot + 1, pulls + 1
        W.check_conservation(before, state, deltas)
        all_deltas += deltas
    usage = W.summarize_usage(policy, all_deltas)
    assert usage.total == pulls and usage.total.denominator == 1
    rev = W.revenue(state.cash_events)
    n_sub = sum(1 for e in state.cash_events if e.product == W.PRODUCT_SUBSCRIPTION)
    assert rev.sub_revenue_fen == n_sub * policy.sub_price_fen
    assert rev.topup_revenue_fen == policy.topup_price_fen_per_pull * usage.topup_pulls_used
    assert rev.total_fen == sum(e.amount_fen for e in state.cash_events)
    # 期末余额 = 全部到账 − 全部消耗
    granted = sum(d.delta_credit_minor for d in all_deltas if d.delta_credit_minor > 0)
    spent = -sum(d.delta_credit_minor for d in all_deltas if d.delta_credit_minor < 0)
    assert state.total_credit_minor == granted - spent
    assert all(x > 0 for x in (usage.curr_free_used, usage.hoarded_free_used,
                               usage.sub_pulls_used, usage.topup_pulls_used))


def test_wallet_policy_rejects_unsupported(cfg):
    bad = C.deep_thaw(cfg.module("economy"))
    bad["spend_priority"] = ["instant_topup", "current_free"]
    with pytest.raises(W.WalletError):
        W.WalletPolicy.from_config(bad)
    bad = C.deep_thaw(cfg.module("economy"))
    bad["topup"]["balance_rollover"] = True
    with pytest.raises(W.WalletError):
        W.WalletPolicy.from_config(bad)


def test_non_mixed_payment(cfg):
    e = C.deep_thaw(cfg.module("economy"))
    e["allow_mixed_existing_provenance_payment"] = False
    pol = W.WalletPolicy.from_config(e)
    cpp = pol.credit_per_pull
    st = _wallet_with(pol, hist=cpp // 2, curr=cpp // 2)
    assert W.stock_pulls(pol, st) == 0
    st = _wallet_with(pol, hist=cpp // 2, curr=cpp)
    ch = W.prepare_payment(pol, st, pull_id="p", day=1, allow_topup=False)
    assert [d.batch_id for d in ch.resource_deltas] == ["free:t1"]


# ===========================================================================
# agents/roster
# ===========================================================================


def test_initial_roster(cfg):
    r = R.initial_roster(cfg.module("scenarios"), player_id=3)
    s = _starter(cfg)
    st = r.character("starter_dps")
    assert st.cinema == s["cinema"] and st.first_acquired_day == s["acquired_day"]
    assert st.equipped_signature == s["signature_weapon"]
    for n in _neutrals(cfg):
        assert r.character(n) is not None  # 中性角色是显式资产


def test_roster_apply_rules():
    r = C.RosterState()
    r = R.apply_asset_delta(r, C.AssetDelta("a", C.AssetKind.CHARACTER, "U", 1, 10))
    r = R.apply_asset_delta(r, C.AssetDelta("b", C.AssetKind.CHARACTER, "U", 1, 50))
    assert r.character("U").cinema == 1 and r.character("U").first_acquired_day == 10
    with pytest.raises(R.DuplicateAssetEvent):
        R.apply_asset_delta(r, C.AssetDelta("a", C.AssetKind.CHARACTER, "U", 1, 60))
    r = R.apply_asset_delta(r, C.AssetDelta("c", C.AssetKind.WEAPON, C.signature_weapon_id("U"), 1, 60))
    assert r.character("U").equipped_signature
    r = R.apply_asset_delta(r, C.AssetDelta("d", C.AssetKind.CATEGORY, "OFFRATE_S_CHAR", 1, 60))
    r = R.apply_asset_delta(r, C.AssetDelta("e", C.AssetKind.CATEGORY, "OFFRATE_S_CHAR", 1, 61))
    assert r.category_count("OFFRATE_S_CHAR") == 2
    r = R.apply_asset_delta(r, C.AssetDelta("f", C.AssetKind.CHARACTER, "U", C.MAX_CINEMA + 2, 70))
    assert r.character("U").cinema == C.MAX_CINEMA
    assert r.category_count(R.CINEMA_OVERFLOW_CATEGORY) == 3


def test_roster_disc_values_and_freeze(params):
    r = R.apply_asset_delta(C.RosterState(), C.AssetDelta("a", C.AssetKind.CHARACTER, "U", 1, 0))
    r = R.update_disc_values(r, {"U": params.gear(84, 0)})
    assert r.character("U").gear_value == pytest.approx(params.gear(84, 0))
    assert r.character("U").first_acquired_day == 0
    with pytest.raises(R.RosterError):
        R.update_disc_values(r, {"X": 1.0})
    f = R.freeze(r)
    assert f.frozen and all(c.frozen for c in f.characters)
    with pytest.raises(R.FrozenRosterError):
        R.apply_asset_delta(f, C.AssetDelta("z", C.AssetKind.CHARACTER, "V", 1, 0))
    with pytest.raises(R.FrozenRosterError):
        R.update_disc_values(f, {"U": 1.0})


def test_enumerate_feasible_teams(cfg, params):
    n1, n2 = _neutrals(cfg)
    sups = sorted(params.support_gains)
    r = R.initial_roster(cfg.module("scenarios"), player_id=0)
    r = R.apply_asset_delta(r, C.AssetDelta("s", C.AssetKind.CHARACTER, sups[0], 1, 1))
    r = R.apply_asset_delta(r, C.AssetDelta("d", C.AssetKind.CHARACTER, "DPS_X", 1, 1))
    teams = R.enumerate_feasible_teams(r, anchors=["starter_dps", "DPS_X", "DPS_NOT_HELD"],
                                       neutral_fixed=n1, neutral_flex=n2, supports=sups)
    assert teams == sorted(teams)
    assert {t[0] for t in teams} == {"starter_dps", "DPS_X"}
    assert all(t[1] == n1 and t[2] in {n2, sups[0]} for t in teams)
    assert all(sum(u in sups for u in t) <= 1 for t in teams)
    assert R.enumerate_feasible_teams(C.RosterState(), anchors=["starter_dps"], neutral_fixed=n1,
                                      neutral_flex=n2, supports=sups) == []


# ===========================================================================
# 单抽原子组装（wallet → gacha → roster → EventBundle）
# ===========================================================================


def test_single_pull_bundle_roundtrip(cfg, policy, gm, rng):
    cpp = policy.credit_per_pull
    w0 = _wallet_with(policy, curr=cpp)
    r0 = R.initial_roster(cfg.module("scenarios"), player_id=0)
    pity = C.PityState(gm.pool("character").pity_group, 0, True)
    pay = W.prepare_payment(policy, w0, pull_id="pull1", day=42)
    out = G.resolve_one_pull(gm, "character", pity, u_s=0.0, u_up=0.5, pull_id="pull1",
                             banner_id="BC", draw_slot=0, up_unit_id="DPS_01", day=42)
    r1 = R.apply_asset_deltas(r0, out.asset_deltas)
    bundle = C.EventBundle("b1", C.BundleKind.SINGLE_PULL, "run", 0, 1, w0, pay.state, r0, r1,
                           (pity,), (out.pity_after,), (out,), pay.resource_deltas, pay.cash_events,
                           out.asset_deltas)
    W.check_conservation(bundle.wallet_before, bundle.wallet_after, bundle.resource_deltas)
    assert bundle.roster_after.character("DPS_01") is not None
    # 无法支付 → 不产生抽取
    assert W.prepare_payment(policy, pay.state, pull_id="pull2", day=42, allow_topup=False) is None
