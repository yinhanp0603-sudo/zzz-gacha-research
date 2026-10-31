"""基于已完成导出表的看板图表。"""

from __future__ import annotations

import html
import math
from collections import defaultdict
from pathlib import Path
from typing import Mapping, Sequence

from zzz_research.report import fnum, read_csv, reference_records, search_records, stage_usable

SCENARIO_NAMES = {
    "Ref_HP": "粗暴加血流(Ref_HP)",
    "Ref_Mechanism": "广义机制换锁流(Ref_Mechanism)",
    "Ref_Support": "专拐保值流(Ref_Support)",
}
SUPPORT_NAMES = {
    "none": "无限定支援",
    "join_on_debut:S_A": "配早期支援(S_A)",
    "join_on_debut:S_B": "配后期强支援(S_B)",
}
COHORT_NAMES = {
    "avail_rate_active": "在玩持有者满星率",
    "cohort_active_clear_share": "全队列存续满星占比",
    "avail_rate_counterfactual_frozen": "退坑冻结配置反事实满星率",
    "churned_holder_share": "持有者累计退坑率",
}
COLORS = ("#2563eb", "#d28a22", "#658143", "#b3537b", "#6b7280")
FONT = "'Microsoft YaHei','Noto Sans CJK SC',system-ui,sans-serif"


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _num(value: object) -> float:
    return fnum(value) or 0.0


def _frame(title: str, height: int, subtitle: str = "") -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 {height}" '
        f'role="img" aria-label="{_esc(title)}" font-family="{FONT}">',
        f'<title>{_esc(title)}</title>',
        f'<text x="22" y="28" font-size="17" font-weight="600" fill="#18263a">{_esc(title)}</text>',
        f'<text x="22" y="50" font-size="11" fill="#64748b">{_esc(subtitle)}</text>',
    ]


def _ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next((n * mag for n in (1, 2, 2.5, 5, 10) if n * mag >= raw), raw)
    first = math.ceil(lo / step) * step
    return [first + i * step for i in range(count + 2) if first + i * step <= hi + step * 1e-6]


