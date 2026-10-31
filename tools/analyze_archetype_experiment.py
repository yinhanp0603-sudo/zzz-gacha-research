"""体系实验的全日志分析：A 组售后类型的分解与选择偏差（混合型按副输出份额 ρ 分档），B 组新体系换代锁与旧主流售后。

同一种子在各格共享初始人群与随机数流，效应先在种子内配对，再跨 3 个复核种子求均值与 t95 区间。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_v2_stability_stats import _estimate
from zzz_research.pipeline import from_project, write_csv

L_OLD = ("L01", "L03", "L05")
FOCAL = "L03"
PRE, POST, LATE = (5, 6), (7, 8, 9), (8, 9, 10, 11, 12)
B_WINDOW = (14, 15, 16)
PRODUCT = {"control": "P07", "narrow": "P07", "system": "P07"}


def _rows(con, sql, args=()):
    return con.execute(sql, args).fetchall()


def run_metrics(run: dict) -> dict:
    con = sqlite3.connect(from_project(run["database"]).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        config = json.loads(con.execute("SELECT config_json FROM run_manifest").fetchone()[0])
        core_tier = config["modules"]["population"]["core_retention_tier"]
        archetype = {e["unit_id"]: e["unit_id"][0] for e in config["modules"]["scenarios"]["events"]
                     if e["role"] != "support"}
        archetype["H07"] = "L"
        deployed = defaultdict(dict)
        for player, t, unit, support in _rows(con, """SELECT player_id,t_idx,unit_id,support_id FROM fact_player_roster_panel
                WHERE eval_phase='post' AND active_start=1 AND is_deployed_main=1"""):
            deployed[t][player] = (unit, support)
        active = defaultdict(set)
        tier, alive_end = {}, defaultdict(dict)
        cash = defaultdict(lambda: defaultdict(float))
        for player, t, ptier, a_start, a_end, sub, topup in _rows(con, """SELECT player_id,t_idx,player_tier,active_start,
                active_end,sub_revenue_fen,topup_revenue_fen FROM agg_player_period"""):
            tier[player] = ptier
            if a_start:
                active[t].add(player)
            alive_end[t][player] = a_end
            cash[t][player] = (sub + topup) / 100
        focal_ci = {p: ci for p, ci in _rows(con, """SELECT player_id,clear_index FROM fact_player_roster_panel
                WHERE eval_phase='post' AND active_start=1 AND unit_id=? AND t_idx=8""", (FOCAL,))}
        product = PRODUCT.get(run["product"], "H07")
        owners = {p for (p,) in _rows(con, """SELECT player_id FROM fact_ownership WHERE asset_kind='character'
                AND asset_id=? AND t_idx=8 AND copies>=1""", (product,))}
        topup_by_banner = defaultdict(float)
        for banner, fen in _rows(con, """SELECT b.banner_id, SUM(c.amount_fen) FROM fact_cash_event c
                JOIN fact_pull_event p ON p.run_id=c.run_id AND p.pull_event_id=c.pull_event_id
                JOIN dim_banner b ON b.banner_id=p.banner_id GROUP BY b.banner_id"""):
            topup_by_banner[banner] = (fen or 0) / 100
        allowed = defaultdict(lambda: [0, 0])
        for player, t, kind, ok in _rows(con, """SELECT player_id,t_idx,decision_kind,allowed FROM fact_spending_decision
                WHERE capacity>0"""):
            allowed[t, kind, player][0] += ok
            allowed[t, kind, player][1] += 1
    finally:
        con.close()

    def share(t, units):
        players = active[t]
        return sum(deployed[t].get(p, ("", ""))[0] in units for p in players) / len(players) if players else None

    out = {"cell_id": run["cell_id"], "block": run["block"], "product": run["product"], "env": run["env"],
           "lam": run["lam"], "lock": run["lock"], "postsale": run["postsale"], "co_output": run["co_output"],
           "seed": run["seed"]}
    for t in range(5, 17):
        out[f"deploy_L_old_t{t}"] = share(t, L_OLD)
    out["deploy_L_old_pre"] = st.mean(share(t, L_OLD) for t in PRE)
    out["deploy_L_old_post"] = st.mean(share(t, L_OLD) for t in POST)
    out["deploy_L_old_change_pp"] = 100 * (out["deploy_L_old_post"] - out["deploy_L_old_pre"])
    l_teams = [deployed[t][p] for t in LATE for p in active[t] if archetype.get(deployed[t].get(p, ("",))[0]) == "L"]
    for flex, key in ((product, "flex_share_product"), ("SUP_L04", "flex_share_old_support")):
        out[key] = sum(s == flex for _, s in l_teams) / len(l_teams) if l_teams else None
    out["product_deployed_as_main_t8_12"] = (sum(deployed[t].get(p, ("",))[0] == "H07" for t in LATE for p in active[t])
                                             / sum(len(active[t]) for t in LATE))
    with_h = [deployed[t][p] for t in LATE for p in active[t] if "H07" in deployed[t].get(p, ())]
    out["hybrid_flex_share_t8_12"] = sum(s == "H07" for _, s in with_h) / len(with_h) if with_h else None
    out["focal_ci_by_player"] = focal_ci
    out["product_owners"] = owners
    holders, others = [focal_ci[p] for p in focal_ci if p in owners], [focal_ci[p] for p in focal_ci if p not in owners]
    out["obs_focal_ci_ratio"] = st.median(holders) / st.median(others) if holders and others else None
    out["n_focal_owners"], out["n_focal_nonowners"] = len(holders), len(others)
    core = [p for p, t in tier.items() if t == core_tier]
    for t in (10, 20):
        out[f"retention_core_t{t}"] = sum(alive_end[t].get(p, 0) for p in core) / len(core)
    out["cash_total"] = sum(sum(v.values()) for v in cash.values())
    out["cash_product_banner"] = topup_by_banner.get("ARC_P07", 0.0)
    out["cash_new_main_banners_t8_13"] = sum(topup_by_banner.get(f"ARC_P{t:02d}", 0.0) for t in (8, 9, 11, 12, 13))
    loyal = [p for p in active[14] if archetype.get(deployed[13].get(p, ("",))[0]) == "L"]
    out["n_L_loyal_t13"] = len(loyal)
    out["L_loyal_retention_t20"] = sum(alive_end[20].get(p, 0) for p in loyal) / len(loyal) if loyal else None
    cells = [allowed[t, "topup", p] for t in B_WINDOW for p in loyal if allowed[t, "topup", p][1]]
    out["L_loyal_topup_allowed_t14_16"] = sum(a for a, _ in cells) / sum(n for _, n in cells) if cells else None
    out["L_loyal_cash_t14_20"] = sum(cash[t].get(p, 0) for t in range(14, 21) for p in loyal)
    out["deploy_L_t14_16"] = st.mean(share(t, {u for u, a in archetype.items() if a == "L"}) for t in B_WINDOW)
    out["deploy_N_t14_16"] = st.mean(share(t, {u for u, a in archetype.items() if a == "N"}) for t in B_WINDOW)
    out["cash_N_banners"] = sum(topup_by_banner.get(f"ARC_P{t:02d}", 0.0) for t in (14, 16, 17))
    out["cash_postsale_banner"] = topup_by_banner.get("ARC_P15", 0.0)
    return out


def paired(rows, key, pick_a, pick_b, scale=1.0):
    """每个种子内 a−b，再跨种子估计。"""
    seeds = sorted({r["seed"] for r in rows})
    lookup = {(r["cell_id"], r["seed"]): r for r in rows}
    values = [(lookup[pick_a, s][key] - lookup[pick_b, s][key]) * scale for s in seeds
              if lookup[pick_a, s][key] is not None and lookup[pick_b, s][key] is not None]
    e = _estimate(values)
    return {"mean": e["mean"], "ci95_low": e["low"], "ci95_high": e["high"], "n_seeds": e["n"]}


def summarize(rows):
    lookup = {(r["cell_id"], r["seed"]): r for r in rows}
    seeds = sorted({r["seed"] for r in rows})
    a_rows, selection, displacement, b_rows = [], [], [], []
    for lam in (1.0, 2.0):
        cid = lambda p, e: f"A_{p}_{e}_L{lam:g}"
        trend = _estimate([lookup[cid("control", "unfit"), s]["deploy_L_old_change_pp"] for s in seeds])
        env = paired(rows, "deploy_L_old_change_pp", cid("control", "fit"), cid("control", "unfit"))
        for product in ("narrow", "system", *sorted({r["product"] for r in rows if r["product"].startswith("hybrid")})):
            rho = next(r["co_output"] for r in rows if r["product"] == product)
            effects = {e: paired(rows, "deploy_L_old_change_pp", cid(product, e), cid("control", e)) for e in ("fit", "unfit")}
            interaction = _estimate([(lookup[cid(product, "fit"), s]["deploy_L_old_change_pp"] - lookup[cid("control", "fit"), s]["deploy_L_old_change_pp"])
                                     - (lookup[cid(product, "unfit"), s]["deploy_L_old_change_pp"] - lookup[cid("control", "unfit"), s]["deploy_L_old_change_pp"]) for s in seeds])
            naive = _estimate([lookup[cid(product, "fit"), s]["deploy_L_old_change_pp"] for s in seeds])
            retention = {e: paired(rows, "retention_core_t20", cid(product, e), cid("control", e), 100) for e in ("fit", "unfit")}
            cash = {e: paired(rows, "cash_total", cid(product, e), cid("control", e)) for e in ("fit", "unfit")}
            new_main = {e: paired(rows, "cash_new_main_banners_t8_13", cid(product, e), cid("control", e)) for e in ("fit", "unfit")}
            a_rows.append({"lam": lam, "product": product, "co_output": rho,
                           "naive_change_fit_pp": naive["mean"],
                           "trend_pp": trend["mean"], "env_effect_pp": env["mean"],
                           "product_effect_fit_pp": effects["fit"]["mean"], "product_effect_fit_low": effects["fit"]["ci95_low"],
                           "product_effect_fit_high": effects["fit"]["ci95_high"],
                           "product_effect_unfit_pp": effects["unfit"]["mean"], "product_effect_unfit_low": effects["unfit"]["ci95_low"],
                           "product_effect_unfit_high": effects["unfit"]["ci95_high"],
                           "interaction_pp": interaction["mean"],
                           "retention_core_t20_diff_fit_pp": retention["fit"]["mean"],
                           "retention_core_t20_diff_fit_low": retention["fit"]["ci95_low"],
                           "retention_core_t20_diff_fit_high": retention["fit"]["ci95_high"],
                           "retention_core_t20_diff_unfit_pp": retention["unfit"]["mean"],
                           "cash_total_diff_fit_yuan": cash["fit"]["mean"], "cash_total_diff_unfit_yuan": cash["unfit"]["mean"],
                           "new_main_banner_cash_diff_fit_yuan": new_main["fit"]["mean"],
                           "new_main_banner_cash_diff_unfit_yuan": new_main["unfit"]["mean"],
                           "decomposition": "naive(fit) = trend + env + product(fit); interaction = product(fit) − product(unfit)"})
            for e in ("fit", "unfit"):
                obs, exp = [], []
                for s in seeds:
                    treated, control = lookup[cid(product, e), s], lookup[cid("control", e), s]
                    if treated["obs_focal_ci_ratio"]:
                        obs.append(treated["obs_focal_ci_ratio"])
                    both = [treated["focal_ci_by_player"][p] / control["focal_ci_by_player"][p]
                            for p in treated["focal_ci_by_player"] if p in control["focal_ci_by_player"]
                            and p in treated["product_owners"] and control["focal_ci_by_player"][p] > 0]
                    if both:
                        exp.append(st.median(both))
                o, x = (_estimate(obs) if obs else None), (_estimate(exp) if exp else None)
                selection.append({"lam": lam, "product": product, "co_output": rho, "env": e,
                                  "observational_ratio": o and o["mean"], "experimental_ratio_owners": x and x["mean"],
                                  "selection_gap": (o["mean"] - x["mean"]) if o and x else None,
                                  "n_owners_mean": st.mean(lookup[cid(product, e), s]["n_focal_owners"] for s in seeds),
                                  "n_nonowners_mean": st.mean(lookup[cid(product, e), s]["n_focal_nonowners"] for s in seeds),
                                  "observational_definition": f"{FOCAL} CI at t8: median among product owners ÷ median among non-owners, same cell",
                                  "experimental_definition": f"{FOCAL} CI at t8 of the same product owners: product cell ÷ control cell, per-player median"})
            for e in ("fit", "unfit"):
                cell = [lookup[cid(product, e), s] for s in seeds]
                displacement.append({"lam": lam, "product": product, "co_output": rho, "env": e,
                                     "flex_share_product": st.mean(r["flex_share_product"] for r in cell),
                                     "flex_share_old_support": st.mean(r["flex_share_old_support"] for r in cell),
                                     "control_flex_share_old_support": st.mean(lookup[cid("control", e), s]["flex_share_old_support"] for s in seeds),
                                     "product_deployed_as_main": st.mean(r["product_deployed_as_main_t8_12"] for r in cell),
                                     "hybrid_flex_share": (st.mean(r["hybrid_flex_share_t8_12"] for r in cell)
                                                           if product.startswith("hybrid") else None),
                                     "scope": "L-archetype deployed teams, periods 8–12"})
    for key, label, scale in (("L_loyal_retention_t20", "L 体系玩家第 20 期留存（pp）", 100),
                              ("L_loyal_topup_allowed_t14_16", "L 体系玩家第 14–16 期直购许可率（pp）", 100),
                              ("L_loyal_cash_t14_20", "L 体系玩家第 14–20 期现金（元）", 1),
                              ("deploy_L_t14_16", "第 14–16 期 L 体系上场比例（pp）", 100),
                              ("deploy_N_t14_16", "第 14–16 期 N 体系上场比例（pp）", 100),
                              ("cash_N_banners", "N 体系卡池直购（元）", 1),
                              ("cash_postsale_banner", "第 15 期旧主流售后卡池直购（元）", 1),
                              ("cash_total", "20 期总现金（元）", 1)):
        lock = paired(rows, key, "B_lock_no", "B_none_no", scale)
        sale = paired(rows, key, "B_lock_yes", "B_lock_no", scale)
        sale_free = paired(rows, key, "B_none_yes", "B_none_no", scale)
        b_rows.append({"metric": key, "label": label,
                       "lock_effect": lock["mean"], "lock_low": lock["ci95_low"], "lock_high": lock["ci95_high"],
                       "postsale_effect_under_lock": sale["mean"], "postsale_low": sale["ci95_low"], "postsale_high": sale["ci95_high"],
                       "postsale_effect_without_lock": sale_free["mean"],
                       "recovered_share": (sale["mean"] / -lock["mean"])
                       if lock["ci95_low"] > 0 or lock["ci95_high"] < 0 else None})
    return a_rows, selection, displacement, b_rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / "outputs/research_v2_archetype")
    args = parser.parse_args(argv)
    manifest = json.loads((args.dir / "experiment_manifest.json").read_text("utf-8"))
    if manifest["status"] != "completed":
        raise ValueError("体系实验尚未完成")
    rows = [run_metrics(r) for r in manifest["runs"]]
    a_rows, selection, displacement, b_rows = summarize(rows)
    tables = args.dir / "tables"
    write_csv(tables / "run_metrics.csv", [{k: v for k, v in r.items() if k not in ("focal_ci_by_player", "product_owners")} for r in rows])
    write_csv(tables / "a_decomposition.csv", a_rows)
    write_csv(tables / "a_selection_bias.csv", selection)
    write_csv(tables / "a_displacement.csv", displacement)
    write_csv(tables / "b_generation_shift.csv", b_rows)
    print(json.dumps({"runs": len(rows), "tables": ["run_metrics", "a_decomposition", "a_selection_bias", "a_displacement",
                                                    "b_generation_shift"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
