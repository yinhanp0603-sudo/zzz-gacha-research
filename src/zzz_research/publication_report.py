"""Integrated publication from completed observations and paired V2 experiments."""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from .report import line_chart


PROXY_V2_NOTE = (
    "Layer 2/3 采用防御、有效输出窗口、侵蚀壁垒三个通道的参数化代理。"
    "贯穿只跳过防御区；角色与机制的适配度、支援净增益及适配修复分别登记。"
    "真实 Boss 的条件成绩用于选择情景锚点，模型未逐技能复现战斗。"
)


def md_table(headers, rows):
    clean = lambda x: str(x).replace("|", r"\|").replace("\n", " ")
    return "\n".join([
        "| " + " | ".join(map(clean, headers)) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(map(clean, row)) + " |" for row in rows),
    ]) + "\n"


def _base(rows):
    return [r for r in rows if r["policy_id"] == "L1_A1"
            and r.get("combat_variant", "v2") == "v2" and int(r["horizon"]) == 20]


def _truth(value):
    return value is True or value == "True" or value == 1


def survival_steps(rows):
    """Kaplan–Meier steps for fixed reference subjects, retaining right censoring."""
    times = sorted({float(r["first_failure_or_censor_days"]) for r in rows})
    survival = 1.0
    steps = [(0.0, survival)]
    for age in times:
        risk = sum(float(r["first_failure_or_censor_days"]) >= age for r in rows)
        failures = sum(float(r["first_failure_or_censor_days"]) == age
                       and _truth(r["first_failure_event"]) for r in rows)
        if failures:
            survival *= 1 - failures / risk
        steps.append((age, survival))
    return steps


def survival_median(rows):
    return next((int(t) for t, s in survival_steps(rows) if s <= .5), None)


def registered_early_pairs(data):
    """First-cycle registered pairs, including zero effects and later recoveries."""
    registered = data["combat_pairs"]
    return sorted([
        r for r in _base(data["tables"]["fixed"]["l2_support_pair_effects"])
        if r["anchor_unit_id"] in ("DPS_01", "DPS_02", "DPS_03")
        and r["investment_plan"] == "0+1" and r["gear_policy"] == "growth_from_debut"
        and r["support_policy"].split(":")[-1] in registered.get(r["anchor_unit_id"], {})
    ], key=lambda r: (r["anchor_unit_id"], r["support_policy"]))


def make_l2_figures(out, data):
    out = Path(out)
    dest = out / "report/figures"
    dest.mkdir(parents=True, exist_ok=True)
    lifetimes = _base(data["tables"]["fixed"]["l2_reference_lifetimes"])
    series = {}
    for investment in ("0+0", "0+1", "2+1"):
        rows = [r for r in lifetimes if r["investment_plan"] == investment
                and r["gear_policy"] == "growth_from_debut" and r["support_policy"] == "none"]
        series[f"{investment}｜持续刷盘"] = [(x, 100 * y) for x, y in survival_steps(rows)]
    names = {}
    name = "v2_l2_natural_km.svg"
    (dest / name).write_text(line_chart(
        series, title="模拟｜无专拐时，投资能保住多久？", subtitle="λ=1、a=1；16 名固定参考主 C；首次 CI<1 为事件，期末未失效右删失",
        xlabel="从主 C 登场起的角色年龄（天）", ylabel="尚未首次失效（%）", ymin=0, ymax=100,
        step=True, hlines=[(50, "中位数线", "5,4")], w=800, h=400), encoding="utf-8")
    names[name] = dest / name
    panel = [r for r in _base(data["tables"]["fixed"]["l2_reference_panel"])
             if r["anchor_unit_id"] == "DPS_03" and r["investment_plan"] == "0+1"
             and r["gear_policy"] == "growth_from_debut"]
    series = {}
    for policy, label in (("none", "无限定支援"), ("join_on_debut:S_A", "早期支援 S_A"),
                          ("join_on_debut:S_B", "后期支援 S_B")):
        series[label] = [(float(r["age_days"]), float(r["clear_index"])) for r in panel
                         if r["support_policy"] == policy]
    name = "v2_l2_support_recovery.svg"
    (dest / name).write_text(line_chart(
        series, title="模拟｜早买延迟失效，晚买可以恢复可用", subtitle="固定主 C DPS_03、0+1、持续刷盘；后期支援不会改写已经发生的首次失效",
        xlabel="从主 C 登场起的角色年龄（天）", ylabel="通关指数 CI", ymin=0,
        hlines=[(1, "满星线 1.00", ""), (.95, "舒适线 0.95", "5,4")], w=800, h=400), encoding="utf-8")
    names[name] = dest / name
    return names


def _period_means(rows, policy, metric):
    by_period = defaultdict(list)
    for r in rows:
        if r["policy_id"] == policy and r["split"] == "validation" and int(r["horizon"]) == 20:
            by_period[int(r["t_idx"])].append(float(r[metric]))
    return [(t, mean(values)) for t, values in sorted(by_period.items())]


def make_behavior_figures(out, data):
    dest = Path(out) / "report/figures"
    dest.mkdir(parents=True, exist_ok=True)
    rows = data["tables"]["n1000"]["period_outcomes"]
    series = {label: [(t, 100 * y) for t, y in _period_means(rows, policy, "retention_core")]
              for policy, label in (("L1_A1", "基准 λ1 / a1"), ("L2_A1", "高压 λ2 / a1"),
                                    ("L0.5_A1.6", "温和锁 / 强专拐"))}
    name = "v2_retention.svg"
    (dest / name).write_text(line_chart(
        series, title="模拟｜机制压力在中期与长期怎样影响留存？", subtitle="每次 1000 人，3 组独立复核种子等权平均；分母固定为初始核心付费层（Budget_40，月卡及中氪主力群）",
        xlabel="物理期数（每期 42 天）", ylabel="核心层留存（%）", ymin=0, ymax=100, w=800, h=400), encoding="utf-8")
    names = {name: dest / name}
    series = {label: [(t, y / 10000) for t, y in _period_means(rows, "L1_A1", metric)]
              for metric, label in (("cumulative_new_main_topup_yuan", "新主 C 池直购"),
                                    ("cumulative_support_topup_yuan", "专拐池直购"),
                                    ("cumulative_subscription_yuan", "月卡续订"))}
    name = "v2_cash_structure.svg"
    (dest / name).write_text(line_chart(
        series, title="模拟｜基准情景的三类现金从哪里来？", subtitle="λ=1、a=1；3 组复核种子均值；现金按实际付款计入，囤抽消耗不重复计收",
        xlabel="物理期数（每期 42 天）", ylabel="累计现金（万元）", ymin=0, w=800, h=400), encoding="utf-8")
    names[name] = dest / name
    return names


def _interval(value, low, high, suffix="", digits=2):
    if value is None:
        return "未估计"
    fmt = lambda x: f"{float(x):+,.{digits}f}"
    return f"{fmt(value)}{suffix} [{fmt(low)}, {fmt(high)}]{suffix}"


def _axis_rows(data):
    rows = data["tables"]["n1000"]["axis_effects"]
    pairs = {}
    for row in rows:
        if row["contrast_kind"] != "endpoint" or row["metric"] not in ("cash_yuan", "retention_core"):
            continue
        key = (int(row["horizon"]), row["split"], row["axis"])
        pairs.setdefault(key, {})[row["metric"]] = row
    output = []
    for key in sorted(pairs, key=lambda x: (x[0], x[1] != "train", x[2])):
        horizon, split, axis = key
        cash, retention = pairs[key]["cash_yuan"], pairs[key]["retention_core"]
        output.append((horizon, "筛选" if split == "train" else "复核", "λ 0→2" if axis == "mechanism_strength" else "a 0.8→1.6",
                       cash["n_pairs"], _interval(cash["mean_relative_pct"], cash["relative_ci95_low"], cash["relative_ci95_high"], "%"),
                       _interval(retention["mean_diff"], retention["ci95_low"], retention["ci95_high"], "pp")))
    return output


def _cash_rows(data):
    rows = data["tables"]["n1000"]["axis_cash_decomposition"]
    by_metric = {r["metric"]: r for r in rows if int(r["horizon"]) == 20 and r["split"] == "validation"
                 and r["axis"] == "support_gain_scale" and r["contrast_kind"] == "endpoint"}
    output = []
    for metric, label in (("new_main_topup_yuan", "新主 C 池直购"), ("support_topup_yuan", "专拐池直购"),
                          ("subscription_yuan", "月卡续订"), ("cash_yuan", "总现金")):
        r = by_metric[metric]
        output.append((label, r["n_pairs"], _interval(r["mean_diff_yuan"], r["ci95_low_yuan"], r["ci95_high_yuan"], "", 0),
                       _interval(r["mean_contribution_pp"], r["contribution_ci95_low_pp"], r["contribution_ci95_high_pp"])))
    return output


CASH_COMPONENT_LABELS = (("new_main_topup_yuan", "新主 C 池直购"), ("support_topup_yuan", "专拐池直购"),
                         ("subscription_yuan", "月卡续订"))


def _support_cash_components(data):
    """20 期复核 a 端点对照的三类现金：按点估计方向分组，并列出金额区间跨零的分项。"""
    rows = {r["metric"]: r for r in data["tables"]["n1000"]["axis_cash_decomposition"]
            if int(r["horizon"]) == 20 and r["split"] == "validation"
            and r["axis"] == "support_gain_scale" and r["contrast_kind"] == "endpoint"}
    decreased = [label for metric, label in CASH_COMPONENT_LABELS if float(rows[metric]["mean_diff_yuan"]) < 0]
    increased = [label for metric, label in CASH_COMPONENT_LABELS if float(rows[metric]["mean_diff_yuan"]) > 0]
    crossing = [label for metric, label in CASH_COMPONENT_LABELS
                if float(rows[metric]["ci95_low_yuan"]) <= 0 <= float(rows[metric]["ci95_high_yuan"])]
    return decreased, increased, crossing


def _direction_sentence(decreased, increased):
    parts = ([f"{'、'.join(decreased)}减少"] if decreased else []) + ([f"{'与'.join(increased)}增加"] if increased else [])
    return f"点估计中，{'，'.join(parts)}。" if parts else ""


def _crossing_sentence(crossing):
    if not crossing:
        return "绝对金额口径下，三项现金差的区间均不跨零。"
    scope = "均" if len(crossing) > 1 else ""
    return f"绝对金额口径下，{'与'.join(crossing)}的区间{scope}跨零，不能单凭点估计认定现实替代关系。"


SUPPORT_ANCHOR_MAINS = (("Yixuan", "仪玄"), ("Miyabi", "星见雅"), ("Remielle", "蕾米埃尔"), ("Seed", "席德"))


def _support_anchor_range(out):
    """危局普通路、固定第三人匹配的 0+1 强绑定例：四名主 C 的匹配成绩比几何均值区间。"""
    with (Path(out) / "empirical_knob2_support_lift.csv").open(encoding="utf-8-sig", newline="") as handle:
        ratios = [float(r["matched_score_ratio_geomean"]) for r in csv.DictReader(handle)
                  if r["scope"] == "matched_summary" and r["comparison_design"] == "fixed_third_matched"
                  and r["mode"] == "deadly_assault" and r["stage_scope"] == "normal"
                  and r["investment_label"] == "0+1" and r["matched_score_ratio_geomean"]
                  and r["main_unit"] in dict(SUPPORT_ANCHOR_MAINS)]
    return min(ratios), max(ratios)