def _line(
    series: Mapping[str, Sequence[tuple[float, float | None]]],
    *, title: str, subtitle: str = "", unit: str = "", percent: bool = False,
    step: bool = False, minimum: float | None = None, maximum: float | None = None,
    threshold: float | None = None, xlabel: str = "模拟区间（每期 42 天）",
) -> str:
    available = [(x, y) for points in series.values() for x, y in points if y is not None]
    if not available:
        return ""
    xs, ys = zip(*available)
    x0, x1 = min(xs), max(xs)
    y0 = min(0.0, min(ys)) if minimum is None else minimum
    y1 = (max(ys) * 1.1 if max(ys) > 0 else 1.0) if maximum is None else maximum
    if y1 <= y0:
        y1 = y0 + 1.0
    left, right, top, bottom = 78, 616, 76, 260
    legend_rows = math.ceil(len(series) / 2)
    height = 320 + legend_rows * 25
    X = lambda x: left + (x - x0) / (x1 - x0 or 1.0) * (right - left)
    Y = lambda y: bottom - (y - y0) / (y1 - y0) * (bottom - top)
    o = _frame(title, height, subtitle)
    label = (lambda y: f"{y:.0%}") if percent else (lambda y: f"¥{y:,.0f}" if unit == "元" else f"{y:,.2f}".rstrip("0").rstrip("."))
    for y in _ticks(y0, y1):
        o += [f'<line x1="{left}" x2="{right}" y1="{Y(y):.2f}" y2="{Y(y):.2f}" stroke="#e5eaf0"/>',
              f'<text x="{left-9}" y="{Y(y)+4:.2f}" font-size="11" text-anchor="end" fill="#64748b">{label(y)}</text>']
    xticks = sorted(set((x0, x1, *_ticks(x0, x1, 5))))
    for x in xticks:
        o.append(f'<text x="{X(x):.2f}" y="{bottom+22}" font-size="11" text-anchor="middle" fill="#64748b">{x:g}</text>')
    o.append(f'<line x1="{left}" x2="{right}" y1="{bottom}" y2="{bottom}" stroke="#9aa9ba"/>')
    o.append(f'<text x="{(left+right)/2}" y="{bottom+45}" font-size="11" text-anchor="middle" fill="#64748b">{_esc(xlabel)}</text>')
    if threshold is not None and y0 <= threshold <= y1:
        o.append(f'<line x1="{left}" x2="{right}" y1="{Y(threshold):.2f}" y2="{Y(threshold):.2f}" stroke="#94a3b8" stroke-dasharray="4 4"/>')
    for idx, (name, points) in enumerate(series.items()):
        color = COLORS[idx % len(COLORS)]
        previous = None
        parts = []
        for x, y in sorted(points, key=lambda pair: pair[0]):
            if y is None:
                previous = None
                continue
            if previous is None:
                parts.append(f'M{X(x):.2f},{Y(y):.2f}')
            elif step:
                parts.append(f'H{X(x):.2f}V{Y(y):.2f}')
            else:
                parts.append(f'L{X(x):.2f},{Y(y):.2f}')
            previous = y
        o.append(f'<path d="{" ".join(parts)}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for x, y in points:
            if y is not None:
                o.append(f'<circle cx="{X(x):.2f}" cy="{Y(y):.2f}" r="3" fill="{color}"><title>{_esc(name)} · {x:g}：{label(y)}{_esc(unit if unit != "元" else "")}</title></circle>')
        lx, ly = (24 if idx % 2 == 0 else 330), 322 + (idx // 2) * 25
        o += [f'<line x1="{lx}" x2="{lx+17}" y1="{ly-4}" y2="{ly-4}" stroke="{color}" stroke-width="3"/>',
              f'<text x="{lx+24}" y="{ly}" font-size="11" fill="#334155">{_esc(name)}</text>']
    o.append('</svg>')
    return "".join(o)


def combat_charts(out: Path | str) -> dict[str, str]:
    """返回固定条件配对、首次失效 KM 与参考适用性图。"""
    out = Path(out)
    if not stage_usable(out, "build_benchmarks"):
        return {}
    result: dict[str, str] = {}
    paired = read_csv(out / "benchmarks" / "benchmark_pairs.csv")
    values: dict[str, dict[float, list[float]]] = defaultdict(lambda: defaultdict(list))
    names = {"support:S_A": "增加早期支援(S_A)", "support:S_B": "增加后期强支援(S_B)",
             "kappa:k=0.7": "避盾爆发（κ=0.7）", "kappa:k=1.3": "盾内爆发（κ=1.3）"}
    for row in paired:
        key = row["changed_factor"] + ":" + row["alt_label"].split("|")[-1]
        values[names.get(key, "其他配对")][float(row["period_id"].lstrip("t"))].append(_num(row["abs_diff"]))
    result["benchmark_pairs"] = _line(
        {name: [(t, sum(v) / len(v)) for t, v in sorted(points.items())] for name, points in sorted(values.items())},
        title="固定条件配对：战力增量", subtitle="仅改变支援或爆发对齐；支援比较包含额外资产", unit=" CI", threshold=0.0,
    )
    km = read_csv(out / "benchmarks" / "km_steps.csv")
    series: dict[str, list] = {}
    for row in km:
        if row["investment_plan"] == "0+1" and row["gear_policy"] == "growth_from_debut":
            name = SUPPORT_NAMES.get(row["support_policy"], row["support_policy"])
            series.setdefault(name, [(0.0, 1.0)]).append((_num(row["age_days"]), fnum(row["survival"])))
    result["km_first_failure"] = _line(series, title="首次失效：满星中位寿命", subtitle="0+1 · 持续刷盘养成(+16%) · 首次低于满星线即计事件",
                                         percent=True, step=True, minimum=0, maximum=1, threshold=0.5, xlabel="出道后天数")
    trajectories = [r for r in read_csv(out / "benchmarks" / "reference_trajectories.csv")
                    if r["plan"] == "0+1" and r["gear_policy"] == "growth_from_debut" and r["support_policy"] == "none"]
    if trajectories:
        anchors = sorted({r["anchor"] for r in trajectories})
        periods = sorted({int(r["t_idx"]) for r in trajectories})
        vals = {(r["anchor"], int(r["t_idx"])): fnum(r["ci"]) for r in trajectories}
        h = 110 + len(anchors) * 30
        o = _frame("参考主 C 的版本适用性", h, "0+1 · 持续刷盘养成(+16%) · 无限定支援；数字为代理战力指数")
        cellw = 490 / len(periods)
        for j, period in enumerate(periods):
            o.append(f'<text x="{125+(j+.5)*cellw:.1f}" y="78" font-size="11" text-anchor="middle" fill="#64748b">第{period}期</text>')
        for i, anchor in enumerate(anchors):
            yy = 88 + i * 30
            display = "主 C " + anchor.removeprefix("DPS_")
            o.append(f'<text x="112" y="{yy+19}" text-anchor="end" font-size="12" fill="#334155">{_esc(display)}</text>')
            for j, period in enumerate(periods):
                val = vals.get((anchor, period))
                fill = "#eef2f6" if val is None else ("#deebff" if val >= 1 else "#fbe8db")
                text = "—" if val is None else f"{val:.2f}"
                o += [f'<rect x="{125+j*cellw:.1f}" y="{yy}" width="{cellw-2:.1f}" height="27" rx="3" fill="{fill}"/>',
                      f'<text x="{125+(j+.5)*cellw:.1f}" y="{yy+18}" font-size="11" text-anchor="middle" fill="#334155">{text}</text>']
        o.append(f'<text x="24" y="{h-6}" font-size="11" fill="#64748b">蓝：指数≥1；橙：指数&lt;1；灰：尚未出道。模型达标代理，非实测满星率。</text></svg>')
        result["reference_applicability"] = "".join(o)
    return {key: value for key, value in result.items() if value}


def behavior_charts(out: Path | str) -> dict:
    """返回三情景总览及各情景的四来源、现金与队列曲线。"""
    out = Path(out)
    retention: dict[str, list] = {}
    cash: dict[str, list] = {}
    scenarios: dict[str, dict] = {}
    refs = reference_records(out)
    structures = {ref["scenario"]: read_csv(out / "tables" / f"{Path(ref['path']).stem}_revenue_structure.csv") for ref in refs}
    source_max = max((_num(row[key]) for rows in structures.values() for row in rows
                      for key in ("curr_free_used", "hoarded_free_used", "sub_pulls_used", "topup_pulls_used")), default=1.0)
    cash_max = max((_num(row[key]) / 100 for rows in structures.values() for row in rows
                    for key in ("sub_revenue_fen", "topup_revenue_fen")), default=1.0)
    for ref in refs:
        scenario = ref["scenario"]
        tag = Path(ref["path"]).stem
        label = SCENARIO_NAMES.get(scenario, scenario)
        rc = read_csv(out / "tables" / f"{tag}_retention_cash.csv")
        retention[label] = [(_num(r["t_idx"]), fnum(r["retention_end"])) for r in rc]
        cash[label] = [(_num(r["t_idx"]), _num(r["cum_cash_per_initial_fen"]) / 100) for r in rc]
        rs = structures[scenario]
        sources = {lab: [(_num(r["t_idx"]), fnum(r[key])) for r in rs] for key, lab in (
            ("curr_free_used", "本期免费抽"), ("hoarded_free_used", "历史囤积免费抽"),
            ("sub_pulls_used", "订阅权益抽"), ("topup_pulls_used", "直购抽"))}
        money = {lab: [(_num(r["t_idx"]), _num(r[key]) / 100) for r in rs] for key, lab in (
            ("sub_revenue_fen", "订阅现金"), ("topup_revenue_fen", "按需直购现金"))}
        charts = {"sources": _line(sources, title="四来源抽数", subtitle=f"模拟 · {label} · 当期实际消耗 · 三情景同刻度", unit="抽", maximum=source_max * 1.1),
                  "money": _line(money, title="订阅与直购现金", subtitle=f"模拟 · {label} · 当期现金收入 · 三情景同刻度", unit="元", maximum=cash_max * 1.1)}
        coh = read_csv(out / "tables" / f"{tag}_cohort_availability.csv")
        if coh:
            sizes = {(r["unit_id"], r["acquisition_period"]): int(r["n"]) for r in coh}
            unit, period = max(sizes, key=lambda key: (sizes[key], key))
            rows = [r for r in coh if r["unit_id"] == unit and r["acquisition_period"] == period]
            series = {lab: [(_num(r["t_idx"]), fnum(r[key])) for r in rows] for key, lab in COHORT_NAMES.items()}
            charts["cohort"] = _line(series, title="固定获取队列的四种口径", subtitle=f"模拟 · 第{period}期获取主 C {unit.removeprefix('DPS_')} · N={sizes[(unit, period)]}",
                                      percent=True, minimum=0, maximum=1)
        scenarios[scenario] = charts
    return {"retention": _line(retention, title="三情景期末留存", subtitle="模拟 · 分母为初始全体玩家，区别于首屏核心预算层", percent=True, minimum=0, maximum=1),
            "cash": _line(cash, title="三情景累计人均现金", subtitle="模拟 · 分母固定为初始玩家人数", unit="元"),
            "scenarios": scenarios}


def candidate_chart(out: Path | str) -> str:
    """候选现金与核心预算层留存；均值按筛选、独立复核分列。"""
    rows = [r for r in search_records(Path(out), "seed_robustness.csv") if r.get("perturbation_id") == "P0"]
    if not rows:
        return ""
    xs = [_num(r["revenue_mean"]) / 100 for r in rows]
    ys = [_num(r["retention_mean"]) for r in rows]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    padx = (x1 - x0) * .12 or 1
    pady = (y1 - y0) * .15 or .05
    x0, x1 = x0 - padx, x1 + padx
    y0, y1 = max(0, y0 - pady), min(1, y1 + pady)
    X = lambda x: 85 + (x - x0) / (x1 - x0) * 522
    Y = lambda y: 280 - (y - y0) / (y1 - y0) * 200
    o = _frame("收入与核心预算层留存", 370, "模拟 · 25 个候选配置 · 跨种子均值 · 坐标聚焦候选范围")
    for y in _ticks(y0, y1):
        o += [f'<line x1="85" x2="607" y1="{Y(y):.2f}" y2="{Y(y):.2f}" stroke="#e5eaf0"/>',
              f'<text x="75" y="{Y(y)+4:.2f}" text-anchor="end" font-size="11" fill="#64748b">{y:.0%}</text>']
    for x in _ticks(x0, x1):
        o.append(f'<text x="{X(x):.2f}" y="303" text-anchor="middle" font-size="11" fill="#64748b">¥{x:,.0f}</text>')
    for row in rows:
        val = row["split"] == "validation"
        color = COLORS[0] if val else COLORS[1]
        x, y = _num(row["revenue_mean"]) / 100, _num(row["retention_mean"])
        candidate = row["candidate_id"].replace("L", "机制 λ=").replace("_A", " / 支援增益=")
        o.append(f'<circle cx="{X(x):.2f}" cy="{Y(y):.2f}" r="4.5" fill="{color}" fill-opacity=".8"><title>{_esc(candidate)} · {"独立复核" if val else "筛选"} · 现金¥{x:,.0f} · 留存{y:.2%} · 入选频率{_num(row["inclusion_frequency"]):.2%}</title></circle>')
    o += ['<text x="346" y="330" text-anchor="middle" font-size="12" fill="#64748b">累计现金（元） · 纵轴为核心预算层期末留存</text>',
          f'<circle cx="220" cy="352" r="4" fill="{COLORS[1]}"/><text x="232" y="356" font-size="12" fill="#334155">筛选种子均值</text>',
          f'<circle cx="395" cy="352" r="4" fill="{COLORS[0]}"/><text x="407" y="356" font-size="12" fill="#334155">独立复核均值</text>', '</svg>']
    return "".join(o)
