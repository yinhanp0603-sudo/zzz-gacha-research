"""本地看板的业务标签、格式和已导出结果摘要。"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from zzz_research.analytics.insights import axis_paired_effects, largest_step
from zzz_research.report import (fnum, observations_available, read_csv, read_json,
                                 reference_records, search_records, stage_usable)


LABELS = {
    "fixed_G1": "初始面板(不刷盘)",
    "growth_from_debut": "持续刷盘养成(+16%)",
    "none": "无限定支援", "NONE": "无限定支援",
    "join_on_debut:S_A": "配早期支援(S_A)",
    "join_on_debut:S_B": "配后期强支援(S_B)",
    "Ref_HP": "粗暴加血流(Ref_HP)",
    "Ref_Mechanism": "广义机制换锁流(Ref_Mechanism)",
    "Ref_Support": "专拐保值流(Ref_Support)",
    "avail_rate_active": "在玩持有者满星率",
    "cohort_active_clear_share": "全队列存续满星占比",
    "avail_rate_itt_zero": "全队列存续满星占比",
    "counterfactual_frozen": "退坑冻结配置反事实满星率",
    "avail_rate_counterfactual_frozen": "退坑冻结配置反事实满星率",
    "churned_holder_share": "持有者累计退坑率",
    "shiyu_defense": "式舆防卫战", "deadly_assault": "危局强袭战",
    "train": "筛选种子", "validation": "独立复核种子",
    "train_mean_vs_validation_mean": "筛选均值与独立复核均值",
    "validation_vs_P0_validation": "扰动复核与基准复核",
    "time": "用时制", "score": "得分制", "ok": "正常",
    "small_sample": "小样本", "MedianNotReached": "观察期内未达中位数",
    "P0": "基准配置", "reduced_scale": "缩减规模", "completed": "已完成",
    "gap_weight": "通关缺口痛感权重", "failure_weight": "连续未满星痛感权重",
    "budget_weight": "直购预算痛感权重", "Core Carry": "主输出核心",
    "Core Engine": "体系引擎", "Carry / Engine": "主输出兼体系引擎",
    "Flex / Support": "泛用插件/辅助",
}

RATE_KEYS = {
    "core_retention_end", "retention_end_all", "retention_mean", "retention_sd",
    "retention_min", "retention_max", "inclusion_frequency", "nondominated_freq",
    "rel_change_revenue_vs_P0", "rel_change_retention_vs_P0", "appearance_player_share",
    "appearance_per_clear", "S_at_last", "mean_p_churn", "mean_budget_utilization",
    "retention_change_mean", "retention_change_min", "retention_change_max",
    "revenue_change_mean", "revenue_change_min", "revenue_change_max",
    "mean_ret", "mean_delta_ret", "active_share", "full_star_share",
    "share", "peak_player_share", "clear_share", "retention_start", "retention_end",
    "support_given_core", "core_given_support", "validation_inclusion_frequency",
    "avail_rate_active", "cohort_active_clear_share", "avail_rate_itt_zero",
    "counterfactual_frozen", "avail_rate_counterfactual_frozen", "churned_holder_share",
}
MONEY_KEYS = {
    "total_revenue_fen", "revenue_mean", "revenue_sd", "revenue_min", "revenue_max",
    "delta_revenue_mean", "delta_revenue_sd", "delta_revenue_min", "delta_revenue_max",
    "mean_delta_rev_fen", "mean_rev_fen", "sd_delta_rev_fen",
    "sub_revenue_fen", "topup_revenue_fen", "cash_fen", "cumulative_cash_fen",
}


def label(value: Any) -> str:
    """把展示值转换为业务名称，保留必要的情景与投资标识。"""
    txt = str(value) if value is not None else "—"
    if txt in LABELS:
        return LABELS[txt]
    match = re.fullmatch(r"(?:behavior\.fatigue\.)?(gap_weight|failure_weight|budget_weight)(?:_x([0-9.]+))?", txt)
    if match:
        name = LABELS[match[1]]
        return f"{name} ×{float(match[2]):g}" if match[2] else name
    candidate = re.fullmatch(r"L([0-9.]+)_A([0-9.]+)", txt)
    if candidate:
        return f"机制 λ={float(candidate[1]):g} · 支援 a={float(candidate[2]):g}"
    return txt


def display(key: str, value: Any) -> str:
    """金额统一为元，比例统一为百分比；缺失值保持缺失。"""
    if value in (None, "", "None", "NULL", "null"):
        return "—"
    if value == "MedianNotReached":
        return label(value)
    num = fnum(value)
    if num is None:
        return label(value)
    if key in MONEY_KEYS or key.endswith("_fen"):
        return f"¥{num / 100:,.2f}"
    if key in RATE_KEYS or key.endswith("_rate") or key.endswith("_share"):
        return f"{num:.2%}"
    if key.startswith("rho_"):
        return f"{num:.3f}"
    if num.is_integer():
        return f"{num:,.0f}"
    return f"{num:,.3f}".rstrip("0").rstrip(".")


def _csv(path: Path) -> list[dict]:
    return [{k.lstrip("\ufeff"): v for k, v in r.items()} for r in read_csv(path)]


def _season(value: str) -> tuple[int, ...]:
    return tuple(int(p) for p in value.split("."))


def coverage_rows(out: Path | str) -> list[dict]:
    """仅保留合格通关大于零的关卡，覆盖用时制七层及得分制终局层。"""
    out = Path(out)
    if not observations_available(out):
        return []
    rows = [r for r in _csv(out / "observations" / "obs_coverage.csv")
            if (fnum(r.get("eligible_records")) or 0) > 0]
    return sorted(rows, key=lambda r: (_season(r["combat_season"]), r["mode"], r["stage_id"]), reverse=True)


def appearance_rows(out: Path | str) -> list[dict]:
    """展示已确认代理人的赛季出场数据；原始标识保留用于中英文搜索。"""
    out = Path(out)
    if not observations_available(out):
        return []
    dim = {r["unit_id"]: r for r in _csv(out / "dim_observed_archetype.csv")}
    if not dim:
        return []
    rows = []
    for r in _csv(out / "observations" / "appearance.csv"):
        unit = dim.get(r.get("unit_id"))
        if unit is None:
            continue
        rows.append({**r, "name_zh": unit["name_zh"], "anchor_role": unit["anchor_role"],
                     "specialty_zh": unit["specialty_zh"], "mechanism_family": unit["mechanism_family"]})
    return sorted(rows, key=lambda r: (_season(r["combat_season"]), fnum(r["players"]) or 0), reverse=True)


def perturbation_data(out: Path | str) -> dict:
    """六种扰动各自汇总全部候选，详情不截断候选行。"""
    rows = search_records(Path(out), "parameter_sensitivity.csv")
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["perturbation_id"]].append(row)
    order = {"failure_weight": 0, "gap_weight": 1, "budget_weight": 2}
    keys = sorted(grouped, key=lambda key: (order.get(key.split("_x")[0], 3), key))
    summary = []
    for key in keys:
        group = grouped[key]
        rev = [fnum(r.get("rel_change_revenue_vs_P0")) for r in group]
        ret = [fnum(r.get("rel_change_retention_vs_P0")) for r in group]
        rev = [v for v in rev if v is not None]
        ret = [v for v in ret if v is not None]
        summary.append({"perturbation_id": key, "n_candidates": len(group),
                        "revenue_change_mean": sum(rev) / len(rev) if rev else None,
                        "revenue_change_min": min(rev) if rev else None,
                        "revenue_change_max": max(rev) if rev else None,
                        "retention_change_mean": sum(ret) / len(ret) if ret else None,
                        "retention_change_min": min(ret) if ret else None,
                        "retention_change_max": max(ret) if ret else None,
                        "changed_candidates": sum((fnum(r.get("rel_change_revenue_vs_P0")) or 0) != 0
                                                  or (fnum(r.get("rel_change_retention_vs_P0")) or 0) != 0
                                                  for r in group)})
    return {"summary": summary, "details": {key: grouped[key] for key in keys}, "types": keys}


def kpi_data(out: Path | str) -> list[dict]:
    """四项首屏指标均从已完成产物推导；未完成阶段显示未运行。"""
    out = Path(out)
    cards = []
    evolution = (read_json(out / "observations" / "archetype_evolution_manifest.json") or {}) if observations_available(out) else {}
    n = evolution.get("valid_3man_records")
    units = _csv(out / "dim_observed_archetype.csv")
    seasons = evolution.get("season_range", [])
    cards.append({"title": "真实通关样本库", "value": f"{n / 10000:,.1f} 万条" if n else "尚未导入",
                  "detail": f"覆盖 {'–'.join(seasons)} · {evolution.get('n_seasons', 0)} 个赛季 · {len(units)} 名代理人" if n else "未导入公开战斗记录",
                  "note": "合法完整三人通关记录；非去重玩家人数。",
                  "source": "observations/archetype_evolution_manifest.json"})
    km = read_csv(out / "benchmarks" / "km_first_failure.csv") if stage_usable(out, "build_benchmarks") else []
    low = [r for r in km if r.get("gear_policy") == "growth_from_debut" and r.get("investment_plan") in ("0+0", "0+1")]
    baselines = {r["investment_plan"]: fnum(r.get("median_age_days")) for r in low if r.get("support_policy") == "none"}
    ratios, days = [], []
    for row in low:
        base, age = baselines.get(row["investment_plan"]), fnum(row.get("median_age_days"))
        if row.get("support_policy") != "none" and base and age is not None:
            ratios.append(age / base)
            days.append(age)
    cards.append({"title": "限定支援延寿杠杆", "value": f"{min(ratios):.1f}× – {max(ratios):.1f}×" if ratios else "尚未运行",
                  "detail": f"0+0 / 0+1 持续刷盘：配支援后中位寿命 {min(days):.0f}–{max(days):.0f} 天" if days else "缺少固定队伍基准结果",
                  "note": "模拟首次失效中位寿命；同时受支援增益和加入时点影响。",
                  "source": "benchmarks/km_first_failure.csv"})
    refs = {r["scenario"]: r for r in reference_records(out)}
    support, mechanism, hp = (refs.get(k) for k in ("Ref_Support", "Ref_Mechanism", "Ref_HP"))
    ready = support and mechanism and hp
    cards.append({"title": "专拐保值流 · 参照情景", "value": f"留存 {support['core_retention_end']:.1%}" if support else "尚未运行",
                  "detail": f"较广义机制换锁流：现金 +{(support['total_revenue_fen'] / mechanism['total_revenue_fen'] - 1):.1%}" if ready else "缺少完整参照情景",
                  "note": f"对比粗暴加血流：核心层留存 +{(support['core_retention_end'] - hp['core_retention_end']) * 100:.1f}pp，现金 +{(support['total_revenue_fen'] / hp['total_revenue_fen'] - 1):.1%}。" if ready else "留存口径为核心预算层期末留存。",
                  "source": "reference_runs.json"})
    levels = axis_paired_effects(search_records(out, "search_results.csv"), axis=0, split="train")
    cliff = largest_step(levels, "mean_ret")
    cards.append({"title": "机制强度 · 留存陡降区间", "value": f"λ = {cliff[0]:g} → {cliff[1]:g}" if cliff else "尚未运行",
                  "detail": f"核心层留存单步 {cliff[2] * 100:+.1f}pp" if cliff else "缺少候选网格搜索结果",
                  "note": f"筛选种子、对支援强度取平均；降幅为其余相邻步均值的 {abs(cliff[2]) / cliff[3]:.1f} 倍。" if cliff and cliff[3] else "结果仅适用于指定行为假设。",
                  "source": "search/search_results.csv"})
    return cards


def budget_effect_data(out: Path | str) -> dict:
    """预算扰动解释的已观测人期分组，不把人期误作独立玩家人数。"""
    out = Path(out)
    ref = next((r for r in reference_records(out) if r["scenario"] == "Ref_Mechanism"), None)
    if not ref:
        return {}
    rows = read_csv(out / "tables" / f"Ref_Mechanism_seed{ref['seed']}_budget_fatigue_cross.csv")
    budget = [r for r in rows if fnum(r.get("budget_used")) == 1]
    total = sum(fnum(r.get("player_periods")) or 0 for r in budget)
    cleared = sum(fnum(r.get("player_periods")) or 0 for r in budget if fnum(r.get("full_star_post")) == 1)
    zero = sum(fnum(r.get("fatigue_after_zero")) or 0 for r in budget)
    return {"topup_player_periods": total, "full_star_player_periods": cleared,
            "full_star_rate": cleared / total if total else None, "fatigue_after_zero_periods": zero,
            "source": f"tables/Ref_Mechanism_seed{ref['seed']}_budget_fatigue_cross.csv"}