DISPLAY_ZH = {"雅": "星见雅", "柚叶": "浮波柚叶"}
MODE_ZH = {"shiyu_defense": "式舆防卫战", "deadly_assault": "危局强袭战"}
GENERATIONS = ("G1", "G2", "G3", "G4", "G5")
EVIDENCE_LEVELS = (
    ("1. 配队流派、机制演替（G1–G5）、主核—插件共现", "ShiyuStats 清洗后 2,866,763 条合法完整三人记录；危局精确首领池 1,388,743 条",
     "L1 强实证（描述统计）", "作为揭示偏好展示；完整三人、已知成绩与危局精确池三种分母分开报告"),
    ("2. 专拐与通关成绩的条件关系", "固定单主核、已知影画／专武档位及第三人后的条件比较",
     "L1.5 中强实证（条件关联）", "同时给出 P25／P50／P75 与样本量 n；仍受操作、驱动盘词条与主动提交选择影响，不是无偏因果净效应"),
    ("3. 角色保值寿命与售后恢复", "56 期真实主C出场与 0+1 分数轨迹；DPS_01–DPS_16 固定排期受控代理",
     "L2 双轨对照（实证轨迹＋受控代理）", "真实轨迹给出经验半衰期与售后事件窗；合成队在相同 Boss 排期下剥离登场时点"),
    ("4. 囤抽、充值、停氪、退坑与长期现金", "公开样本无跨期个人 ID 与充值标签；效用、疲劳、停氪函数为显式设定",
     "L3 反事实推演（情景模拟＋结构边界）", "扩大人口与种子只降低给定公式下的抽样误差；用两个市场的卡池流水、全服调频统计与付费动机调查校验结构，三种行为函数做结构消融"),
)


def _zh(name):
    return DISPLAY_ZH.get(name, name)


def _evidence(data, name):
    return data.get("evidence", {}).get(name, [])


def evidence_level_rows():
    return list(EVIDENCE_LEVELS)


def real_dps_summary_rows(data):
    rows = [r for r in _evidence(data, "real_dps_longevity") if r["scope"] == "unit_summary"]
    output = []
    for r in sorted(rows, key=lambda r: (r["first_season_start"], r["unit_id"], r["mode"] != "shiyu_defense")):
        life = lambda key: (f"{r[key + '_life_seasons_from_first']} 期／{r[key + '_life_days_from_first']} 天"
                            if r[key + "_life_status"] == "observed" else "未跌破（右删失）")
        output.append((_zh(r["unit_zh"]), MODE_ZH[r["mode"]], r["first_season"], f"{r['peak_appearance_per_clear']:.1%}（{r['peak_season']}）",
                       life("half"), life("quarter"), f"{r['last_retention_vs_peak']:.0%}", r["seasons_observed"]))
    return output


def real_dps_generation_rows(data):
    cells = {(r["unit_id"], r["mech_generation"], int(r["unit_is_prespecified_counter"])): r
             for r in _evidence(data, "real_dps_longevity") if r["scope"] == "da_generation_summary"}
    units = {r["unit_id"]: _zh(r["unit_zh"]) for r in _evidence(data, "real_dps_longevity") if r["scope"] == "unit_summary"}
    order = [r["unit_id"] for r in sorted((r for r in _evidence(data, "real_dps_longevity") if r["scope"] == "unit_summary"
                                           and r["mode"] == "deadly_assault"), key=lambda r: r["first_season_start"])]
    output = []
    for unit in order:
        row = [units[unit]]
        for generation in GENERATIONS:
            parts = [f"{label} {cells[unit, generation, flag]['relative_p50_median_over_rooms']:.2f}"
                     for flag, label in ((1, "对口"), (0, "非对口")) if (unit, generation, flag) in cells]
            row.append("／".join(parts) or "—")
        output.append(tuple(row))
    return output


def generation_sentence(data):
    cells = {(r["unit_id"], r["mech_generation"], int(r["unit_is_prespecified_counter"])): r["relative_p50_median_over_rooms"]
             for r in _evidence(data, "real_dps_longevity") if r["scope"] == "da_generation_summary"}
    paired = [(u, g) for u, g, flag in cells if flag == 1 and (u, g, 0) in cells]
    if not paired:
        return ""
    lower = sum(cells[u, g, 0] < cells[u, g, 1] for u, g in paired)
    units = {u for u, _, _ in cells}
    deepest = Counter(min((g for g in GENERATIONS if (u, g, 0) in cells), key=lambda g: cells[u, g, 0])
                      for u in units if any((u, g, 0) in cells for g in GENERATIONS))
    generation, count = deepest.most_common(1)[0]
    return (f"同时有对口与非对口房间的 {len(paired)} 个主 C × 世代组合中，{lower} 个非对口相对分更低；"
            f"{sum(deepest.values())} 名主 C 中有 {count} 名的非对口最低点出现在 {generation}。")


def _quartile_text(p25, p50, p75):
    return "—" if p50 is None else f"{p50:.2f} [{p25:.2f}–{p75:.2f}]"


def event_window_rows(data):
    rows = _evidence(data, "support_event_window")
    appearance = {(r["event_id"], r["mode"]): r for r in rows if r["scope"] == "appearance_window"}
    scores = {(r["event_id"], r["comparison_design"], r["phase"]): r for r in rows if r["scope"] == "score_window_summary"}
    output = []
    for (event, mode), r in sorted(appearance.items(), key=lambda item: (item[1]["event_season_start"], item[0])):
        if mode != "deadly_assault":
            continue
        shiyu = appearance.get((event, "shiyu_defense"), {})
        change = lambda x: "同期首发" if x.get("main_share_change_pp") is None else f"{x['main_share_change_pp']:+.1f}pp"
        post = scores.get((event, "fixed_third_matched", "post"), {})
        pre = scores.get((event, "main_build_matched", "pre"), {})
        ratio = post.get("matched_median_ratio_geomean")
        output.append((f"{_zh(r['main_zh'])} + {_zh(r['component_zh'])}", r["event_season"],
                       f"{change(r)}／{change(shiyu)}" if shiyu else change(r),
                       f"{post.get('n_with_support', 0):,}／{post.get('n_legacy_support', 0):,}",
                       _quartile_text(post.get("with_rel_p25"), post.get("with_rel_p50"), post.get("with_rel_p75")),
                       _quartile_text(post.get("legacy_rel_p25"), post.get("legacy_rel_p50"), post.get("legacy_rel_p75")),
                       "—" if ratio is None else f"{ratio:.2f}（{post['n_stable_strata']} 层）",
                       _quartile_text(pre.get("legacy_rel_p25"), pre.get("legacy_rel_p50"), pre.get("legacy_rel_p75"))))
    return output


def make_empirical_figures(out, data):
    rows = [r for r in _evidence(data, "real_dps_longevity") if r["scope"] == "appearance_track" and r["mode"] == "deadly_assault"]
    if not rows:
        return {}
    dest = Path(out) / "report/figures"
    dest.mkdir(parents=True, exist_ok=True)
    raw = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["unit_id"], r["delta_t_seasons"])):
        raw[_zh(r["unit_zh"])].append((r["delta_t_seasons"], 100 * r["retention_vs_peak"]))
    series = {unit: [(t, mean(v for _, v in points[max(0, i - 2):i + 1])) for i, (t, _) in enumerate(points)]
              for unit, points in raw.items()}
    name = "l1_real_dps_retention.svg"
    (dest / name).write_text(line_chart(
        series, title="实证｜真实主C的危局出场占比相对自身峰值", subtitle="分母为当期合法完整三人记录；曲线为含当期的 3 期滑动均值，Δt 从首次进入危局合格样本起算，每期约 14 天",
        xlabel="距首发的危局赛季数 Δt", ylabel="出场占比 / 自身峰值（%）", ymin=0, ymax=100,
        hlines=[(50, "半衰线 50%", "5,4"), (25, "25% 线", "2,3")], w=800, h=400), encoding="utf-8")
    return {name: dest / name}


ABLATION_LABELS = (("M0_Baseline", "M0 现行（停氪缓冲）"), ("M1_NoStopSpend", "M1 无停氪缓冲"),
                   ("M2_NoveltyDriven", "M2 强追新偏好"))


def _ablation_lookup(data):
    return {(r["model"], r["proposition"], int(r["horizon"])): r
            for r in data["tables"].get("ablation", {}).get("proposition_summary", [])}


def behavior_ablation_rows(data):
    lookup = _ablation_lookup(data)
    if not lookup:
        return []
    pp = lambda r: _interval(r["retention_core_diff_pp"], r["retention_core_ci95_low_pp"], r["retention_core_ci95_high_pp"], "pp")
    cash = lambda r: _interval(r["cash_relative_pct"], r["cash_relative_ci95_low_pct"], r["cash_relative_ci95_high_pct"], "%")
    output = []
    for model, label in ABLATION_LABELS:
        a10, a20, b20 = lookup[model, "A", 10], lookup[model, "A", 20], lookup[model, "B/C", 20]
        output.append((label, pp(a20), f"{cash(a10)} → {cash(a20)}", pp(b20), cash(b20),
                       _interval(b20["new_main_topup_contribution_pp"], b20["new_main_topup_contribution_ci95_low_pp"],
                                 b20["new_main_topup_contribution_ci95_high_pp"], "pp"),
                       f"{b20['support_topup_contribution_pp']:+.2f}／{b20['subscription_contribution_pp']:+.2f}pp"))
    return output


def _excludes_zero(low, high):
    return low > 0 or high < 0


def ablation_verdict_rows(data):
    """命题 × 行为设定：点估计方向，加逐项 95% 区间是否跨零。"""
    lookup = _ablation_lookup(data)
    if not lookup:
        return []

    def sign(row, value, low, high, unit, positive, negative):
        v, lo, hi = row[value], row[low], row[high]
        word = positive if v > 0 else negative
        return f"{word} {v:+.2f}{unit} [{lo:+.2f}, {hi:+.2f}]" + ("" if _excludes_zero(lo, hi) else "，区间跨零")

    output = []
    specs = (
        ("A：λ 0→2 损害核心留存", "A", "retention_core_diff_pp", "retention_core_ci95_low_pp", "retention_core_ci95_high_pp", "pp", "上升", "下降"),
        ("A：λ 0→2 降低 20 期总现金", "A", "cash_relative_pct", "cash_relative_ci95_low_pct", "cash_relative_ci95_high_pct", "%", "上升", "下降"),
        ("B：a 0.8→1.6 提升核心留存", "B/C", "retention_core_diff_pp", "retention_core_ci95_low_pp", "retention_core_ci95_high_pp", "pp", "提升", "下降"),
        ("C：a 0.8→1.6 替代新主C池直购（基期总现金贡献）", "B/C", "new_main_topup_contribution_pp", "new_main_topup_contribution_ci95_low_pp", "new_main_topup_contribution_ci95_high_pp", "pp", "增加", "减少"),
        ("C：a 0.8→1.6 的 20 期总现金", "B/C", "cash_relative_pct", "cash_relative_ci95_low_pct", "cash_relative_ci95_high_pct", "%", "上升", "下降"),
    )
    for title, proposition, *keys in specs:
        output.append((title, *(sign(lookup[model, proposition, 20], *keys) for model, _ in ABLATION_LABELS)))
    return output


BANNER_ZH = {"Caesar": "凯撒", "Jane": "简", "Astra Yao": "耀嘉音", "Miyabi & Harumasa": "星见雅＆浅羽悠真",
             "Yuzuha": "浮波柚叶", "Yixuan": "仪玄", "Ju Fufu": "橘福福", "Lucia": "卢西娅", "Sunna": "千夏",
             "Ye Shunguang & Zhao": "叶瞬光＆照", "Trigger": "「扳机」", "Soldier 0 - Anby": "零号·安比"}


def _post_sale_cores(data):
    return {r["unit"]: r["post_sale_strong_cores"] for r in _evidence(data, "support_archetypes")}


def external_ratio_rows(data):
    post_sale = _post_sale_cores(data)
    return [(r["category"], r["support_zh"], post_sale.get(r["support_banner"], "—"), r["reference_zh"],
             f"¥{r['support_jpy'] / 1e8:.2f} 亿／¥{r['reference_jpy'] / 1e8:.2f} 亿", f"{r['ratio']:.0%}",
             f"{r['jp_per_day_ratio']:.0%}", f"{r['cn_iphone_per_day_ratio']:.0%}", f"{r['jp_floor_adjusted_ratio']:.0%}",
             f"#{r['support_cn_best_rank']}／#{r['reference_cn_best_rank']}")
            for r in _evidence(data, "banner_category_ratios")]


def _pair(data, support):
    return next((r for r in _evidence(data, "banner_category_ratios") if r["support_banner"] == support), None)


def _ratio_values(data, category, key, exclude=()):
    return [r[key] for r in _evidence(data, "banner_category_ratios")
            if r["category"] == category and r["support_banner"] not in exclude]


