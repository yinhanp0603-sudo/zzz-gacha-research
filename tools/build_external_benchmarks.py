"""External market and pull-statistics benchmarks for the simulated revenue and pull structure.

Reads the stored tracker / rng.moe snapshots in data/external, the published N=1000 period table and
the two full-log L1_A1 runs; writes comparison tables into the release directory. No simulation is run.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "data/external/gacha_tracker_zzz_2026-10-31.json"
RNGMOE = ROOT / "data/external/rngmoe_zzz_global_2026-10-31.json"
FULL_LOG_RUNS = sorted((ROOT / "outputs/research_v2/runs/h20/L1_A1__combat-v2__spending-v2").glob("Research_seed*.sqlite"))
USER_UNITS = ROOT / "tools/user_supplied_units.json"
# 专拐池 → 参照主C池与配对规则；专拐类别取 empirical_support_archetypes.csv 的数据分类
PAIRS = (("Lucia", "Yixuan", "同体系头部主C首发池"), ("Ju Fufu", "Yixuan", "同体系头部主C首发池"),
         ("Yuzuha", "Miyabi&Harumasa", "同体系头部主C池"), ("Nangong Yu", "Aria", "同体系前一个主C池"),
         ("Sunna", "Ye Shunguang&Zhao", "主要搭配主C首发池"), ("Trigger", "Soldier 0 - Anby", "主要搭配主C首发池"),
         ("Caesar", "Jane", "前一个限定主C池"), ("Astra Yao", "Miyabi&Harumasa", "前一个限定主C池"))
ZH = {"Caesar": "凯撒", "Jane": "简", "Astra Yao": "耀嘉音", "Miyabi&Harumasa": "星见雅＆浅羽悠真", "Yuzuha": "浮波柚叶",
      "Yixuan": "仪玄", "Ju Fufu": "橘福福", "Lucia": "卢西娅", "Sunna": "千夏", "Ye Shunguang&Zhao": "叶瞬光＆照",
      "Trigger": "「扳机」", "Soldier 0 - Anby": "零号·安比", "Nangong Yu": "南宫羽", "Aria": "爱芮"}
SYSTEM_CATEGORIES = ("体系专拐", "单核专拐")


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
        writer.writeheader()
        writer.writerows(rows)


def characters():
    data = json.loads((ROOT / "data/raw/SRC_SHIYU_meta_d930cec50bfd/characters.json").read_text("utf-8"))
    return data, {v["id"]: (k, v["role"]) for k, v in data.items()}


def banner_table(meta):
    snapshot = json.loads(TRACKER.read_text("utf-8"))
    supplied = json.loads(USER_UNITS.read_text("utf-8"))["units"]
    rows = [dict(zip(snapshot["fields"], r)) for r in snapshot["rows"]]
    floors = defaultdict(list)
    for r in rows:
        names = [r["agents"]] if r["agents"] == "Orphie & Magus" else [a.strip() for a in r["agents"].split("&")]
        roles = [meta.get(n, {}).get("role") for n in names]
        days = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
        r.update(role="support" if roles == ["support"] else "main_dps" if "dps" in roles else "other",
                 days=days, jp_revenue_jpy=round(r["jp_revenue_oku_g"] * 1e8), jp_jpy_per_day=r["jp_revenue_oku_g"] * 1e8 / days,
                 cn_usd_per_day=r["cn_iphone_usd_qimai"] / days, year=r["start"][:4],
                 ongoing=int(r["end"] > snapshot["tracker_updated"][:10]),
                 unit_note="；".join(f"{supplied[n]['name_zh']}：{supplied[n]['note']}（{supplied[n]['source']}）"
                                    for n in names if n in supplied))
        if not r["rerun"]:
            floors[r["year"]].append(r["jp_jpy_per_day"])
    for r in rows:
        r["jp_year_floor_per_day"] = min(floors[r["year"]])
    return rows, snapshot


def pair_rows(banners, out):
    by_agents = {r["agents"]: r for r in banners if not r["rerun"]}
    with (out / "empirical_support_archetypes.csv").open(encoding="utf-8-sig", newline="") as stream:
        archetypes = {r["unit"]: r for r in csv.DictReader(stream)}
    output = []
    for support, main, rule in PAIRS:
        s, m = by_agents[support], by_agents[main]
        floor = max(s["jp_year_floor_per_day"], m["jp_year_floor_per_day"])
        kind = archetypes[support]
        output.append({"source_id": "SRC_REVENUE_TRACKER", "category": kind["category"],
                       "family_groups": kind["family_groups"], "n_strong_cores": kind["n_strong_cores"],
                       "support_banner": support, "support_zh": ZH[support], "reference_banner": main, "reference_zh": ZH[main],
                       "pairing_rule": rule,
                       "support_jpy": s["jp_revenue_jpy"], "reference_jpy": m["jp_revenue_jpy"],
                       "ratio": s["jp_revenue_jpy"] / m["jp_revenue_jpy"],
                       "jp_per_day_ratio": s["jp_jpy_per_day"] / m["jp_jpy_per_day"],
                       "cn_iphone_per_day_ratio": s["cn_usd_per_day"] / m["cn_usd_per_day"],
                       "jp_floor_adjusted_ratio": (s["jp_jpy_per_day"] - floor) / (m["jp_jpy_per_day"] - floor),
                       "support_cn_best_rank": s["cn_ios_best_rank_intraday"], "reference_cn_best_rank": m["cn_ios_best_rank_intraday"],
                       "support_jp_best_rank": s["jp_best_rank"], "reference_jp_best_rank": m["jp_best_rank"],
                       "floor_definition": "当年非复刻卡池中最低的日均流水，作为月卡与常驻消费地板的保守估计",
                       "category_source": "empirical_support_archetypes.csv（主核搭配率规则分类）",
                       "scope": "category-level relative magnitude only; not a per-player causal estimate"})
    return output


def rngmoe_tables(by_game_id):
    snapshot = json.loads(RNGMOE.read_text("utf-8"))
    fields = snapshot["banner_fields"]
    rows = [dict(zip(fields, r)) for r in snapshot["banners"]]
    weapons = {r["start_unix"]: r for r in rows if r["gacha_type"] == 3001}
    banners = []
    for r in rows:
        if r["gacha_type"] != 2001:
            continue
        ids = r["rateup_s_game_ids"].split("|")
        name, role = by_game_id[ids[0]]
        weapon = weapons.get(r["start_unix"])
        banners.append({**r, "featured_unit": name, "featured_role": role, "co_featured_rerun": len(ids) > 1,
                        "start_date": datetime.fromtimestamp(r["start_unix"], timezone.utc).date().isoformat(),
                        "pulls_per_user": r["total_pulls"] / r["users"],
                        "win_rate_5": r["count_win_5"] / (r["count_win_5"] + r["count_lose_5"]),
                        "w_engine_pulls": weapon and weapon["total_pulls"], "w_engine_users": weapon and weapon["users"],
                        "w_engine_to_character_pulls": weapon and weapon["total_pulls"] / r["total_pulls"],
                        "w_engine_users_share": weapon and weapon["users"] / r["users"],
                        "w_engine_win_rate_5": weapon and weapon["count_win_5"] / (weapon["count_win_5"] + weapon["count_lose_5"])})
    curve = []
    for pool, key, base, soft in (("character", "character_2001", .006, 74), ("w_engine", "w_engine_3001", .01, 65)):
        for roll, pulls, hits in snapshot["pity_by_roll"][key]:
            curve.append({"pool": pool, "roll": roll, "pulls": pulls, "s_hits": hits, "observed_chance": hits / pulls,
                          "simulated_chance": min(1.0, base + .06 * max(0, roll - soft + 1))})
    return banners, curve, snapshot


def simulated_pulls():
    """Full-log baseline runs (λ=1, a=1, N=200): pull mix by banner role and pool."""
    totals = defaultdict(lambda: defaultdict(int))
    pair_ratio, weapon_ratio = [], []
    for db in FULL_LOG_RUNS:
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
            rows = con.execute("""SELECT u.role, p.t_idx, p.pool_kind, COUNT(*), SUM(p.is_S), SUM(p.is_UP),
                SUM(CASE WHEN p.guaranteed_before=0 AND p.is_S=1 THEN 1 ELSE 0 END),
                SUM(CASE WHEN p.guaranteed_before=0 AND p.is_UP=1 THEN 1 ELSE 0 END)
                FROM fact_pull_event p JOIN dim_banner b ON b.banner_id=p.banner_id
                JOIN dim_unit u ON u.unit_id=b.target_unit_id AND u.catalog_id=b.catalog_id
                GROUP BY 1,2,3""").fetchall()
        per = defaultdict(lambda: defaultdict(int))
        for role, t, pool, n, s, up, s_open, up_open in rows:
            kind = "support" if role == "support" else "main"
            for key, value in (("pulls", n), ("s", s), ("s_open", s_open), ("up_open", up_open)):
                totals[kind, pool][key] += value
            per[t][kind, pool] += n
        roles = {t: next(k for k, _ in v) for t, v in per.items()}
        for t in sorted(per):
            if roles[t] == "support":
                previous = max(u for u in per if u < t and roles[u] == "main")
                pair_ratio.append(per[t]["support", "character"] / per[previous]["main", "character"])
            elif per[t]["main", "character"]:
                weapon_ratio.append(per[t]["main", "weapon"] / per[t]["main", "character"])
    return totals, pair_ratio, weapon_ratio


def simulated_cash(period_path):
    groups = defaultdict(lambda: defaultdict(list))
    with period_path.open(encoding="utf-8-sig", newline="") as stream:
        for r in csv.DictReader(stream):
            if r["split"] == "validation" and r["horizon"] == "20":
                groups[r["policy_id"]][int(r["t_idx"])].append(r)
    mean = lambda rows, key: st.mean(float(r[key]) for r in rows)
    topup, total, floor, share = [], [], [], []
    for periods in groups.values():
        role = {t: rows[0]["banner_role"] for t, rows in periods.items()}
        for t in periods:
            if role[t] == "support":
                previous = max(u for u in periods if u < t and role[u] != "support")
                topup.append(mean(periods[t], "topup_yuan") / mean(periods[previous], "topup_yuan"))
                total.append(mean(periods[t], "cash_yuan") / mean(periods[previous], "cash_yuan"))
            else:
                floor.append(mean(periods[t], "subscription_yuan") / mean(periods[t], "cash_yuan"))
        share.append(sum(mean(r, "subscription_yuan") for r in periods.values()) / sum(mean(r, "cash_yuan") for r in periods.values()))
    return {"topup": topup, "total": total, "floor": floor, "subscription_share": share, "candidates": len(groups)}


def span(values, pct=True):
    fmt = (lambda v: f"{v:.0%}") if pct else (lambda v: f"{v:.2f}")
    return f"{fmt(min(values))}–{fmt(max(values))}（中位 {fmt(st.median(values))}）" if len(values) > 1 else fmt(values[0])


def compare(real, simulated, tolerance=.15):
    """Sim median against the real interquartile range (≥4 values, else full range) or relative gap (scalars)."""
    if isinstance(real, list):
        centre = st.median(simulated)
        low, _, high = st.quantiles(real, n=4, method="inclusive") if len(real) >= 4 else (min(real), None, max(real))
        return "一致" if low <= centre <= high else ("模拟偏高" if centre > high else "模拟偏低")
    gap = simulated / real - 1
    return "一致" if abs(gap) <= tolerance else ("模拟偏高" if gap > 0 else "模拟偏低")


def benchmark_rows(pairs, banners, curve, sim_pulls, sim_cash):
    totals, sim_pair, sim_weapon = sim_pulls
    bound = [p for p in pairs if p["category"] in SYSTEM_CATEGORIES]
    generic = [p for p in pairs if p["category"] == "泛用"]
    headline = [p["jp_per_day_ratio"] for p in pairs if p["reference_banner"] in ("Yixuan", "Ye Shunguang&Zhao")]
    character = [c for c in curve if c["pool"] == "character"]
    weapon = [c for c in curve if c["pool"] == "w_engine"]
    rate = lambda rows, lo, hi: sum(c["s_hits"] for c in rows if lo <= c["roll"] < hi) / sum(c["pulls"] for c in rows if lo <= c["roll"] < hi)
    single = [b for b in banners if not b["co_featured_rerun"]]
    single_weapon = [b["w_engine_win_rate_5"] for b in single if b["w_engine_win_rate_5"] is not None]
    main = [b["w_engine_to_character_pulls"] for b in banners if b["featured_role"] == "dps" and b["w_engine_to_character_pulls"]]
    by_unit = {b["featured_unit"]: b for b in banners}
    bound_pull = [by_unit[s]["total_pulls"] / by_unit[m]["total_pulls"]
                  for s, m in (("Lucia", "Yixuan"), ("Ju Fufu", "Yixuan"), ("Yuzuha", "Miyabi"))]
    generic_pull = [by_unit[s]["total_pulls"] / by_unit[m]["total_pulls"]
                    for s, m in (("Caesar", "Jane"), ("Astra Yao", "Miyabi"))]
    char_s = totals["main", "character"]["s"] + totals["support", "character"]["s"]
    char_pulls = totals["main", "character"]["pulls"] + totals["support", "character"]["pulls"]
    char_up = totals["main", "character"]["up_open"] + totals["support", "character"]["up_open"]
    char_open = totals["main", "character"]["s_open"] + totals["support", "character"]["s_open"]
    sim_w = totals["main", "weapon"]
    real_rate, real_base, real_w_base = rate(character, 1, 91), rate(character, 1, 74), rate(weapon, 1, 65)
    real_win = sum(b["count_win_5"] for b in single) / sum(b["count_win_5"] + b["count_lose_5"] for b in single)
    real_w_win = st.mean(single_weapon)
    soft = [(c["observed_chance"], c["simulated_chance"]) for c in character if 74 <= c["roll"] <= 78]
    bound_day = [p["jp_per_day_ratio"] for p in bound]
    bound_cn = [p["cn_iphone_per_day_ratio"] for p in bound]
    bound_floor = [p["jp_floor_adjusted_ratio"] for p in bound]
    rows = [
        ("pull", "角色池 5★ 综合出率（每抽）", f"{real_rate:.2%}", f"{char_s / char_pulls:.2%}", compare(real_rate, char_s / char_pulls),
         "rng.moe 全部限定角色池合并；模拟为两次全日志基准运行"),
        ("pull", "角色池基础出率／软保底起点", f"{real_base:.2%}／第 74 抽", "0.60%／第 74 抽", compare(real_base, .006),
         "第 74–78 抽观测出率 " + "、".join(f"{o:.1%}" for o, _ in soft) + "；模型 " + "、".join(f"{m:.1%}" for _, m in soft)),
        ("pull", "音擎池基础出率／软保底起点", f"{real_w_base:.2%}／第 65 抽", "1.00%／第 65 抽", compare(real_w_base, .01), ""),
        ("pull", "角色池小保底胜率（单 UP 池）", f"{real_win:.1%}", f"{char_up / char_open:.1%}", compare(real_win, char_up / char_open),
         "1.5 起双 UP 合并统计的池不计入；模拟为非大保底状态下出 S 的 UP 比例"),
        ("pull", "音擎池 UP 胜率（单 UP 池）", f"{real_w_win:.1%}", f"{sim_w['up_open'] / sim_w['s_open']:.1%}",
         compare(real_w_win, sim_w["up_open"] / sim_w["s_open"]), "单 UP 音擎池逐池胜率的算术均值"),
        ("pull", "主C池 音擎抽数 ÷ 角色抽数", span(main, pct=False), span(sim_weapon, pct=False), compare(main, sim_weapon),
         f"真实 {len(main)} 个主C池；模拟各档目标路径均含专武"),
        ("pull", "体系专拐池 ÷ 同体系头部主C池 角色抽数", span(bound_pull), span(sim_pair), compare(bound_pull, sim_pair),
         "卢西娅、橘福福 vs 仪玄；浮波柚叶 vs 星见雅；模拟为专拐期 ÷ 前一主C期"),
        ("pull", "泛用辅助池 ÷ 前一主C池 角色抽数", span(generic_pull), span(sim_pair), compare(generic_pull, sim_pair),
         "凯撒 vs 简；耀嘉音 vs 星见雅；模拟只有一类支援商品"),
        ("revenue", "体系专拐 ÷ 主C 日均流水（日本 game-i）", span(bound_day), span(sim_cash["total"]),
         compare(bound_day, sim_cash["total"]), "模拟为专拐期 ÷ 前一主C期的总现金（含月卡续订）"),
        ("revenue", "体系专拐 ÷ 主C 日均流水（中国 iPhone 七麦）", span(bound_cn), span(sim_cash["total"]),
         compare(bound_cn, sim_cash["total"]), "七麦日度总额按卡池窗口加总"),
        ("revenue", "体系专拐 ÷ 主C 扣除地板后的增量", span(bound_floor), span(sim_cash["topup"]),
         compare(bound_floor, sim_cash["topup"]), "真实扣除当年最低日均卡池；模拟只取直购"),
        ("revenue", "泛用辅助 ÷ 主C 扣除地板后的增量", span([p["jp_floor_adjusted_ratio"] for p in generic]), span(sim_cash["topup"]),
         compare([p["jp_floor_adjusted_ratio"] for p in generic], sim_cash["topup"]), "模拟只有一类支援商品"),
        ("revenue", "头部主C期现金中的月卡／常驻地板占比", f"≤ {max(headline):.0%}", span(sim_cash["floor"]),
         compare([0, max(headline)], sim_cash["floor"]),
         "同期专拐池的日均流水是头部主C期（仪玄、叶瞬光＆照）地板的上界；模拟为主C期月卡续订 ÷ 总现金"),
        ("revenue", "20 期累计现金中的月卡续订占比", "—", span(sim_cash["subscription_share"]), "无直接真实对照",
         f"{sim_cash['candidates']} 个候选的 20 期复核均值"),
    ]
    return [{"domain": d, "metric": m, "real": r, "simulated": s, "verdict": v, "note": n} for d, m, r, s, v, n in rows]


def build(out):
    meta, by_game_id = characters()
    banners, tracker = banner_table(meta)
    pairs = pair_rows(banners, out)
    pull_banners, curve, rng = rngmoe_tables(by_game_id)
    sim_cash = simulated_cash(out / "research/n1000/tables/period_outcomes.csv")
    sim_pulls = simulated_pulls()
    write_csv(out / "external_gacha_banner_revenue.csv",
              [{"source_id": "SRC_REVENUE_TRACKER", **r, "retrieved_at": tracker["retrieved_at"]} for r in banners])
    write_csv(out / "external_banner_category_ratios.csv", pairs)
    write_csv(out / "external_rngmoe_banner_stats.csv", [{"source_id": "SRC_RNGMOE", **r} for r in pull_banners])
    write_csv(out / "external_rngmoe_pity_curve.csv", [{"source_id": "SRC_RNGMOE", **r} for r in curve])
    rows = benchmark_rows(pairs, pull_banners, curve, sim_pulls, sim_cash)
    write_csv(out / "external_sim_benchmarks.csv", rows)
    stats_day = datetime.fromtimestamp(rng["stats_updated_at"], timezone.utc).date().isoformat()
    print(json.dumps({"banners": len(banners), "pairs": len(pairs), "rngmoe_banners": len(pull_banners),
                      "rngmoe_stats_updated": stats_day, "full_log_runs": [p.name for p in FULL_LOG_RUNS],
                      "benchmarks": [(r["metric"], r["real"], r["simulated"], r["verdict"]) for r in rows]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / "outputs/final_release")
    build(parser.parse_args().dir.resolve())
