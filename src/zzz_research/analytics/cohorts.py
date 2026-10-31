"""M-STATS 11.1 / 11.4：固定获取队列三口径、留存与现金（单个 run 内）。

- 队列 C = 某角色首次获取期 g 的固定成员，不加入未来获取者。
- A=本期期初活跃；Y=本期 PostEval 达标；Y^F=冻结配置在本期环境下达标。
- 分母成员中存在该口径所需的未知结局（error/unsupported/缺行）时，比率为 NULL 而不是当 0。
- 现金与抽数先在 (run, player, t) 粒度汇总，再按 t 聚合；累计人均现金除以初始人数。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Optional

UNKNOWN_STATUSES = ("error", "unsupported", "missing_model")


def _ratio(num: float, den: float) -> Optional[float]:
    return None if den == 0 else num / den


@dataclass(frozen=True)
class CohortRate:
    run_id: str
    unit_id: str
    acquisition_period: int
    t_idx: int
    n: int
    active: int
    active_clear: int
    frozen_clear: int
    churned: int
    unknown: int
    avail_rate_active: Optional[float]
    cohort_active_clear_share: Optional[float]
    avail_rate_counterfactual_frozen: Optional[float]
    churned_holder_share: Optional[float]
    unknown_share: Optional[float]
    unknown_active: int = 0
    unknown_counterfactual: int = 0
    unknown_lifecycle: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def cohort_availability(writer: Any, run_id: str) -> list[CohortRate]:
    members = writer.query("SELECT unit_id, acquisition_period, player_id FROM fact_unit_cohort WHERE run_id=?",
                           (run_id,))
    if not members:
        return []
    lc = {(p, t): a for p, t, a in writer.query(
        "SELECT player_id, t_idx, active_start FROM fact_player_lifecycle WHERE run_id=?", (run_id,))}
    panel = {}
    for p, t, u, phase, star, status in writer.query(
            "SELECT player_id, t_idx, unit_id, eval_phase, is_full_star, eval_status "
            "FROM fact_player_roster_panel WHERE run_id=? AND eval_phase IN ('post','frozen_post')", (run_id,)):
        panel[(p, t, u, phase)] = (star, status)
    anchors = {u for (_, _, u, _) in panel}
    t_max = max([t for (_, t) in lc] + [t for (_, t, _, _) in panel])
    groups: dict[tuple[str, int], list[int]] = {}
    for u, g, p in members:
        if u in anchors:  # 支援无单独通关指标，不伪造
            groups.setdefault((u, g), []).append(p)
    out = []
    for (u, g), players in sorted(groups.items()):
        for t in range(g, t_max + 1):
            n = len(players)
            act = ay = fy = churned = unknown = 0
            unknown_active = unknown_counterfactual = unknown_lifecycle = 0
            for p in players:
                a = lc.get((p, t))
                if a is None:
                    unknown += 1
                    unknown_active += 1
                    unknown_counterfactual += 1
                    unknown_lifecycle += 1
                    continue
                if a == 1:
                    act += 1
                    star, st = panel.get((p, t, u, "post"), (None, None))
                    if star is None or st in UNKNOWN_STATUSES:
                        unknown += 1
                        unknown_active += 1
                        unknown_counterfactual += 1
                    else:
                        ay += star
                else:
                    churned += 1
                    star, st = panel.get((p, t, u, "frozen_post"), (None, None))
                    if star is None or st in UNKNOWN_STATUSES:
                        unknown += 1
                        unknown_counterfactual += 1
                    else:
                        fy += star
            out.append(CohortRate(
                run_id, u, g, t, n, act, ay, fy, churned, unknown,
                None if unknown_active else _ratio(ay, act),
                None if unknown_active else _ratio(ay, n),
                None if unknown_counterfactual else _ratio(ay + fy, n),
                None if unknown_lifecycle else _ratio(churned, n), _ratio(unknown, n),
                unknown_active, unknown_counterfactual, unknown_lifecycle))
    return out


def retention_and_cash(writer: Any, run_id: str, by_tier: bool = False) -> list[dict]:
    """每期 retention_start/end 与累计人均现金（分母=初始人数，不除以幸存者）。"""
    key = "player_tier" if by_tier else "'ALL'"
    rows = writer.query(f"""
        SELECT {key}, t_idx, SUM(active_start), SUM(active_end), COUNT(*),
               SUM(sub_revenue_fen), SUM(topup_revenue_fen)
        FROM agg_player_period WHERE run_id=? GROUP BY 1,2 ORDER BY 1,2""", (run_id,))
    out, cum = [], {}
    for grp, t, a0, a1, n0, sub, top in rows:
        cum[grp] = cum.get(grp, 0) + sub + top
        out.append({"run_id": run_id, "group": grp, "t_idx": t, "initial_players": n0,
                    "retention_start": _ratio(a0, n0), "retention_end": _ratio(a1, n0),
                    "period_cash_fen": sub + top, "cum_cash_fen": cum[grp],
                    "cum_cash_per_initial_fen": _ratio(cum[grp], n0)})
    return out


def revenue_structure(writer: Any, run_id: str) -> list[dict]:
    """每期两类现金与四来源抽数；占比分母为总消耗抽数，分母 0 记 NULL。"""
    rows = writer.query("""
        SELECT t_idx, SUM(sub_revenue_fen), SUM(topup_revenue_fen), SUM(total_pulls),
               SUM(curr_free_used), SUM(hoarded_free_used), SUM(sub_pulls_used), SUM(topup_pulls_used)
        FROM agg_player_period WHERE run_id=? GROUP BY t_idx ORDER BY t_idx""", (run_id,))
    out = []
    for t, sub, top, pulls, cf, hf, sp, tp in rows:
        out.append({"run_id": run_id, "t_idx": t, "sub_revenue_fen": sub, "topup_revenue_fen": top,
                    "total_pulls": pulls, "curr_free_used": cf, "hoarded_free_used": hf,
                    "sub_pulls_used": sp, "topup_pulls_used": tp,
                    "curr_free_share": _ratio(cf, pulls), "hoarded_free_share": _ratio(hf, pulls),
                    "sub_share": _ratio(sp, pulls), "topup_share": _ratio(tp, pulls)})
    return out


def budget_fatigue_cross(writer: Any, run_id: str) -> list[dict]:
    """活跃玩家-期按（期末是否满星 × 是否使用直购额度）交叉计数。

    用于解释预算使用率 b 进入疲劳的路径：F_after=0 计数表示该期疲劳被下界截断，
    预算项 w_b·b 对状态无边际作用。只读生命周期日志，不重算任何公式。
    """
    rows = writer.query("""
        SELECT COALESCE(is_full_star_post, -1) AS fs,
               CASE WHEN COALESCE(budget_utilization_rate, 0) > 0 THEN 1 ELSE 0 END AS used,
               COUNT(*), SUM(CASE WHEN F_after = 0 THEN 1 ELSE 0 END), SUM(churn_this_period),
               AVG(budget_utilization_rate), AVG(F_before), AVG(p_churn)
        FROM fact_player_lifecycle WHERE run_id=? AND active_start=1
        GROUP BY fs, used ORDER BY fs, used""", (run_id,))
    return [{"run_id": run_id, "full_star_post": fs, "budget_used": used, "player_periods": n,
             "fatigue_after_zero": f0, "churned": ch, "mean_budget_utilization": mb,
             "mean_fatigue_before": mf, "mean_p_churn": mp}
            for fs, used, n, f0, ch, mb, mf, mp in rows]


@dataclass(frozen=True)
class RunObjectives:
    run_id: str
    total_revenue_fen: int
    core_tier: str
    core_initial: int
    core_active_end_last: int
    core_retention_end: Optional[float]
    retention_end_all: Optional[float]


def run_objectives(writer: Any, run_id: str, core_tier: str) -> RunObjectives:
    """两个搜索目标：全观察期模拟累计现金（整数分）与期末核心预算层留存（人数比）。"""
    total = writer.query("SELECT COALESCE(SUM(sub_revenue_fen+topup_revenue_fen),0) FROM agg_player_period "
                         "WHERE run_id=?", (run_id,))[0][0]
    t_last = writer.query("SELECT MAX(t_idx) FROM agg_player_period WHERE run_id=?", (run_id,))[0][0]
    n_core, end_core = writer.query("SELECT COUNT(*), COALESCE(SUM(active_end),0) FROM agg_player_period "
                                    "WHERE run_id=? AND t_idx=? AND player_tier=?", (run_id, t_last, core_tier))[0]
    n_all, end_all = writer.query("SELECT COUNT(*), COALESCE(SUM(active_end),0) FROM agg_player_period "
                                  "WHERE run_id=? AND t_idx=?", (run_id, t_last))[0]
    return RunObjectives(run_id, int(total), core_tier, n_core, end_core, _ratio(end_core, n_core),
                         _ratio(end_all, n_all))


def check_identity(rates: list[CohortRate], tol: float = 1e-12) -> list[CohortRate]:
    """无未知时应满足 cohort_active_clear_share = avail_rate_active·(1−churned_holder_share)。返回违例行。"""
    bad = []
    for r in rates:
        if any(v is None for v in (r.avail_rate_active, r.cohort_active_clear_share,
                                    r.churned_holder_share)):
            continue
        if abs(r.cohort_active_clear_share - r.avail_rate_active * (1 - r.churned_holder_share)) > tol:
            bad.append(r)
    return bad