def support_archetype_rows(data, since="2025-01-01"):
    rows = [r for r in _evidence(data, "support_archetypes") if r["release"] >= since]
    return [(_zh(r["unit_zh"]), r["specialty_zh"], r["release"], r["category"],
             "、".join(json.loads(r["family_groups"])) or "—", r["n_strong_cores"], r["strong_cores"] or "—")
            for r in sorted(rows, key=lambda r: r["release"])]


def anomaly_ecosystem_rows(data):
    pct = lambda v: "—" if v is None else f"{v:.0%}"
    return [(r["combat_season"], r["season_start"], pct(r["three_anomaly_share"]),
             pct(r["in_anomaly_Remielle"]), pct(r["in_anomaly_Velina"]), pct(r["in_anomaly_Yuzuha"]),
             pct(r["in_anomaly_Nangong Yu"]), pct(r["in_anomaly_Astra Yao"]),
             pct(r["appear_Jane"]), pct(r["appear_Burnice"]), pct(r["appear_Claret"]))
            for r in _evidence(data, "anomaly_ecosystem") if r["mode"] == "deadly_assault"]


def armorer_room_rows(data, since="3.2.1"):
    return [(r["combat_season"], r["stage_slot"], r["boss_zh"], r["mech_tag"], r["favored_group"],
             f"{r['anomaly_core_team_share']:.0%}", f"{r['armorer_core_team_share']:.0%}")
            for r in _evidence(data, "room_archetype_share")
            if tuple(map(int, str(r["combat_season"]).split("."))) >= tuple(map(int, since.split(".")))]


def armorer_room_contrast(data):
    rows = [r for r in _evidence(data, "room_archetype_share") if str(r["combat_season"]) >= "3.2.1"]
    locked = [r["anomaly_core_team_share"] for r in rows if r["anomaly_penalty"] == 1]
    favored = [r["anomaly_core_team_share"] for r in rows if r["anomaly_penalty"] == 0
               and ("异常" in r["favored_group"] or "三异常" in r["favored_group"])]
    armorer = [r["armorer_core_team_share"] for r in rows if r["anomaly_penalty"] == 1]
    return (locked, favored, armorer) if locked and favored else None


ARCHETYPE_PRODUCTS = {"narrow": "窄专拐", "system": "体系专拐", "hybrid": "混合型 ρ=0", "hybrid_r25": "混合型 ρ=0.25",
                      "hybrid_r50": "混合型 ρ=0.5", "hybrid_r75": "混合型 ρ=0.75"}


def _archetype(data, name):
    return data["tables"].get("archetype", {}).get(name, [])


def archetype_decomposition_rows(data):
    sign = lambda v: "—" if v is None else f"{v:+.1f}"
    return [(f"λ={r['lam']:g}", ARCHETYPE_PRODUCTS[r["product"]], sign(r["naive_change_fit_pp"]), sign(r["trend_pp"]),
             sign(r["env_effect_pp"]),
             _interval(r["product_effect_fit_pp"], r["product_effect_fit_low"], r["product_effect_fit_high"], "", 1),
             sign(r["product_effect_unfit_pp"]), sign(r["interaction_pp"]),
             _interval(r["retention_core_t20_diff_fit_pp"], r["retention_core_t20_diff_fit_low"],
                       r["retention_core_t20_diff_fit_high"], "", 1))
            for r in _archetype(data, "a_decomposition")]


def archetype_selection_rows(data):
    num = lambda v: "—" if v is None else f"{v:.2f}"
    return [(f"λ={r['lam']:g}", ARCHETYPE_PRODUCTS[r["product"]], "对口" if r["env"] == "fit" else "非对口",
             num(r["observational_ratio"]), num(r["experimental_ratio_owners"]),
             "—" if r["selection_gap"] is None else f"{r['selection_gap']:+.2f}",
             f"{r['n_owners_mean']:.0f}／{r['n_nonowners_mean']:.0f}")
            for r in _archetype(data, "a_selection_bias")]


def archetype_displacement_rows(data):
    return [(f"λ={r['lam']:g}", ARCHETYPE_PRODUCTS[r["product"]], "对口" if r["env"] == "fit" else "非对口",
             f"{r['flex_share_product']:.0%}", f"{r['control_flex_share_old_support']:.0%} → {r['flex_share_old_support']:.0%}",
             f"{r['product_deployed_as_main']:.0%}",
             "—" if r.get("hybrid_flex_share") is None else f"{r['hybrid_flex_share']:.0%}")
            for r in _archetype(data, "a_displacement")]


def archetype_b_rows(data):
    out = []
    for r in _archetype(data, "b_generation_shift"):
        digits = 0 if "元" in r["label"] else 1
        out.append((r["label"], _interval(r["lock_effect"], r["lock_low"], r["lock_high"], "", digits),
                    _interval(r["postsale_effect_under_lock"], r["postsale_low"], r["postsale_high"], "", digits),
                    "—" if r["recovered_share"] is None else f"{r['recovered_share']:.0%}"))
    return out


ROLE_ZH = {"dps": "主C", "support": "支援", "subdps": "副C"}


def rngmoe_banner_rows(data):
    names = {r["unit_id"]: _zh(r["unit_zh"]) for r in _evidence(data, "real_dps_longevity")}
    names.update({"Miyabi": "星见雅", "Astra Yao": "耀嘉音", "Yuzuha": "浮波柚叶", "Lucia": "卢西娅", "Ju Fufu": "橘福福",
                  "Trigger": "「扳机」", "Caesar": "凯撒", "Qingyi": "青衣", "Lighter": "莱特", "Burnice": "柏妮思", "Yanagi": "柳",
                  "Vivian": "薇薇安", "Hugo": "雨果", "Alice": "爱丽丝", "Seed": "「席德」", "Orphie & Magus": "奥菲丝&「鬼火」",
                  "Yidhari": "伊德海莉", "Dialyn": "琉音", "Banyue": "般岳"})
    return [(names.get(r["featured_unit"], r["featured_unit"]), ROLE_ZH.get(r["featured_role"], r["featured_role"]), r["start_date"],
             f"{r['users']:,}", f"{r['total_pulls'] / 1e6:.2f}M", f"{r['pulls_per_user']:.1f}",
             "—" if r["w_engine_to_character_pulls"] is None else f"{r['w_engine_to_character_pulls']:.2f}",
             "—" if r["w_engine_users_share"] is None else f"{r['w_engine_users_share']:.0%}")
            for r in _evidence(data, "rngmoe_banners")]


def benchmark_report_rows(data):
    return [(r["metric"], r["real"], r["simulated"], r["verdict"], r["note"] or "—") for r in _evidence(data, "sim_benchmarks")]


PITY_COMPARISON_MAX = {"character": 82, "w_engine": 72}


def pity_tail_note(data):
    rows = _evidence(data, "rngmoe_pity_curve")
    shares = []
    for pool, label in (("character", "角色池"), ("w_engine", "音擎池")):
        pool_rows = [r for r in rows if r["pool"] == pool]
        if pool_rows:
            tail = sum(r["pulls"] for r in pool_rows if r["roll"] > PITY_COMPARISON_MAX[pool])
            shares.append(f"{label}第 {PITY_COMPARISON_MAX[pool]} 抽之后占 {tail / sum(r['pulls'] for r in pool_rows):.2%}")
    return ("图中只对比到出率仍单调上升的位置。之后观测出率反而回落，与硬保底必出矛盾，应来自导入记录的计数偏差；"
            "这部分样本极少（" + "，".join(shares) + "），不纳入对照。") if shares else ""


def make_external_figures(out, data):
    rows = _evidence(data, "rngmoe_pity_curve")
    if not rows:
        return {}
    dest = Path(out) / "report/figures"
    dest.mkdir(parents=True, exist_ok=True)
    series = {}
    for pool, label, low, high in (("character", "角色池", 60, PITY_COMPARISON_MAX["character"]),
                                   ("w_engine", "音擎池", 52, PITY_COMPARISON_MAX["w_engine"])):
        points = [r for r in rows if r["pool"] == pool and low <= r["roll"] <= high]
        series[f"{label}｜rng.moe 观测"] = [(r["roll"], 100 * r["observed_chance"]) for r in points]
        series[f"{label}｜模拟器设定"] = [(r["roll"], 100 * r["simulated_chance"]) for r in points]
    name = "ext_pity_curve.svg"
    (dest / name).write_text(line_chart(
        series, title="外部校验｜逐抽 5★ 出率：全服统计 vs 模拟器",
        subtitle=f"rng.moe 2024-07 至 2026-01 全部限定池合并；角色池截至第 {PITY_COMPARISON_MAX['character']} 抽、音擎池截至第 {PITY_COMPARISON_MAX['w_engine']} 抽",
        xlabel="当前保底计数（抽）", ylabel="该抽出 5★ 的概率（%）", ymin=0, w=800, h=400), encoding="utf-8")
    return {name: dest / name}


def _l2_summary(data):
    rows = _base(data["tables"]["fixed"]["l2_reference_lifetimes"])
    output = []
    for gear, label in (("fixed_G1", "初始面板（不刷盘）"), ("growth_from_debut", "持续刷盘养成（上限 +16%）")):
        for investment in ("0+0", "0+1", "2+1"):
            group = [r for r in rows if r["gear_policy"] == gear and r["investment_plan"] == investment and r["support_policy"] == "none"]
            median = survival_median(group)
            output.append((investment, label, len(group), sum(_truth(r["first_failure_event"]) for r in group),
                           f"{median} 天" if median is not None else "Median Not Reached"))
    return output


