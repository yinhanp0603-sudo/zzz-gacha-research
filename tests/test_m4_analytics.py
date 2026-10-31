"""M4 统计与搜索：KM、秩相关、非支配集合、固定队列三口径、参考轨迹与 25 点协议。"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from zzz_research import contracts as C
from zzz_research.analytics import cohorts as CO
from zzz_research.analytics import reference as REF
from zzz_research.analytics import search as SE
from zzz_research.analytics import survival as SV
from zzz_research.combat.proxy import ProxyCombatModel
from zzz_research.config import resolve_config
from zzz_research.simulation import resolve_world, run_simulation
from zzz_research.storage.writer import SQLiteWriter

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "config" / "experiment.json"
N_SMALL = 12  # 测试负载


@pytest.fixture(scope="module")
def base():
    return resolve_config(ENTRY)


@pytest.fixture(scope="module")
def small_run(tmp_path_factory, base):
    cfg = resolve_config(ENTRY, overrides={"population.n_players": N_SMALL})
    db = tmp_path_factory.mktemp("cohort") / "run.sqlite"
    w = SQLiteWriter(db)
    seed = base.metadata["module_metadata"]["experiments"]["train_seeds"][0]
    res = run_simulation(cfg, master_seed=seed, writer=w)
    w.close()
    return db, res.run_id, cfg


# =========================================================================== KM / 秩 / 非支配


def _o(sid, t, ev, entry=0.0):
    return SV.SubjectOutcome(sid, ("g",), t, ev, entry, 1)


def test_kaplan_meier_hand_computed():
    outs = [_o("a", 0, True), _o("b", 42, True), _o("c", 42, False), _o("d", 84, True), _o("e", 126, False)]
    km = SV.kaplan_meier(outs)
    s = {st.time: st.survival for st in km.steps}
    assert s[0] == pytest.approx(4 / 5)                       # age=0 事件计入
    assert s[42] == pytest.approx(4 / 5 * 3 / 4)              # 同时点删失者仍在风险集
    assert s[84] == pytest.approx(4 / 5 * 3 / 4 * 1 / 2)
    assert km.median == 84 and km.n_events == 3
    assert km.survival_at(100) == pytest.approx(s[84])
    km_half = SV.kaplan_meier([_o("a", 42, False), _o("b", 84, True), _o("c", 84, False)])
    assert km_half.median == 84  # S=0.5 恰好满足“首个 S(t)≤0.5”
    km2 = SV.kaplan_meier([_o("a", 42, False), _o("b", 84, True), _o("c", 84, False), _o("d", 126, False)])
    assert km2.median == SV.MEDIAN_NOT_REACHED and km2.steps[-1].survival == pytest.approx(2 / 3)


def test_spearman_average_ranks_and_null():
    assert SE.average_ranks([3, 1, 3, 2]) == [3.5, 1.0, 3.5, 2.0]
    assert SE.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert SE.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert SE.spearman([1, 1, 1], [1, 2, 3]) is None        # 无变异 → NULL
    assert SE.spearman([1, 2], [1]) is None


def test_nondominated_keeps_ties():
    pts = {"a": (10.0, 0.5), "b": (10.0, 0.5), "c": (9.0, 0.4), "d": (8.0, 0.9), "e": (10.0, 0.4)}
    assert SE.nondominated(pts) == {"a", "b", "d"}
    assert SE.dominates((1, 1), (1, 0)) and not SE.dominates((1, 1), (1, 1))


# =========================================================================== 三口径


def test_cohort_three_rates_identity(small_run):
    db, rid, cfg = small_run
    w = SQLiteWriter(db)
    rates = CO.cohort_availability(w, rid)
    assert rates
    assert CO.check_identity(rates) == []
    for r in rates:
        assert r.n == r.active + r.churned
        if r.unknown == 0 and r.active:
            assert r.avail_rate_active == pytest.approx(r.active_clear / r.active)
            assert r.cohort_active_clear_share == pytest.approx(r.active_clear / r.n)
            assert r.avail_rate_counterfactual_frozen == pytest.approx((r.active_clear + r.frozen_clear) / r.n)
    # 首次获取期全员活跃
    for r in rates:
        if r.t_idx == r.acquisition_period:
            assert r.churned == 0
    w.close()


def test_cohort_unknown_makes_null(small_run, tmp_path):
    db, rid, _ = small_run
    copy = tmp_path / "c.sqlite"
    shutil.copy(db, copy)
    con = sqlite3.connect(copy)
    u, g, p = con.execute("SELECT unit_id, acquisition_period, player_id FROM fact_unit_cohort c WHERE EXISTS "
                          "(SELECT 1 FROM fact_player_roster_panel r WHERE r.unit_id=c.unit_id) LIMIT 1").fetchone()
    con.execute("UPDATE fact_player_roster_panel SET eval_status='error', is_full_star=NULL, clear_index=NULL "
                "WHERE player_id=? AND unit_id=? AND t_idx=? AND eval_phase='post'", (p, u, g))
    con.commit()
    con.close()
    w = SQLiteWriter(copy)
    r = next(x for x in CO.cohort_availability(w, rid) if x.unit_id == u and x.acquisition_period == g and x.t_idx == g)
    assert r.unknown >= 1 and r.avail_rate_active is None and r.cohort_active_clear_share is None
    assert r.unknown_share == pytest.approx(r.unknown / r.n)
    w.close()


def test_retention_and_cash_denominators(small_run):
    db, rid, cfg = small_run
    w = SQLiteWriter(db)
    rows = CO.retention_and_cash(w, rid)
    assert all(r["initial_players"] == N_SMALL for r in rows)
    cash = w.query("SELECT SUM(amount_fen) FROM fact_cash_event")[0][0]
    assert rows[-1]["cum_cash_fen"] == cash
    assert rows[-1]["cum_cash_per_initial_fen"] == pytest.approx(cash / N_SMALL)
    assert all(r["retention_end"] <= r["retention_start"] + 1e-12 for r in rows)
    rs = CO.revenue_structure(w, rid)
    for r in rs:
        if r["total_pulls"]:
            assert r["curr_free_share"] + r["hoarded_free_share"] + r["sub_share"] + r["topup_share"] == pytest.approx(1)
    obj = CO.run_objectives(w, rid, cfg.module("population")["core_retention_tier"])
    assert obj.total_revenue_fen == cash
    assert obj.core_retention_end is None or 0 <= obj.core_retention_end <= 1
    w.close()


# =========================================================================== 参考轨迹


def test_reference_trajectories(base):
    bid, world, obs = REF.evaluate_trajectories(base)
    plans = REF.dps_investment_plans(base)
    dps = [u for u in world.units.values() if u.b_ref is not None and u.unit_id != "starter_dps"]
    n_subjects = len(dps) * len(plans) * 2 * (1 + len(world.supports))
    assert len({o.subject.reference_subject_id for o in obs}) == n_subjects
    assert base.module("behavior")["support_target"] not in plans
    debut = {u.unit_id: u.debut_t for u in world.units.values()}
    model = ProxyCombatModel(base.module("combat"))
    resolve_world(base, model)
    for o in obs:
        s = o.subject
        assert o.t_idx >= s.debut_t
        if s.support_unit and o.t_idx < debut[s.support_unit]:
            assert o.support_active is None            # 支援出道前不可用
        if o.age_days == 0:
            assert o.gear == pytest.approx(1.0)
        if s.gear_policy == REF.GEAR_FIXED:
            assert o.gear == 1.0
    km = SV.km_by_group(obs, ("investment_plan", "gear_policy", "support_policy"))
    assert len(km) == len(plans) * 2 * (1 + len(world.supports))
    assert all(k.n_subjects == len(dps) for k in km)
    assert SV.cox_status()["status"] == "skipped"


# =========================================================================== 搜索协议


def test_full_protocol_counts(base):
    st = SE.plan_search(ENTRY)
    exp = base.module("experiments")
    meta = base.metadata["module_metadata"]["experiments"]
    n_c = len(exp["grid"]["mechanism_strength"]) * len(exp["grid"]["support_gain_scale"])
    n_p = 1 + len(exp["pain_sensitivity"]["parameters"]) * (len(exp["pain_sensitivity"]["factors"]) - 1)
    assert len(st.candidates) == n_c and len(st.perturbations) == n_p
    assert len(st.specs) == n_c * len(meta["train_seeds"]) + n_c * n_p * len(meta["validation_seeds"])
    assert st.scale_label == "full_protocol" and st.common_overrides == {}
    reduced = SE.plan_search(ENTRY, n_players=10)
    assert reduced.scale_label == "reduced_scale" and reduced.experiment_id != st.experiment_id


def test_candidate_overrides_scale_base_values(base):
    cands = SE.candidates(base)
    b = SE.baseline_candidate(base, cands)
    p0 = SE.perturbations(base)[0]
    ov = SE.run_overrides(base, b, p0, {})
    assert resolve_config(ENTRY, overrides=ov).config_id == base.config_id   # 基准点 = Ref_Mechanism 同一配置
    c = next(x for x in cands if x.lambda_value == 2 and x.support_gain_scale != 1)
    ov = SE.run_overrides(base, c, p0, {})
    for p in SE.LAMBDA_PARAMS:
        assert ov[p] == pytest.approx(base.get(p) * 2)
    assert ov[SE.A_PARAM] == pytest.approx(base.get(SE.A_PARAM) * c.support_gain_scale)
    pert = next(p for p in SE.perturbations(base) if p.parameter_name)
    ov2 = SE.run_overrides(base, b, pert, {})
    assert ov2[pert.parameter_name] == pytest.approx(base.get(pert.parameter_name) * pert.factor)
    assert resolve_config(ENTRY, overrides=ov2).config_id != base.config_id


def test_tiny_search_end_to_end(tmp_path, base):
    seeds = base.metadata["module_metadata"]["experiments"]
    st = SE.plan_search(ENTRY, n_players=2, train_seeds=seeds["train_seeds"][:2], validation_seeds=seeds["validation_seeds"][:1],
                        candidate_ids=["L1_A1", "L0_A0.8"], perturbation_ids=["P0", "gap_weight_x1.25"])
    res = SE.execute(st, tmp_path, workers=1)
    assert len(res) == len({(SE.run_overrides(base, s.candidate, s.perturbation, st.common_overrides).__repr__(), s.seed)
                            for s in st.specs})
    summary = SE.assemble(st, res, tmp_path / "search.sqlite")
    again = SE.assemble(st, SE.execute(st, tmp_path, workers=1), tmp_path / "search.sqlite")  # 复用 + 可重算
    assert again["unique_runs"] == summary["unique_runs"]
    con = sqlite3.connect(tmp_path / "search.sqlite")
    assert con.execute("SELECT COUNT(*) FROM agg_search_results").fetchone()[0] == len(st.specs)
    assert con.execute("SELECT COUNT(*) FROM search_run_map").fetchone()[0] == len(st.specs)
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("SELECT COUNT(*) FROM run_manifest WHERE status<>'completed'").fetchone()[0] == 0
    # 基准点相对自身差为 0
    d = con.execute("SELECT DISTINCT delta_revenue_vs_base, delta_retention_vs_base FROM agg_search_results "
                    "WHERE candidate_id='L1_A1'").fetchall()
    assert all(x[0] == 0 for x in d)
    freq = con.execute("SELECT inclusion_frequency, inclusion_count, n_seeds FROM agg_seed_robustness").fetchall()
    assert all(f == c / n for f, c, n in freq)
    assert con.execute("SELECT COUNT(*) FROM agg_rank_stability").fetchone()[0] == 2
    con.close()
