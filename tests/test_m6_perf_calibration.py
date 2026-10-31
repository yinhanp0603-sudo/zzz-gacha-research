"""2026-10-28：性能优化的逐位等价、情景标定验收与报告核心发现。

- 性能改动只改变计算方式：draw_pair≡draw_u、NumPy 查找表≡逐抽 DP、评估缓存≡直接评估、钱包只重建被扣款批次。
- 标定验收：检查当前配置满足登记的 L2 标定目标（不是模型发现，而是防止参数被无意改动）。
- 核心发现：只由数据推出；无拐点/中位未达到时如实表述。
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pytest

from zzz_research import contracts as C
from zzz_research.analytics import insights as IN
from zzz_research.analytics import reference as REF
from zzz_research.analytics import survival as SV
from zzz_research.config import resolve_config
from zzz_research.economy import gacha as G
from zzz_research.economy import planner as PL
from zzz_research.economy import wallet as W
from zzz_research import simulation as SIM
from zzz_research.rng import HashCounterRng, RngError

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "config" / "experiment.json"


@pytest.fixture(scope="module")
def cfg():
    return resolve_config(ENTRY)


# =========================================================================== 逐位等价


def test_draw_pair_bitwise_equal_to_draw_u(cfg):
    seeds = cfg.metadata["module_metadata"]["experiments"]["train_seeds"]
    for seed in seeds[:2]:
        r = HashCounterRng.from_config(seed, cfg.module("randomness"))
        for pid, t, b, pk, slot in itertools.product([0, 9, 2**40], [1, 10], ["SYN_P01_A", "中文", 'a"b\\c'],
                                                     ["character", "weapon"], [0, 1, 88, 10_000]):
            assert r.draw_pair(pid, t, b, pk, slot) == (r.draw_u(pid, t, b, pk, slot, "s_check"),
                                                       r.draw_u(pid, t, b, pk, slot, "up_check"))
    with pytest.raises(RngError):
        r.draw_pair(0, 1, "B", "character", True)
    with pytest.raises(RngError):
        r.draw_pair(0, 1, "", "character", 0)


def test_numpy_table_matches_reference_dp(cfg):
    gm = G.GachaModel.from_config(cfg.module("gacha"))
    for pool in gm.pools.values():
        table = PL.up_cost_table(pool)
        assert len(table) == 2 * pool.hard_pity
        for k in range(pool.hard_pity):
            for g in (False, True):
                ref = G.up_cost_distribution(pool, C.PityState(pool.pity_group, k, g))
                got = table[(k, g)]
                assert len(got) == len(ref)
                assert np.max(np.abs(got - np.asarray(ref))) < 1e-15
                assert not got.flags.writeable  # 共享表只读
        assert PL.up_cost_table(pool) is table   # 进程内共享，不重复计算


def test_evaluator_cache_equals_direct(cfg):
    small = resolve_config(ENTRY, overrides={"population.n_players": 1})
    rt = SIM.Runtime(small, 1)
    ps, _ = rt.init_player(0)
    p = rt.world.periods[3]
    first = rt.eval.anchors(ps.roster, p.scenario, p.end_day)
    again = rt.eval.anchors(ps.roster, p.scenario, p.end_day)
    direct = rt.eval._anchors(ps.roster, p.scenario, p.end_day)
    assert first == again == direct
    other = rt.eval.anchors(ps.roster, rt.world.periods[4].scenario, rt.world.periods[4].end_day)
    assert other != first  # 场景/日期进入缓存键


def test_wallet_keeps_untouched_batches(cfg):
    pol = W.WalletPolicy.from_config(cfg.module("economy"))
    cpp = pol.credit_per_pull
    a = C.CreditBatch("free:t0", C.CreditSource.HISTORICAL_FREE, 2 * cpp, 0)
    b = C.CreditBatch("sub:x", C.CreditSource.SUBSCRIPTION, cpp, 1)
    st = C.WalletState(batches=(a, b))
    ch = W.prepare_payment(pol, st, pull_id="p", day=1)
    assert ch.state.batches[1] is b and ch.state.batches[0].credit_minor == cpp
    W.check_conservation(st, ch.state, ch.resource_deltas)


# =========================================================================== 标定验收（登记的目标，不是发现）


def test_l2_calibration_targets(cfg):
    _, world, obs = REF.evaluate_trajectories(cfg)
    km = {k.group: k.median for k in SV.km_by_group(obs, ("investment_plan", "gear_policy", "support_policy"))}
    g = REF.GEAR_GROWTH
    none00, none01 = km[("0+0", g, "none")], km[("0+1", g, "none")]
    assert none00 != SV.MEDIAN_NOT_REACHED and 42 <= none00 <= 126
    assert none01 != SV.MEDIAN_NOT_REACHED and 84 <= none01 <= 168
    sa = km[("0+0", g, "join_on_debut:S_A")]
    assert sa == SV.MEDIAN_NOT_REACHED or sa >= 2 * none00   # 支援带来可辨识的寿命梯度
    # 0+0 无支援：全部主输出出道当期达标（DPS_09 例外已由 B_ref_first 标定消除）
    for o in obs:
        s = o.subject
        if s.investment_plan == "0+0" and s.gear_policy == g and s.support_policy == "none" and o.age_days == 0:
            assert o.ci >= 1.0, (s.anchor_unit_id, o.ci)
    # 0+1 无支援：出道当期与第 1 期全部达标
    for o in obs:
        s = o.subject
        if s.investment_plan == "0+1" and s.gear_policy == g and s.support_policy == "none" and o.age_days <= 42:
            assert o.ci >= 1.0, (s.anchor_unit_id, o.age_days, o.ci)


# =========================================================================== 核心发现


def test_sign_change_and_wording():
    levels = [{"level": 0, "x": 5.0}, {"level": 0.5, "x": 2.0}, {"level": 1, "x": 0.0}, {"level": 1.5, "x": -3.0}]
    assert IN.sign_change(levels, "x") is None  # 0 不是正→负的相邻转折
    levels[2]["x"] = 1.0
    assert IN.sign_change(levels, "x") == (1, 1.5)
    assert IN.sign_change([{"level": 0, "x": None}], "x") is None


def test_lifetime_findings_handle_unreached_and_zero():
    rows = [
        {"investment_plan": "0+0", "gear_policy": "growth_from_debut", "support_policy": "none", "median_age_days": "0"},
        {"investment_plan": "0+0", "gear_policy": "growth_from_debut", "support_policy": "join_on_debut:S_A",
         "median_age_days": "MedianNotReached"},
        {"investment_plan": "0+1", "gear_policy": "growth_from_debut", "support_policy": "none", "median_age_days": "84"},
        {"investment_plan": "0+1", "gear_policy": "growth_from_debut", "support_policy": "join_on_debut:S_A",
         "median_age_days": "210"},
    ]
    f = [x.text for x in IN.lifetime_findings(rows)]
    assert "超过观察窗口" in f[0] and "×" not in f[0].split("；")[1]
    assert "×2.5" in f[1]
    assert IN.render([]) == ""
    assert IN.render(IN.lifetime_findings(rows)).startswith("> **核心业务发现")


def test_search_findings_report_no_inflection_honestly():
    rows = []
    for lam in (0, 1, 2):
        for seed in (1, 2):
            rows.append({"candidate_id": f"L{lam}_A1", "split": "train", "perturbation_id": "P0",
                         "delta_revenue_vs_base": str(100 * (lam - 1) + seed), "delta_retention_vs_base": "0",
                         "total_revenue_fen": "1000", "core_retention_end": "0.9"})
    text = " ".join(x.text for x in IN.search_findings(rows, [], [], [], 1.0, 1.0))
    assert "过零" not in text            # 配对差单调上升、未由正转负：不报告过零
    assert "收入随水平单调上升" in text or "收入不随水平变化" in text or "收入随水平" in text


def test_profile_shape_and_largest_step():
    mk = lambda xs: [{"level": i * 0.5, "v": x} for i, x in enumerate(xs)]
    assert IN.profile_shape(mk([5, 4, 3, 2]), "v") == ("decreasing", None)
    assert IN.profile_shape(mk([1, 3, 2, 0]), "v") == ("interior_max", 0.5)
    assert IN.profile_shape(mk([3, 1, 2, 4]), "v") == ("interior_min", 0.5)
    assert IN.profile_shape(mk([1, 3, 1, 3]), "v")[0] == "mixed"
    st = IN.largest_step(mk([0.88, 0.85, 0.84, 0.80, 0.66]), "v")
    assert st[:2] == (1.5, 2.0) and abs(st[2] + 0.14) < 1e-9
    assert IN.largest_step(mk([1, 2]), "v") is None


def test_search_findings_do_not_call_reference_crossing_a_peak():
    rows = []
    for lam, rev in ((0, 300), (1, 200), (2, 100)):   # 单调下降，配对差跨零
        for seed in (1, 2):
            rows.append({"candidate_id": f"L{lam}_A1", "split": "train", "perturbation_id": "P0",
                         "delta_revenue_vs_base": str(rev - 150), "delta_retention_vs_base": "0",
                         "total_revenue_fen": str(rev), "core_retention_end": "0.9"})
    text = " ".join(x.text for x in IN.search_findings(rows, [], [], [], 1.0, 1.0))
    assert "单调下降" in text and "不是收入峰值" in text and "先升后降" not in text


def test_budget_fatigue_findings_from_log_counts():
    cross = [
        {"full_star_post": "0", "budget_used": "0", "player_periods": "200", "fatigue_after_zero": "10", "churned": "40"},
        {"full_star_post": "1", "budget_used": "0", "player_periods": "1500", "fatigue_after_zero": "1400", "churned": "5"},
        {"full_star_post": "1", "budget_used": "1", "player_periods": "50", "fatigue_after_zero": "50", "churned": "0"},
    ]
    f = IN.budget_fatigue_findings(cross, 0.2, 0.3, [0.75, 1.25])
    assert "50 个活跃玩家-期中 50 个" in f[0].text and "45 次流失里有 40 次" in f[0].text
    assert len(f) == 2 and "结构性" in f[1].text and "0.25" in f[1].text
    # 未满星玩家也直购：不给出“不重叠”的结论
    cross.append({"full_star_post": "0", "budget_used": "1", "player_periods": "5", "fatigue_after_zero": "0",
                  "churned": "2"})
    assert len(IN.budget_fatigue_findings(cross, 0.2, 0.3, [0.75, 1.25])) == 1
    # 扰动上限超过满星恢复时不声称结构性
    assert "结构性" not in IN.budget_fatigue_findings(cross[:3], 0.2, 0.3, [2.0])[-1].text
    assert "无从生效" in IN.budget_fatigue_findings(cross[:2], 0.2, 0.3)[0].text


def test_budget_fatigue_cross_export_matches_lifecycle(tmp_path):
    from zzz_research.analytics import cohorts as CO
    from zzz_research.storage.writer import SQLiteWriter
    small = resolve_config(ENTRY, overrides={"population.n_players": 20})
    w = SQLiteWriter(tmp_path / "r.sqlite")
    try:
        rid = SIM.run_simulation(small, master_seed=3, writer=w).run_id
        rows = CO.budget_fatigue_cross(w, rid)
        n = w.query("SELECT COUNT(*) FROM fact_player_lifecycle WHERE run_id=? AND active_start=1", (rid,))[0][0]
        assert n > 0 and sum(r["player_periods"] for r in rows) == n
        assert {(r["full_star_post"], r["budget_used"]) for r in rows} <= {(a, b) for a in (-1, 0, 1) for b in (0, 1)}
    finally:
        w.close()