def render_publication_report(out, data):
    out = Path(out)
    tables = data["tables"]["n1000"]
    meta = data["manifest"]
    obs = data["observations"]
    from .archetype_presentation import archetype_markdown
    from .boss_presentation import PROXY_NOTE, boss_markdown
    from .observed_funnel import funnel_markdown

    observation_text = archetype_markdown(out) + boss_markdown(out).replace(PROXY_NOTE, PROXY_V2_NOTE)
    pairs = registered_early_pairs(data)
    pair_rows = []
    for r in pairs:
        support = r["support_policy"].split(":")[-1]
        nat = r["natural_first_failure_or_censor_days"]
        treated = r["first_failure_or_censor_days"]
        event = _truth(r["first_failure_event"])
        nat_event = _truth(r["natural_first_failure_event"])
        age = r.get("first_recovery_age_days")
        pair_rows.append((r["anchor_unit_id"] + " + " + support,
                          f"{nat} 天" + ("" if nat_event else "（右删失）"),
                          f"{treated} 天" + ("" if event else "（右删失）"),
                          f"{int(r['incremental_full_star_periods']):+d} 期", "—" if age is None else f"{age} 天"))
    ranks = {(int(r["horizon"]), r["metric"]): r for r in tables["rank_stability"]}
    previous = {(int(r["horizon"]), r["metric"]): r for r in tables["population_rank_comparison"]}
    rank_rows = []
    for metric, label in (("cash_yuan", "总现金"), ("new_main_topup_yuan", "新主 C 池直购"),
                          ("support_topup_yuan", "专拐池直购"), ("subscription_yuan", "月卡续订"),
                          ("retention_core", "核心层留存"), ("retention_all", "全队列留存")):
        for horizon in (10, 20):
            rank_rows.append((label, horizon, f"{previous[horizon, metric]['previous_rho']:.3f}", f"{ranks[horizon, metric]['rho']:.3f}"))
    focus_rows = [(r["players"], f"λ={r['mechanism_strength']:g}，a={r['support_gain_scale']:g}",
                   f"¥{r['mean_cash_per_initial_player_yuan']:,.2f}", f"{r['retention_core']:.1%}",
                   _interval(r["paired_cash_per_player_diff_yuan"], r["paired_cash_per_player_ci95_low_yuan"],
                             r["paired_cash_per_player_ci95_high_yuan"]), f"{r['included_seeds']}/{r['n_seeds']}")
                  for r in tables["focus_candidates_comparison"]]
    validation = [r for r in tables["grid_summary"] if int(r["horizon"]) == 20 and r["split"] == "validation"]
    inclusion = {r["policy_id"]: r for r in tables["inclusion_frequency"] if int(r["horizon"]) == 20 and r["split"] == "validation"}
    candidate_rows = [(f"{r['mechanism_strength']:g}", f"{r['support_gain_scale']:g}", f"¥{r['cash_yuan']:,.0f}",
                       f"{r['retention_core']:.1%}", f"{inclusion[r['policy_id']]['included_seeds']}/{inclusion[r['policy_id']]['n_seeds']}")
                      for r in sorted(validation, key=lambda r: (r["mechanism_strength"], r["support_gain_scale"]))]
    cash_base = next(r for r in validation if r["policy_id"] == "L1_A1")
    ablations = [r for r in data["tables"]["fixed"].get("run_summary", [])
                 if r["policy_id"] == "L1_A1" and r["seed"] == meta["train_seeds"][0]]
    ablation_rows = [(r["horizon"], "单盾代理" if r["combat_variant"] == "v1" else "三通道代理",
                      "基线行为" if r["spending_variant"] == "v1" else "疲劳接入付费", r["players"],
                      f"¥{r['cash_yuan']:,.0f}", f"{r['retention_core']:.1%}")
                     for r in sorted(ablations, key=lambda r: (r["horizon"], r["combat_variant"], r["spending_variant"]))]
    endpoints = {(r["axis"], r["metric"]): r for r in tables["axis_effects"]
                 if int(r["horizon"]) == 20 and r["split"] == "validation" and r["contrast_kind"] == "endpoint"}
    lock_cash = endpoints["mechanism_strength", "cash_yuan"]
    lock_retention = endpoints["mechanism_strength", "retention_core"]
    support_cash = endpoints["support_gain_scale", "cash_yuan"]
    support_retention = endpoints["support_gain_scale", "retention_core"]
    short_lock_cash = next(r for r in tables["axis_effects"]
                           if int(r["horizon"]) == 10 and r["split"] == "train" and r["contrast_kind"] == "endpoint"
                           and r["axis"] == "mechanism_strength" and r["metric"] == "cash_yuan")
    short_text = (f"前 10 期筛选样本中，同一对照的累计现金变化为 **{_interval(short_lock_cash['mean_relative_pct'], short_lock_cash['relative_ci95_low'], short_lock_cash['relative_ci95_high'], '%')}**"
                  + ("：机制压力先推高现金，到 20 期转为现金与留存同时下降。"
                     if float(short_lock_cash["mean_relative_pct"]) > 0 > float(lock_cash["mean_relative_pct"]) else "。"))
    decreased, increased, crossing = _support_cash_components(data)
    anchor_low, anchor_high = _support_anchor_range(out)
    valid_records = data["layer1"]["valid_records"]
    seasons = sorted({r["combat_season"] for r in data["layer1"]["evolution"]},
                     key=lambda s: tuple(map(int, s.split("."))))
    focus_current = [r for r in tables["focus_candidates_comparison"] if r["players"] == meta["players"]]
    focus_text = "、".join(f"a={r['support_gain_scale']:g}：{r['included_seeds']}/{r['n_seeds']}" for r in focus_current)
    late = next(r for r in pairs if r["anchor_unit_id"] == "DPS_03" and r["support_policy"] == "join_on_debut:S_B")
    early = next(r for r in pairs if r["anchor_unit_id"] == "DPS_03" and r["support_policy"] == "join_on_debut:S_A")
    fixed_rows = _base(data["tables"]["fixed"]["l2_reference_lifetimes"])
    fixed_subjects = len({r["anchor_unit_id"] for r in fixed_rows})
    guide = "../../../docs/review/REVIEW_GUIDE.md"
    longevity = [r for r in _evidence(data, "real_dps_longevity") if r["scope"] == "unit_summary" and r["mode"] == "deadly_assault"]
    lived = sorted((r for r in longevity if r["half_life_status"] == "observed"), key=lambda r: r["half_life_seasons_from_first"])
    short = [r for r in lived if r["half_life_seasons_from_first"] <= 6]
    long_ = [r for r in longevity if r not in short]
    long_text = "、".join(f"{_zh(r['unit_zh'])} {r['half_life_seasons_from_first']} 期（{r['half_life_days_from_first']} 天）"
                          for r in sorted(long_, key=lambda r: r["first_season_start"]) if r["half_life_status"] == "observed")
    event_rows = _evidence(data, "support_event_window")
    post_ratios = [r["matched_median_ratio_geomean"] for r in event_rows
                   if r["scope"] == "score_window_summary" and r["event_type"] == "post_launch_support"
                   and r["comparison_design"] == "fixed_third_matched" and r["phase"] == "post"
                   and r["matched_median_ratio_geomean"] is not None]
    lift = {r["event_id"]: r for r in event_rows if r["scope"] == "appearance_window" and r["mode"] == "deadly_assault"}
    ablation = _ablation_lookup(data)
    ablation_runs = data["tables"].get("ablation", {}).get("run_summary", [])
    by_run = {(r["model"], r["policy_id"], r["seed"], r["horizon"]): r for r in ablation_runs}
    m1_pairs = [(r, by_run[("M0_Baseline", policy, seed, horizon)]) for (model, policy, seed, horizon), r in by_run.items()
                if model == "M1_NoStopSpend" and horizon == 20]
    m1_total = len(m1_pairs)
    m1_same_retention = sum(a["retention_core"] == b["retention_core"] and a["retention_all"] == b["retention_all"] for a, b in m1_pairs)
    m1_same_pulls = sum(a["total_pulls"] == b["total_pulls"] for a, b in m1_pairs)
    rupture = [p for p in (_pair(data, "Lucia"), _pair(data, "Ju Fufu")) if p]
    yuzuha, nangong = _pair(data, "Yuzuha"), _pair(data, "Nangong Yu")
    cross = [p for p in (_pair(data, "Sunna"), _pair(data, "Trigger")) if p]
    generic_floor = _ratio_values(data, "泛用", "jp_floor_adjusted_ratio")
    post_sale = _post_sale_cores(data)
    room_contrast = armorer_room_contrast(data)
    eco = [r for r in _evidence(data, "anomaly_ecosystem") if r["mode"] == "deadly_assault"]
    eco_pre = [r for r in eco if r["in_anomaly_Remielle"] in (None, 0)]
    eco_post = [r for r in eco if r["in_anomaly_Remielle"]]
    archetype_a = {(r["lam"], r["product"]): r for r in _archetype(data, "a_decomposition")}
    archetype_disp = _archetype(data, "a_displacement")
    archetype_b = {r["metric"]: r for r in _archetype(data, "b_generation_shift")}
    remielle_core = [r["with_other_anomaly_core_Remielle"] for r in eco_post
                     if r.get("with_other_anomaly_core_Remielle") is not None]
    hybrid_flex = defaultdict(list)
    for r in archetype_disp:
        if r.get("hybrid_flex_share") is not None:
            hybrid_flex[r["co_output"]].append(r["hybrid_flex_share"])
    # 校准档：模拟中“混合型坐辅助位”的区间触及现实“带蕾米埃尔的队伍同时有其他异常主核”区间的最低 ρ
    rho_fit = min((rho for rho, v in hybrid_flex.items() if remielle_core and max(v) >= min(remielle_core)), default=None)
    hybrid_a = [r for r in _archetype(data, "a_decomposition") if r["product"].startswith("hybrid")]
    hybrid_d = [r for r in archetype_disp if r["product"].startswith("hybrid")]
    span = lambda values: f"{min(values):+.1f}～{max(values):+.1f}"
    fitted = lambda rows, lam=None: [r for r in rows if rho_fit is not None and r["co_output"] >= rho_fit
                                     and (lam is None or r["lam"] == lam)]
    bench ={r["metric"]: r for r in _evidence(data, "sim_benchmarks")}
    lock_parts = {r["metric"]: r["mean_diff_yuan"] for r in tables["axis_cash_decomposition"]
                  if int(r["horizon"]) == 20 and r["split"] == "validation" and r["axis"] == "mechanism_strength"
                  and r["contrast_kind"] == "endpoint"}
    pct = lambda values: f"{min(values):.0%}–{max(values):.0%}"
    sources = {s["source_id"]: s for s in data.get("external_sources", [])}
    m2_lock, m2_support = ablation.get(("M2_NoveltyDriven", "A", 20)), ablation.get(("M2_NoveltyDriven", "B/C", 20))
    m0_lock = ablation.get(("M0_Baseline", "A", 20))
    ablation_bullet = (
        f"- **结构消融区分稳健结论与心理假设依赖结论。** 三种行为函数（N=200、3 组复核种子、9 个角点／中心点）下，"
        f"λ 0→2 降低核心留存、a 提高替代部分新主 C 池直购，点估计方向一致；停氪缓冲只改变月卡续订现金，不改变留存。"
        f"长期现金的负号与 a 的留存收益只在“抽新主 C 主要由强度缺口驱动”时成立：强追新偏好 M2 下，λ 的 20 期现金变化为 "
        f"**{_interval(m2_lock['cash_relative_pct'], m2_lock['cash_relative_ci95_low_pct'], m2_lock['cash_relative_ci95_high_pct'], '%')}**，"
        f"a 的留存变化为 **{_interval(m2_support['retention_core_diff_pp'], m2_support['retention_core_ci95_low_pp'], m2_support['retention_core_ci95_high_pp'], 'pp')}**。\n"
        if m2_lock and m2_support else "")
    if bench:
        ablation_bullet += (
            "- **外部校验：抽卡机制吻合，收入结构偏离。** 模拟的软保底、小保底与综合出率与 rng.moe 全服统计一致；"
            "但模拟现金约七成来自月卡续订，而真实头部主C池的月卡／常驻地板占比不超过约四分之一，"
            "且模拟只有体系绑定型专拐，没有耀嘉音、浮波柚叶这类高需求泛用辅助（第 6 节）。\n")
    parts = [
        "# 机制锁与售后专拐：老角色保值、付费心理与长期现金\n",
        "> 《绝区零》业务原型下的三层研究：公开通关样本描述 + 固定战斗配对 + 玩家行为反事实情景。\n",
        "## 1. 研究问题与结论导读\n",
        "官方通过强化机制锁 λ，或提高售后支援的净增益 a，分别会怎样改变老角色的可用寿命、玩家囤抽与付费，以及长期现金和留存？\n",
        f"- **高机制压力存在长期代价。** 20 期独立复核中，λ 从 0 增至 2，核心留存变化 **{lock_retention['mean_diff']:+.2f} 个百分点**，累计现金变化 **{_interval(lock_cash['mean_relative_pct'], lock_cash['relative_ci95_low'], lock_cash['relative_ci95_high'], '%')}**（逐项 95% 区间）。{short_text}\n"
        f"- **强专拐主要体现为保值与现金结构变化。** a 从 0.8 增至 1.6，核心留存变化 **{support_retention['mean_diff']:+.2f} 个百分点**；总现金变化 **{_interval(support_cash['mean_relative_pct'], support_cash['relative_ci95_low'], support_cash['relative_ci95_high'], '%')}**。{_direction_sentence(decreased, increased)}\n"
        "- **售后到得晚，救回可用也不等于延后首次失效。** 同配置固定配对能观察到延寿、零延寿和失效后恢复三种结果；支援排期是保值策略的一部分。\n"
        f"- **扩样后排名更稳定，但没有唯一最优结论。** 每次 N={meta['players']}、{len(meta['train_seeds'])} 组筛选 + {len(meta['validation_seeds'])} 组独立复核；总现金排序相关 ρ 在 10/20 期分别为 **{ranks[10, 'cash_yuan']['rho']:.3f} / {ranks[20, 'cash_yuan']['rho']:.3f}**。预先指定的 λ=1 候选在 20 期复核的非支配集合入选为 **{focus_text}**。\n"
        + ablation_bullet,
        md_table(["层次", "证据与规模", "回答什么"], [
            ("Layer 1 公开样本", f"{obs['n_battles']:,} 条已导入；{obs['n_eligible']:,} 条合格通关；{valid_records:,} 条合法完整三人记录", "真实样本中的机制与队伍搭配、条件成绩、真实主C出场轨迹"),
            ("Layer 2 固定参考", f"λ=1、a=1；{fixed_subjects} 名主 C，3 档投资、2 档养成、5 档支援", "同一主 C、同一环境下的模型内延寿与恢复"),
            ("Layer 3 行为情景", f"25 候选 × {len(meta['train_seeds']) + len(meta['validation_seeds'])} 种子 × {meta['players']} 初始玩家；20 期，另取前 10 期", "指定心理和商品假设下的现金、囤抽及留存取舍"),
        ]),
        "### 研究内容与证据强度分级\n",
        md_table(["研究模块", "现有数据支撑边界", "证据等级", "本报告的使用方式"], evidence_level_rows()),
        "四个模块的证据强度不同。L1／L1.5 是真实样本的描述与条件关联；L2 把真实轨迹与受控代理并列；"
        "L3 的留存与现金差（如 λ 0→2 的核心留存与累计现金变化）是固定初始队列在显式行为函数下的模拟结果，"
        "不是《绝区零》的官方流水或真实退坑率。扩大模拟人口与种子只降低给定公式下的蒙特卡洛误差，不弥补行为设定本身的缺口，"
        "因此本版用外部卡池流水结构和行为函数结构消融界定 L3 结论的适用边界。\n",
        "## 2. 公开样本：真实配队、机制演替与校准锚点（Layer 1）\n",
        f"公开记录来自 ShiyuStats / Hugging Face 固定快照 `{obs['snapshot_id']}`，覆盖 {len(seasons)} 个观测战斗赛季（{seasons[0]}–{seasons[-1]}）、{data['layer1']['unit_count']} 名角色。"
        "总导入、合格通关与合法完整三人是不同分母；下列流派表以完整三人为分母，机制精确对照另排除 Boss 关联未决记录。\n",
        funnel_markdown(out),
        observation_text,
        "\n### 2.8 两个旋钮如何使用实证锚点\n",
        "[机制条件成绩表](../empirical_knob1_boss_lock.csv)比较当期机制对策与非对策样本，"
        "[专拐条件成绩表](../empirical_knob2_support_lift.csv)保留主 C 投资、第三人及装备匹配分层。"
        f"危局普通路、固定第三人匹配的强绑定 0+1 例中，{'、'.join(zh for _, zh in SUPPORT_ANCHOR_MAINS[:-1])}与{SUPPORT_ANCHOR_MAINS[-1][1]}的条件成绩比约为 {anchor_low:.2f}–{anchor_high:.2f}；"
        "主 C 养成匹配与式舆防卫战分层的数值另列于同表。这些是特定分层的点估计，不是统一上限。"
        "自拐饱和或通用队伍的增量可能接近 1。逐层行同时给出两臂的样本量 n 与 P25／P50／P75，"
        "比值之外可直接比较分布宽度；它们仍受玩家操作、驱动盘词条与主动提交的样本选择影响，属于条件关联。\n",
        "模型把这些现象转译为防御适配、有效输出窗口和侵蚀壁垒三个通道；支援分为强/弱净增益与独立适配修复。"
        "a 放大净增益项，λ 改变机制压力；具体映射及心理参数在科学配置中保留证据标签。\n",
        "### 2.9 辅助为谁服务：体系归属、混合型与换代\n",
        "辅助的服务范围从主核一侧衡量：某主核的完整三人记录中带该辅助的比例（搭配率），只统计辅助上线之后的赛季。"
        "搭配率 ≥30% 的主核记为强搭配；强搭配主核只有一名为单核专拐，都在同一机制流派组为体系专拐，跨两个以上流派组为跨体系，"
        "八名以上为泛用。“*”表示该辅助晚于这名主核上线，对它属于售后；其余为体系底座。\n",
        md_table(["辅助", "职业", "上线", "类别", "机制流派组", "强搭配主核数", "强搭配主核（* 售后）"], support_archetype_rows(data)),
        "卢西娅覆盖命破全部五名主核（搭配率 87%–95%），但只有对仪玄属于售后，对之后的真斗、伊德海莉、般岳、星徽·比利是体系底座；"
        "浮波柚叶覆盖九名异常主核；照几乎只服务叶瞬光。完整的辅助×主核搭配率见[搭配率表](../empirical_support_attach.csv)。\n",
        "**混合型：蕾米埃尔。** 她在数据中是主核（耀变体系），队伍几乎全部满足三异常；对十名异常主核的搭配率在 51%–96%，且全部属于售后。"
        + (f"带她的危局队伍中 {min(remielle_core):.1%}–{max(remielle_core):.1%} 同时有其他异常主核，她几乎总是与旧主C同队输出。"
           if remielle_core else "") +
        "下表为危局各期的异常体系结构（异常队指含异常主核的队伍）。\n",
        md_table(["赛季", "开始", "三异常队伍占比", "异常队中带蕾米埃尔", "带维琳娜", "带浮波柚叶", "带南宫羽", "带耀嘉音",
                  "简出场", "柏妮思出场", "克拉蕾出场"], anomaly_ecosystem_rows(data)),
        ((f"蕾米埃尔上线前，三异常队伍为 0%，浮波柚叶在异常队中的搭配率为 "
          f"{min(r['in_anomaly_Yuzuha'] for r in eco_pre):.0%}–{max(r['in_anomaly_Yuzuha'] for r in eco_pre):.0%}；"
          f"上线后三异常队伍占 {min(r['three_anomaly_share'] for r in eco_post):.0%}–{max(r['three_anomaly_share'] for r in eco_post):.0%}，"
          f"浮波柚叶降至 {min(r['in_anomaly_Yuzuha'] for r in eco_post):.0%}–{max(r['in_anomaly_Yuzuha'] for r in eco_post):.0%}，"
          f"简的出场由 {min(r['appear_Jane'] for r in eco_pre):.0%}–{max(r['appear_Jane'] for r in eco_pre):.0%} 回到 "
          f"{min(r['appear_Jane'] for r in eco_post):.0%}–{max(r['appear_Jane'] for r in eco_post):.0%}。"
          "被替代的是旧专拐，旧主C反而回到场上；三异常要求队内全是异常职业，非异常的浮波柚叶、南宫羽、耀嘉音因此被挤出。")
         if eco_pre and eco_post else "")
        + "她的卡池以主C规格售出（日本流水约 ¥13.8 亿，40 个卡池中第 4），对旧主C的作用却接近售后专拐。\n",
        "**新体系换代：锋御。** 克拉蕾（锋御）上线的同期，危局出现针对锋御设计、同时压制异常的首领。\n",
        md_table(["赛季", "房间", "首领", "机制", "对策组", "异常队占比", "锋御队占比"], armorer_room_rows(data)),
        ((f"“异常伤害 −40%”的库萨里库房间里，异常队只占 {min(room_contrast[0]):.0%}–{max(room_contrast[0]):.0%}，"
          f"锋御队占 {min(room_contrast[2]):.0%}–{max(room_contrast[2]):.0%}；同期偏向异常的房间里异常队占 "
          f"{min(room_contrast[1]):.0%}–{max(room_contrast[1]):.0%}。这是同一批玩家按房间换队，不是退坑。")
         if room_contrast else "")
        + "随后（10-21）上线的菲欧妮是异常职业、蕾米埃尔的队友（不在 ShiyuStats 角色表中，按项目方说明登记），"
          "开池约 10 天的日本、中国 iPhone 流水已超过克拉蕾与洛克茜整期：官方一边用机制锁推动换代，一边继续给被压制的主流体系出售后。\n",
        "## 3. 固定战斗对照：投资、养成与售后时点（Layer 2）\n",
        "公开样本中的机制与队伍更替提供业务原型。固定面板将玩家购买选择、退坑与钱包全部移开，只比较同一主 C 在同一环境中是否获得对应支援。"
        "以 CI≥1.00 定义满星，0.95 为舒适偏好线；角色年龄从登场开始按真实 42 天物理期推进。\n",
        "本节同时展示真实角色与 DPS_01～DPS_16。真实角色（如仪玄、星见雅、简）反映 56 期实际排期下的经验出场率与条件分数轨迹；"
        "DPS_01～DPS_16 固定首发间隔与养成节奏，在完全相同的 Boss 排期下剥离登场时间的混杂，单独测量首次失效寿命与后期专拐恢复。\n",
        "### 3.1 实证轨：真实主C的跨赛季保值轨迹\n",
        "ShiyuStats 按采集期重编匿名 ID，无法追踪同一玩家；但角色、影画／专武档位、配队与赛季时间在 56 期间口径固定。"
        "下表以每名主 C 首次进入合格样本的赛季为 Δt=0，出场占比的分母为当期合法完整三人记录；半衰期为出场占比首次跌破自身峰值 50% 的赛季。"
        "艾莲、朱鸢与简的首发早于数据起点，其 Δt=0 为数据覆盖起点（左删失）。\n",
        "![真实主C出场占比相对峰值](figures/l1_real_dps_retention.svg)\n",
        md_table(["主 C", "模式", "Δt=0 赛季", "峰值出场占比（赛季）", "跌破峰值 50%", "跌破峰值 25%", "最新赛季／峰值", "观测赛季数"],
                 real_dps_summary_rows(data)),
        (f"危局轨道上，{'、'.join(_zh(r['unit_zh']) for r in short)}的经验半衰期为 "
         f"{min(r['half_life_seasons_from_first'] for r in short)}–{max(r['half_life_seasons_from_first'] for r in short)} 期"
         f"（{min(r['half_life_days_from_first'] for r in short)}–{max(r['half_life_days_from_first'] for r in short)} 天）；"
         f"{long_text}的保值期明显更长。" if short else "")
        + "出场占比下降同时包含新角色竞争、机制对口度和样本构成变化，不等同于持有者退坑或角色失效。\n",
        "固定 0+1、单主核（队内唯一主核）的危局记录给出分数轨迹。为消除首领难度差，每条分数除以同赛季同房间全部合法记录的分数中位数；"
        "下表为各世代房间相对分数 P50 的房间中位数，分别列主 C 属于预登记对策组（对口）与不属于（非对口）的房间。\n",
        md_table(["主 C", *GENERATIONS], real_dps_generation_rows(data)),
        generation_sentence(data)
        + "逐房间的 n、P25／P50／P75 与首领机制见[真实主C保值轨迹表](../empirical_real_dps_longevity.csv)。\n",
        "### 3.2 实证轨：真实售后专拐上线的事件窗口\n",
        "以插件首次进入合格样本的赛季为事件期，比较首发前 2 期与首发当期及后 2 期。成绩对照固定主核 0+1，"
        "并固定第三名队友身份、要求旧辅助与新插件同职业（`fixed_third_matched`）；"
        "旧辅助为 A／常驻 S／免费 S，或早于该插件上线的支援／副输出。相对分数的定义同 3.1。\n",
        md_table(["主 C + 插件", "危局事件期", "主 C 出场占比变化（危局／式舆）", "带新插件／带旧辅 n",
                  "带新插件 相对分 P50 [P25–P75]", "带旧辅 相对分 P50 [P25–P75]", "层内中位数比几何均值（稳定层）", "事件前 旧辅 P50 [P25–P75]"],
                 event_window_rows(data)),
        (f"售后上线的插件在固定第三人分层中均与更高的相对分数同时出现，层内中位数比为 {min(post_ratios):.2f}–{max(post_ratios):.2f}。"
         if post_ratios else "")
        + (f"出场占比方面，卢西娅上线后仪玄的危局出场占比 {lift['Yixuan+Lucia']['main_share_change_pp']:+.1f}pp，"
           f"浮波柚叶上线后简 {lift['Jane+Yuzuha']['main_share_change_pp']:+.1f}pp；"
           "同一窗口内若有新主 C 首发（如耀嘉音窗口的伊芙琳、零号·安比），老主 C 的出场占比仍可下降。" if "Yixuan+Lucia" in lift and "Jane+Yuzuha" in lift else "")
        + "潘引壶与照与主 C 同期首发，没有事件前窗口，只作同期搭配对照。"
        "带新插件的样本可能集中于更投入的玩家，比值是条件关联，不是售后的因果增益；完整分层见[事件窗口表](../empirical_support_event_window.csv)。\n",
        "### 3.3 受控轨：自然寿命只记录第一次失效\n",
        "![固定主 C 的自然首次失效](figures/v2_l2_natural_km.svg)\n",
        md_table(["投资", "养成", "参考主 C 数", "首次失效事件数", "KM 中位寿命"], _l2_summary(data)),
        "首次 CI<1 为不可撤销事件，KM 曲线单调不增；窗口结束仍未失效按右删失处理。"
        f"这些曲线汇总固定合成商品的寿命，展示不同登场队列与环境路径的结果，不把 {fixed_subjects} 名角色当作独立现实玩家估计因果置信区间。\n",
        "### 3.4 受控轨：同条件配对下，支援排期决定延寿还是恢复\n",
        "![固定主 C 的早期与后期支援](figures/v2_l2_support_recovery.svg)\n",
        "下表固定早期三名主 C、0+1、持续刷盘，列出科学配置预先登记的全部五个早期配对。"
        "未配置配对的支援不算作对应售后；零延寿结果保留。\n",
        md_table(["主 C + 支援", "无支援首次失效", "有支援首次失效", "观察窗新增满星期数", "失效后首次恢复年龄"], pair_rows),
        f"DPS_03 的后期支援 S_B 到场时，首次失效已经发生：观察窗内新增 {late['incremental_full_star_periods']} 个满星期，但首次失效年龄仍为 {late['first_failure_or_censor_days']} 天。"
        f"早期 S_A 将其首次失效推迟到 {early['first_failure_or_censor_days']} 天。后期辅助的“首次失效寿命”较短，是到场时点和不可撤销事件定义的共同结果。\n",
        "完整的三档投资、两档养成和全部支援配对见[固定寿命表](../research/fixed/tables/l2_reference_lifetimes.csv)、"
        "[支援配对表](../research/fixed/tables/l2_support_pair_effects.csv)与[逐期参考面板](../research/fixed/tables/l2_reference_panel.csv)。\n",
        "### 3.5 虚拟实验：售后类型、环境与新体系换代\n",
        "现实事件里，专拐、环境变化与新角色竞争同时发生（3.2 节）。本节在一个带体系标签的合成世界里，让同一批玩家（同一种子、同一随机数流）"
        "分别经历不同的商品与环境组合，把三者拆开。世界含三个体系：大体系 L（异常型，主C在第 1、3、5、9、11、13、18 期登场，第 4 期有体系底座专拐）、"
        "小体系 S（命破型）与第 14 期起的新体系 N（锋御型）；辅助增益由体系成员关系生成，数值为情景设定。"
        "每格 N=200、复核种子 9101／9102／9103、20 期全日志，共 32 格、96 次运行。\n",
        md_table(["实验组", "因素", "水平"], [
            ("A：第 7 期上线的商品（针对大体系 L）", "商品类型", "无效对照（照常上线但无增益）／窄专拐（只对 L03 有效）／体系专拐（对全部 L 主C有效）／混合型（可当主C，也给 L 主C增益，增益高于旧专拐；坐辅助位时按份额 ρ∈{0, 0.25, 0.5, 0.75} 计入自身伤害）"),
            ("", "第 7–9 期环境", "对口 L（输出窗口机制）／非对口 L（壁垒机制）"),
            ("", "机制锁 λ", "1／2"),
            ("B：第 14–16 期新体系换代", "换代锁", "无／有（壁垒机制，压制 L、利好 N，相当于库萨里库）"),
            ("", "旧主流售后", "第 15 期给 L 出体系队友：无／有（相当于菲欧妮）"),
        ]),
        "**A 组：旧主C上场比例的拆分。** 指标为 L 旧主C（L01、L03、L05）是玩家最佳队伍主C的比例，比较第 5–6 期与第 7–9 期。"
        "朴素前后变化 = 时间趋势（无效对照＋非对口）+ 环境效应 + 商品效应（对口）。\n",
        md_table(["λ", "商品", "朴素前后变化（对口，pp）", "时间趋势", "环境效应", "商品效应·对口 [95%]", "商品效应·非对口",
                  "交互", "核心留存差·对口 [95%，pp]"], archetype_decomposition_rows(data)),
        ((f"λ=1 时，体系专拐的朴素前后变化为 {archetype_a[1.0, 'system']['naive_change_fit_pp']:+.1f}pp，"
          f"其中时间趋势 {archetype_a[1.0, 'system']['trend_pp']:+.1f}pp、环境效应 {archetype_a[1.0, 'system']['env_effect_pp']:+.1f}pp，"
          f"专拐本身只贡献 {archetype_a[1.0, 'system']['product_effect_fit_pp']:+.1f}pp；"
          f"λ=2 时环境效应升至 {archetype_a[2.0, 'system']['env_effect_pp']:+.1f}pp。"
          f"环境效应是专拐效应的 {archetype_a[1.0, 'system']['env_effect_pp'] / archetype_a[1.0, 'system']['product_effect_fit_pp']:.0f}–"
          f"{archetype_a[2.0, 'system']['env_effect_pp'] / archetype_a[2.0, 'system']['product_effect_fit_pp']:.0f} 倍，新角色竞争带来的时间趋势更大；"
          "现实事件窗口中的出场变化（如卢西娅上线后仪玄 +6.4pp）不能直接读作专拐效果。")
         if (1.0, "system") in archetype_a and (2.0, "system") in archetype_a else "")
        + "混合型的效果取决于它坐辅助位时自身还贡献多少伤害，见下文“混合型与旧主C复活”。\n",
        "**选择偏差。** 观测型比值：同一格内持有新商品与未持有者的 L03 通关指数中位数之比，对应现实的层内成绩比；"
        "实验型比值：同一批持有者在商品格与对照格的通关指数之比。\n",
        "<details>\n<summary>展开选择偏差对照</summary>\n\n",
        md_table(["λ", "商品", "环境", "观测型比值", "实验型比值", "选择偏差", "持有／未持有人数"], archetype_selection_rows(data)),
        "\n</details>\n",
        "多数格的观测型与实验型比值相同（约 1.27–1.43），只在 λ=2、非对口时出现 0.16–0.21 的偏差。"
        "模拟玩家之间只有预算带来的养成差异，没有操作水平差异，因此这是选择偏差的下限；"
        "现实中卢西娅的层内成绩比 1.42 落在模拟的纯效应范围内，现实里额外的技术与养成选择无法由本模型排除。\n",
        "**旧专拐被替代。** L 体系队伍（第 8–12 期）中新商品与旧底座专拐的使用比例：\n",
        md_table(["λ", "商品", "环境", "新商品占辅助位", "旧专拐占辅助位（对照格 → 本格）", "新商品作为主C上场", "含混合型队伍中它坐辅助位"], archetype_displacement_rows(data)),
        "**混合型与旧主C复活。** 混合型坐辅助位时，队伍伤害 = 主C伤害 + ρ × 混合型单独出战的伤害（按其自身养成与机制适配，不带支援）。"
        + ((f"ρ=0 时它只是增益更高的专拐：新角色战力更高，常常自己当主C，旧主C上场比例变化 "
            f"{span([r['product_effect_fit_pp'] for r in hybrid_a if r['co_output'] == 0])}pp（对口）。"
            f"现实中带蕾米埃尔的危局队伍有 {min(remielle_core):.1%}–{max(remielle_core):.1%} 同时含其他异常主核；"
            "模拟中含混合型的队伍里它坐辅助位的比例，"
            + "，".join(f"ρ={rho:g} 时为 " + (f"{min(v):.0%}" if f"{min(v):.0%}" == f"{max(v):.0%}" else f"{min(v):.0%}–{max(v):.0%}")
                       for rho, v in sorted(hybrid_flex.items()))
            + f"。区间触及现实的最低档是 ρ={rho_fit:g}。"
            f"在 ρ≥{rho_fit:g} 下，λ=1 时旧主C上场比例对口 {span([r['product_effect_fit_pp'] for r in fitted(hybrid_a, 1.0)])}pp、"
            f"非对口 {span([r['product_effect_unfit_pp'] for r in fitted(hybrid_a, 1.0)])}pp；"
            f"λ=2 时非对口 {span([r['product_effect_unfit_pp'] for r in fitted(hybrid_a, 2.0)])}pp，"
            f"对口 {span([r['product_effect_fit_pp'] for r in fitted(hybrid_a, 2.0)])}pp。"
            "λ=2 对口时对照格中的旧主C已接近饱和，第 9 期新 L 主C登场后混合型同样抬高新主C。"
            f"旧专拐的辅助位占比由 {min(r['control_flex_share_old_support'] for r in fitted(hybrid_d)):.0%}–"
            f"{max(r['control_flex_share_old_support'] for r in fitted(hybrid_d)):.0%} 降到 "
            f"{min(r['flex_share_old_support'] for r in fitted(hybrid_d)):.0%}–{max(r['flex_share_old_support'] for r in fitted(hybrid_d)):.0%}，"
            "方向与浮波柚叶被挤出一致；"
            f"核心留存差为 {span([r['retention_core_t20_diff_fit_pp'] for r in fitted(hybrid_a)])}pp（对口）。"
            "混合型以主C规格售出，又让玩家已有的旧主C重回场上，替代的是旧专拐而不是旧主C。")
           if rho_fit is not None and hybrid_a else "")
        + "\n",
        "**B 组：新体系换代锁与旧主流售后（λ=1）。**\n",
        md_table(["指标", "换代锁效应（无售后）[95%]", "售后效应（有换代锁）[95%]", "售后挽回比例"], archetype_b_rows(data)),
        ((f"换代锁使 L 体系第 14–16 期上场比例变化 {archetype_b['deploy_L_t14_16']['lock_effect']:+.1f}pp、"
          f"N 体系 {archetype_b['deploy_N_t14_16']['lock_effect']:+.1f}pp，留存与现金的变化区间都跨零。"
          f"旧主流售后对 L 体系上场比例的挽回点估计为 {archetype_b['deploy_L_t14_16']['recovered_share']:.0%}，但售后效应本身区间跨零；"
          "售后卡池在模拟里几乎没有直购。")
         if "deploy_L_t14_16" in archetype_b else "")
        + "现实中库萨里库房间的异常队占比从八九成降到个位数，远强于模拟：现实玩家按房间换队，而模拟每期只有一种机制环境；"
        "现实中菲欧妮的早期流水超过锋御卡池，而模拟中辅助池需求偏低（6.3 节）。B 组只用于说明换代锁改变上场结构的方向，不支持现金结论。\n",
        "实验配置、运行清单与逐运行指标见[体系实验清单](../research/archetype/experiment_manifest.json)、"
        "[拆分表](../research/archetype/tables/a_decomposition.csv)、[替代表](../research/archetype/tables/a_displacement.csv)与"
        "[换代表](../research/archetype/tables/b_generation_shift.csv)。\n",
        "## 4. 玩家行为：从卡关痛感到付费、停氪与留存（Layer 3）\n",
        "每期按资源发放 → Pre-Eval → 购买决策与逐抽 → 部分成果入库 → Post-Eval → 疲劳与流失推进。"
        "历史疲劳、连续失败与上次付费后仍失败进入续订/直购意愿；玩家可以保持免费活跃而停止付款。"
        "可负担预算与是否愿意付费分别处理，已购月卡权益留在独立库存。\n",
        "![三组情景的核心层留存](figures/v2_retention.svg)\n",
        "![基准情景的三类现金](figures/v2_cash_structure.svg)\n",
        f"λ=1、a=1 的 20 期复核平均累计现金为 **¥{cash_base['cash_yuan']:,.0f}**，核心留存 **{cash_base['retention_core']:.1%}**。"
        "核心层指初始核心付费层（Budget_40，月卡及中氪主力群），停氪仍活跃者继续计入留存；三组种子先各自按固定分母计算，再等权平均。\n",
        "现金仅包括实际续订与实际直购：本期免费、历史免费囤抽、已购月卡权益和直购抽数分别记账。"
        "新主 C 池直购含本体、影画与专武；专拐池对应支援本体。20 期为 840 天；10 期来自同一运行的前半段。\n",
        "<details>\n<summary>战斗与付费回路的 2×2 结构对照（N=200 固定种子）</summary>\n\n",
        f"固定 λ=1、a=1、种子 {meta['train_seeds'][0]}，分别替换战斗代理与付费回路。用于定位两个模块各自的贡献，不作为千人主网格的跨种子效应估计。\n",
        md_table(["期数", "战斗模块", "付费模块", "初始人数", "累计现金", "核心留存"], ablation_rows),
        "\n</details>\n",
        "## 5. 候选策略与稳健性：保留取舍，区分可辨与不可辨\n",
        "### 5.1 机制锁与专拐的跨种子配对效应\n",
        "每个种子内先匹配同一批玩家，再对差值求均值和 Student-t 逐项 95% 区间。λ 端点在五档 a 上等权平均；a 端点在五档 λ 上等权平均。\n",
        "![现金端点效应](figures/v2_axis_cash_ci.svg)\n\n![留存端点效应](figures/v2_axis_retention_ci.svg)\n",
        md_table(["期数", "用途", "端点对照", "配对种子数", "总现金相对变化 [95%区间]", "核心留存变化 [95%区间]"], _axis_rows(data)),
        "### 5.2 总现金接近中性，是否掩盖了商品之间的变化？\n",
        "![专拐轴的现金拆解](figures/v2_support_cash_decomposition.svg)\n",
        "20 期复核，a 0.8→1.6。各分项先在种子内配对，贡献统一除以该种子 a=0.8 的总现金；不是各分项自身增长率。\n",
        md_table(["现金项", "配对种子数", "现金差 [95%区间，元]", "基期总现金贡献 [95%区间，百分点]"], _cash_rows(data)),
        _direction_sentence(decreased, increased) + _crossing_sentence(crossing) +
        "三项均值与贡献可加；总区间从种子内三项之和单独估计，保留协方差，区间端点不能相加。"
        "金额均值与归一化贡献使用不同估计量，随机基期分母可使两者的区间跨零情况不同。\n",
        "### 5.3 换一批玩家与随机数，排名有多稳定？\n",
        "![现金分项与留存排名复核](figures/v2_rank_stability.svg)\n",
        md_table(["指标", "期数", "N=200，5+3 种子 ρ", "N=1000，5+3 种子 ρ"], rank_rows),
        "ρ 比较 25 个候选的筛选均值与独立复核均值排序，收入与留存分别评估。"
        "扩大人口后的排序更稳，不意味着所有配对区间都缩窄：复核仍只有三组种子，自由度为 2。"
        "不同人口的精度以相对变化或每名初始玩家的现金差比较，见[精度对照表](../research/n1000/tables/population_precision_comparison.csv)。\n",
        "### 5.4 哪些候选反复进入非支配集合？\n",
        "![各候选的独立复核前沿入选](figures/v2_frontier_inclusion.svg)\n",
        "分别以现金和核心留存为两个目标，每个复核种子单独求指定 25 个候选中的非支配集合。频率描述模型内复核结果，不将均值前沿称为全局最优。\n",
        md_table(["每次人数", "预设关注候选", "人均累计现金", "核心留存", "相对基准人均现金差 [95%区间，元]", "入选次数"], focus_rows),
        "<details>\n<summary>展开 25 个候选的 20 期独立复核成绩</summary>\n\n",
        md_table(["机制锁 λ", "专拐 a", "累计现金均值", "核心留存", "复核前沿入选"], candidate_rows),
        "\n</details>\n",
        "### 5.5 换一种同样合理的行为函数，结论还成立吗？\n",
        "主网格的扩样只降低既定行为公式下的抽样误差。本节改用三种竞争性行为结构，各自在 N=200、复核种子 9101／9102／9103、"
        "λ∈{0,1,2} × a∈{0.8,1.0,1.6} 共 9 个角点／中心点上运行，端点对照在另一轴的三档上等权平均；"
        "M0 的 27 次运行直接复用 N=200 多种子实验中配置身份一致的结果，M1／M2 新增 54 次。\n",
        md_table(["行为设定", "定义"], [
            ("M0 现行（停氪缓冲）", "连续未满星与“当期付费仍未满星”降低下期直购与月卡续订意愿；玩家可停氪但继续免费活跃"),
            ("M1 无停氪缓冲", "疲劳、连续失败与付费后仍失败对直购、续订意愿的扣减系数置零；只要在玩且有缺口，按初始预算能力付费；疲劳只驱动退坑"),
            ("M2 强追新偏好", "新主 C 池的下池效用另加不随 CI₀ 满星归零的固定项 β_novelty=1.5；初始核心付费层对已满星新主 C 池的基础下池概率由约 7.6% 升至约 26.9%"),
        ]),
        "**结论对行为假设的依赖性（20 期，3 组复核种子逐项 95% 区间）**\n",
        md_table(["待检验的商业命题", *(label for _, label in ABLATION_LABELS)], ablation_verdict_rows(data)),
        md_table(["行为设定", "λ 0→2 核心留存", "λ 0→2 现金（10 期 → 20 期）", "a 0.8→1.6 核心留存", "a 0.8→1.6 总现金",
                  "a 0.8→1.6 新主C直购贡献", "专拐直购／月卡续订贡献"], behavior_ablation_rows(data)),
        ((f"M1 与 M0 的核心层与全队列留存在 {m1_same_retention}/{m1_total} 次配对运行中完全相同，总抽数在 {m1_same_pulls}/{m1_total} 次中相同："
          "取消停氪缓冲让更多续订与直购许可通过，只有少数转化为额外抽取，阵容、疲劳与退坑没有因此改变，差异主要体现在月卡续订现金。"
          "命题 A、B、C 的方向与幅度因此不依赖停氪缓冲；现行模型中的退坑由战斗挫败驱动，停氪状态是现金通道。")
         if m1_total else "")
        + "M2 改变的是“抽新主 C 的动机”。追新驱动下，玩家即便老队满星也持续获得新主 C，"
        "机制锁造成的挫败更少转化为退坑与收入损失，λ 的 20 期现金差不再可区分；专拐对老主 C 的保值价值也不再体现为留存。"
        "a 提高后新主 C 池直购下降的点估计在三种设定下同向，M2 中因新主 C 直购基数更大，替代的共同分母贡献更大且区间不跨零。\n",
        md_table(["结论类型", "内容"], [
            ("跨设定稳健（方向）", "λ 0→2 降低核心留存：M0／M1 区间不跨零，M2 幅度约为一半、区间上界贴近 0；a 提高时新主 C 池直购下降：三者点估计同向，M0／M1 区间上界略过零；停氪缓冲不改变留存"),
            ("依赖“缺口驱动抽卡”", "λ 的长期现金负号与 a 的留存收益：只有当玩家主要因强度缺口抽新主 C 时，挫败才传导为退坑与现金损失，专拐延寿才转化为留存；外部调查中自报付费动机以角色收集为主（6.4 节）"),
            ("依赖具体参数与网格", "各端点效应的大小与候选排名；a 的总现金在 N=200 九点子网格三种设定下均为负且区间不跨零，在千人五档主网格中区间跨零"),
        ]),
        "消融表与逐运行结果见[命题对照表](../research/ablation/tables/proposition_summary.csv)、[端点效应](../research/ablation/tables/axis_effects.csv)与[运行清单](../research/ablation/experiment_manifest.json)。\n",
        "## 6. 外部校验：真实市场、全服调频统计与行为研究\n",
        "本节用三类外部数据检验 L3 模拟器的商品收入结构、抽卡结构与行为结构。它们都是聚合或横截面数据，"
        "只能校验相对量级与函数形式，不能识别单个玩家的付费因果。\n",
        "### 6.1 主C池 vs 专拐池：两个市场的卡池流水与畅销榜\n",
        f"[Gacha Revenue Tracker]({sources.get('SRC_REVENUE_TRACKER', {}).get('url', 'https://derkein.github.io/gacha-tracker-/')})"
        "的数据文件提供 40 个卡池的日本 game-i 日元估算（合计约 ¥228 亿）、中国区七麦 iPhone 逐日流水估算按卡池窗口的加总，"
        "以及中国 iPhone 畅销总榜的逐日名次（数据更新于 2026-10-30）。卡池窗口长短不一，下表同时给出总额比与日均比；"
        "“扣除地板”用当年最低日均卡池近似月卡与常驻消费，得到专拐池相对主C池的增量流水比。\n",
        md_table(["类别（2.9 节规则）", "专拐池", "强搭配的售后主核数", "参照主C池", "日本流水（专拐／主C）", "日本总额比",
                  "日本日均比", "中国 iPhone 日均比", "扣除地板后增量比", "中国 iPhone 畅销最高名次（专拐／主C）"], external_ratio_rows(data)),
        ((f"同为体系专拐，流水差距取决于上线时能“售后”多少已有主核：卢西娅、橘福福上线时命破体系只有仪玄一名已有主核，"
          f"扣除地板后的增量为 {pct([p['jp_floor_adjusted_ratio'] for p in rupture])}，中国 iPhone 畅销榜最高名次从仪玄池的第 "
          f"{min(p['reference_cn_best_rank'] for p in rupture)} 名落到第 {min(p['support_cn_best_rank'] for p in rupture)}–"
          f"{max(p['support_cn_best_rank'] for p in rupture)} 名；浮波柚叶上线时对应 {post_sale.get('Yuzuha', '—')} 名已有异常主核，"
          f"增量比为 {yuzuha['jp_floor_adjusted_ratio']:.0%}。")
         if rupture and yuzuha else "")
        + (f"南宫羽的比值（{nangong['jp_floor_adjusted_ratio']:.0%}）取决于参照池：她对照的是同期流水较低的爱芮池。" if nangong else "")
        + (f"跨体系的千夏、扳机为 {pct([p['jp_floor_adjusted_ratio'] for p in cross])}，泛用的凯撒、耀嘉音为 {pct(generic_floor)}。"
           if cross and generic_floor else "")
        + "第三方流水由 App Store／Google Play 畅销榜排名与日度权重反推，不含官方 PC 端与支付中心直充，双 UP 卡池同开期按规则切分。"
        "完整序列见[卡池流水表](../external_gacha_banner_revenue.csv)与[品类比值表](../external_banner_category_ratios.csv)。\n",
        "### 6.2 全服调频统计：出金曲线、角色池与音擎池、主C池与专拐池\n",
        "[rng.moe 全服统计](https://zzz.rng.moe/en/tracker/global)的公开接口提供 26 个限定角色池与 26 个音擎池的聚合数据"
        "（2024-07 至 2026-01；该站 2026-01-30 起维护，之后的卡池无统计），含参与人数、总抽数、小保底胜负与逐抽出金率。"
        "[StarDB Signal Tracker](https://stardb.gg/en/zzz/signal-tracker)只展示登录用户自己导入的记录，没有全服聚合，因此不提供可用数据。\n",
        "![逐抽出金率：全服统计与模拟器](figures/ext_pity_curve.svg)\n",
        pity_tail_note(data) + "\n",
        "<details>\n<summary>展开 26 个限定角色池的全服抽数</summary>\n\n",
        md_table(["UP 角色", "定位", "开池", "参与人数", "角色池总抽数", "人均抽数", "音擎÷角色抽数", "抽音擎人数占比"], rngmoe_banner_rows(data)),
        "\n</details>\n",
        "1.5 版本起角色池为新角色与复刻角色双 UP，统计按卡池合并；“定位”取新角色。贡献者是自愿导入记录的活跃玩家，抽数含免费资源。\n",
        "### 6.3 模拟器对照校验\n",
        md_table(["校验指标", "真实（外部数据）", "模拟器", "判定", "口径"], benchmark_report_rows(data)),
        "判定规则：比率类指标以模拟中位数是否落在真实四分位区间（不足 4 个值时用全距）内为准，单值指标以相对差 15% 为界。"
        "保底与 UP 机制与全服统计一致：软保底起点、逐抽斜率和小保底胜率都吻合，模拟中的抽数到角色的转换可信。"
        "收入与抽数结构有四处偏差：\n",
        "- **收入过度依赖月卡续订。** 模拟 20 期累计现金中续订约占七成，主C期现金中续订地板占比中位约八成；"
        f"真实头部主C池的地板占比不超过 {bench.get('头部主C期现金中的月卡／常驻地板占比', {}).get('real', '—').replace('≤ ', '')}。"
        "现实收入远比模拟集中在主C池直购上，模拟中“新主C池直购”通道的权重偏低。\n"
        "- **专拐池需求偏低。** 模拟专拐期的角色池抽数约为前一主C期的四分之一，真实体系专拐约为同体系头部主C池的四成多，"
        "泛用辅助约六成；扣除地板后，模拟专拐直购（中位约 5%）与只服务一名已有主核的命破专拐相当，"
        "远低于服务多名已有主核的浮波柚叶和泛用辅助。主网格中的 S_A～S_D 在经济上对应小体系专拐。\n"
        "- **音擎抽取偏多。** 模拟各预算层的目标路径都含专武，主C池音擎÷角色抽数中位约 0.42；"
        "真实约 0.29，约一半的主C池参与者不抽音擎。\n"
        "- **基础出金率略低。** 全服统计在软保底前的出金率为 0.69%／1.12%，略高于模拟采用的 0.6%／1.0%；综合出率两者均约 1.62%，对抽数到角色的转换影响很小。\n",
        "对结论的影响：a 的核心机制是专拐延寿替代新主C池直购（命题 C）。现实收入更依赖主C池直购，"
        "这一替代对总现金的权重应高于模拟，a 的总现金点估计在现实中可能更负。"
        + (f"λ 0→2 的 20 期现金变化由月卡续订 {lock_parts['subscription_yuan'] / 1e4:+.1f} 万元与新主C直购 "
           f"{lock_parts['new_main_topup_yuan'] / 1e4:+.1f} 万元构成（千人主网格复核）；在续订占比更低、主C直购占比更高的现实结构中，"
           "直购增加对续订损失的抵消更强，λ 的长期现金负号更不稳。" if lock_parts else "")
        + "两者的方向判断仍需结合 5.5 节的行为结构依赖性。\n",
        "### 6.4 行为研究与公开数据集\n",
        "行为状态机将“停氪但继续免费活跃”与“退坑”分开建模。[Fernández del Río 等（2021，arXiv:2003.10287）](https://arxiv.org/abs/2003.10287)"
        "在一款移动免费 RPG 的约 210 万玩家日志上，把登录流失（9 天未登录）与付费流失（付费玩家 50 天无购买）作为两条独立的日度序列建模，"
        "二者对抽卡活动、团战活动与节假日等协变量的响应不同；"
        "[Mustač 等（2022，Applied Sciences 12(6):2795）](https://www.mdpi.com/2076-3417/12/6/2795)在一款免费叙事手游四年的玩家记录上单独预测登录流失。"
        "两项研究支持双状态结构本身，不提供本模型的系数。\n",
        "付费动机方面，[Lakić、Bernik、Čep（2023，Information 14(7):399）](https://www.mdpi.com/2078-2489/14/7/399)对 713 名抽卡游戏玩家的调查中，"
        "612 条付费原因里 77.7% 为解锁角色／武器、39.3% 为皮肤，为更快推进 20.5%、为竞争优势仅 5.8%；53.8% 的受访者因限时角色或武器付费。"
        "自报动机以角色收集为主、强度推进为辅，更接近 M2 的追新结构而非纯缺口驱动；5.5 节中依赖“缺口驱动抽卡”的结论"
        "（λ 的长期现金负号、a 的留存收益）因此只能视为情景结论。该样本来自 Reddit 自选，“解锁角色”也无法区分喜爱与强度动机。\n",
        "公开数据集方面，[Kaggle “Mobile Game Retention & Churn Analysis”](https://www.kaggle.com/datasets/rajatsurana979/mobile-game-retention-and-churn-analysis)"
        "只有一款未具名游戏的 27,291 行日活事件，无付费字段、无采集来源，D1／D7／D30 尺度也与既有队列的 42 天期不对应，不能支撑停氪与退坑的区分；"
        "Figshare 以 gacha、gacha spending、loot box spending survey 检索，只有 Frontiers in Psychiatry 2026 青少年抽卡与问题赌博研究的补充 PDF、"
        "一篇香港问题赌博论文与开箱相关系数表，没有受访者级的抽卡付费动机数据。两者均登记为已评估、未采用。"
        "来源登记见[外部来源清单](../external_source_manifest.json)。\n",
        "## 7. 适用范围与已知限制\n",
        "- **公开样本的选择性。** 数据来自主动提交且已通关的记录，存在队伍、装备与玩家水平选择；条件成绩锚点不等于 λ 或 a 的因果估计，不能从这些通关样本识别退坑或真实收入。完整三人、合格通关、具体机制与已知成绩分母分别保留。\n"
        "- **模型映射的不确定性。** 防御结构参考 ZSim；输出覆盖、排轴、环境增长、支援净增益及适配修复均采用显式情景映射。行为权重、¥98/42 天订阅与其权益是情景设定，不代表官方月卡规格。\n"
        "- **复核与多重比较。** 三组独立复核种子的区间较粗；所有区间为逐项 95%，没有对全候选两两比较提供同时覆盖保证。主网格区间衡量随机误差；当前心理参数的扰动检验需按其科学配置另行执行，不能由扩人口替代。\n"
        "- **固定队列与规划范围。** 不含获客、回流、社交传播与运营成本；玩家只规划当前池，不预知后续排期或求解跨期最优囤抽。囤抽结果展示该局部规则下的库存变化。累计现金不是现实全盘流水、利润或完整 LTV。\n"
        "- **时间与失效定义。** 42 天为标准物理期；固定角色从登场起计龄，后期登场者随观察窗右删失。失效后恢复可用与首次失效寿命分开统计；10/20 期共享运行前缀，不是两套独立实验。\n"
        "- **真实轨迹的口径。** 出场占比是社区提交样本内的部署份额，受新角色竞争和 2.1.2 起样本量扩大的构成变化共同影响；早期主 C 左删失。0+1 只固定影画与专武，不固定等级、盘词条与技能等级。事件窗口内的成绩比是条件关联。\n"
        "- **外部数据的口径。** 卡池流水为第三方按畅销榜反推的移动端估算；全服调频统计来自自愿导入的活跃玩家，覆盖到 2026-01；"
        "两者都是聚合数据，用于校验模拟器的结构与量级，不进入参数估计。\n"
        "- **体系实验的范围。** 合成世界的体系、战力与增益为情景设定；多主核同队只在混合型坐辅助位时以副输出份额 ρ 表达，"
        "ρ 由带蕾米埃尔的队伍结构约束，没有伤害拆分数据可供估计，两名非混合型主核同队不在模型范围内；每期只有一种机制环境，按房间换队的现实行为被压缩；辅助池需求偏低使 B 组现金结论不可用。\n"
        "- **结构消融的范围。** 消融在 N=200 的 9 点子网格上进行，只有 3 组复核种子；它回答结论方向是否依赖行为结构，不重新估计主网格的效应大小。β_novelty 取单一情景值。\n",
        "## 8. 数据、配置与复现入口\n",
        md_table(["材料", "入口与用途"], [
            ("当前科学配置", "[配置快照](../research/config_science.json)；[研究入口](../../../config/research_v2/experiment.json)"),
            ("主实验", "[N1000 清单](../research/n1000/experiment_manifest.json)、[25 候选均值](../research/n1000/tables/grid_summary.csv)、[逐期结果](../research/n1000/tables/period_outcomes.csv)"),
            ("配对统计", "[端点与相邻档](../research/n1000/tables/axis_effects.csv)、[全部候选配对](../research/n1000/tables/paired_all_candidates.csv)、[现金拆解](../research/n1000/tables/axis_cash_decomposition.csv)"),
            ("扩样对照", "[N200 摘要](../research/n200/experiment_manifest.json)、[人群构成](../research/n1000/tables/population_by_seed.csv)"),
            ("公开来源", "[观测清单](../observations/observations_manifest.json)、[机制锁锚点](../empirical_knob1_boss_lock.csv)、[专拐锚点](../empirical_knob2_support_lift.csv)"),
            ("真实轨迹", "[真实主C保值轨迹](../empirical_real_dps_longevity.csv)、[售后事件窗口](../empirical_support_event_window.csv)"),
            ("行为结构消融", "[命题对照](../research/ablation/tables/proposition_summary.csv)、[消融清单](../research/ablation/experiment_manifest.json)、[M2 入口](../../../config/research_v2/experiment_m2_novelty.json)"),
            ("外部校验", "[外部来源清单](../external_source_manifest.json)、[40 卡池流水](../external_gacha_banner_revenue.csv)、[品类比值](../external_banner_category_ratios.csv)、"
             "[全服卡池抽数](../external_rngmoe_banner_stats.csv)、[逐抽出金率](../external_rngmoe_pity_curve.csv)、[模拟器对照](../external_sim_benchmarks.csv)"),
            ("研究边界与审阅", f"[审核入口]({guide})、[实验协议](../../../docs/development/research_v2_stability_protocol.md)"),
            ("旧版对照", "[V1 基线报告](BASELINE_REPORT.md)；其科学配置与当前研究分开"),
        ]),
        f"科学配置身份：`{meta['base_config_id']}`。主网格共 {meta['completed_runs']} 个 20 期运行，摘要日志；10 期从现有库截取。"
        "轻量视图重绘只读取发布目录内的 CSV、汇总 JSON、清洗维表和 SVG，不依赖 SQLite 或原始快照，也不重新模拟或导入公开数据。\n",
        "```powershell\n"
        ".\\.venv\\Scripts\\python.exe -m zzz_research report --dir outputs\\final_release\n"
        ".\\.venv\\Scripts\\python.exe -m zzz_research app --dir outputs\\final_release\n"
        ".\\.venv\\Scripts\\python.exe -m pytest -q\n"
        "```\n",
        "基准素材重提使用三个源实验目录的 CSV/JSON/SVG，并读取千人基准运行 SQLite 中记录的科学配置快照；它不重新模拟，也不需要 Hugging Face 原始快照。\n\n"
        "```powershell\n.\\.venv\\Scripts\\python.exe tools\\rebuild_final_release.py\n```\n",
        "完整重算采用独立目录；有相同科学身份的完成运行会复用：\n\n"
        "```powershell\n.\\.venv\\Scripts\\python.exe tools\\run_research_v2_stability.py --players 1000 --workers 4 --out outputs\\research_v2_stability_n1000\n```\n",
        "真实主 C 轨迹与事件窗口只读观测库；行为结构消融复用 N=200 的 M0 运行并新增 M1／M2：\n\n"
        "```powershell\n.\\.venv\\Scripts\\python.exe tools\\build_empirical_longevity.py --dir outputs\\final_release\n"
        ".\\.venv\\Scripts\\python.exe tools\\build_external_benchmarks.py --dir outputs\\final_release\n"
        ".\\.venv\\Scripts\\python.exe tools\\run_behavior_ablation.py --workers 6\n```\n",
        "辅助体系归属只读观测库；体系实验为每格生成独立配置并开全日志运行：\n\n"
        "```powershell\n.\\.venv\\Scripts\\python.exe tools\\build_support_archetypes.py --dir outputs\\final_release\n"
        ".\\.venv\\Scripts\\python.exe tools\\run_archetype_experiment.py --workers 6\n"
        ".\\.venv\\Scripts\\python.exe tools\\analyze_archetype_experiment.py\n```\n",
    ]
    return "\n".join(parts)


def write_publication_report(out, data):
    out = Path(out)
    figures = {**make_empirical_figures(out, data), **make_external_figures(out, data),
               **make_l2_figures(out, data), **make_behavior_figures(out, data)}
    report = out / "report/REPORT.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    body = render_publication_report(out, data)
    report.write_text(body, encoding="utf-8")

    def rebase(match):
        label, url = match.group(1), match.group(2)
        if re.match(r"[a-z]+:", url) or url.startswith("#"):
            return match.group(0)
        target = report.parent / url
        return f"[{label}]({Path(os.path.relpath(target, out)).as_posix()})"

    root_body = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", rebase, body)
    (out / "REPORT.md").write_text(root_body, encoding="utf-8")
    return {"report": str(report), "figures": [str(p) for p in figures.values()], "status": "completed"}


def build_publication_report(out_dir):
    from .publication_data import load_publication_data
    out = Path(out_dir)
    return write_publication_report(out, load_publication_data(out))
