"""M-PRESENT 14.1/14.2：只消费已完成且通过校验的产物，生成 REPORT.md 与 SVG 图（无第三方依赖）。

不计算新的出货、战斗或收入；未执行的阶段在报告中明确标注，不空造结果。
所有 Layer 3 图标题包含“模拟/假设情景”。阈值 1.0（模型达标）与舒适线使用不同线型。
"""

from __future__ import annotations

import csv
import html
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

REPORT_VERSION = "report_v3"
FONT = "'Noto Sans CJK SC','Microsoft YaHei','PingFang SC','Source Han Sans SC',sans-serif"
PALETTE = ["#2563eb", "#d97706", "#059669", "#dc2626", "#7c3aed", "#0891b2", "#be185d", "#4b5563"]
INK, MUTED, GRID = "#111827", "#6b7280", "#e5e7eb"


# =========================================================================== 读取


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def stage_usable(out: Path, name: str) -> bool:
    """有明确失败/运行中状态时拒绝旧产物；兼容独立命令产生的产物。"""
    stages = (read_json(out / "pipeline_manifest.json") or {}).get("stages", {})
    return name not in stages or stages[name].get("status") == "completed"


def search_metadata(out: Path) -> Optional[dict]:
    return read_json(out / "search" / "experiment_manifest.json") if stage_usable(out, "search") else None


def search_records(out: Path, filename: str) -> list[dict]:
    meta = search_metadata(out)
    if not meta:
        return []
    return [r for r in read_csv(out / "search" / filename) if r.get("experiment_id") == meta.get("experiment_id")]


def observations_available(out: Path) -> bool:
    meta = read_json(out / "observations" / "observations_manifest.json") or {}
    return (stage_usable(out, "prepare_observations")
            and meta.get("status") in ("imported", "imported_with_issues")
            and meta.get("observed_data_available") is True)


def reference_records(out: Path) -> list[dict]:
    return [r for r in (read_json(out / "reference_runs.json") or [])
            if stage_usable(out, f"simulate_{r['scenario']}")
            and stage_usable(out, f"aggregate_{r['scenario']}")]


def database_availability_note(out: Path) -> str:
    meta = search_metadata(out) or {}
    expected = (meta.get("summary") or {}).get("unique_runs", 0)
    present = len(list((out / "search" / "runs").glob("*.sqlite")))
    missing_refs = sum(not Path(r.get("path", "")).is_file()
                       and not (out / "reference" / Path(r.get("path", "")).name).is_file()
                       for r in reference_records(out))
    if (expected and present < expected) or missing_refs:
        return ("本目录缺少部分或全部逐run数据库：当前可复核导出表及其计算关系，"
                "无法在本机逐库复查运行完成状态、外键和完整事件。阶段completed来自随包清单，"
                "不等于本机已复查原始运行库；重跑需要原数据库或重新计算。")
    return ""


def fnum(x: Any) -> Optional[float]:
    if x in (None, "", "None"):
        return None
    try:
        v = float(x)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


# =========================================================================== SVG


def _esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    start = math.floor(lo / step) * step
    out, v = [], start
    while v <= hi + step * 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def _fmt(v: float) -> str:
    if abs(v) >= 1e6:
        return f"{v / 1e6:g}M"
    if abs(v) >= 1e4:
        return f"{v / 1e3:g}k"
    return f"{v:g}"


def _frame(w: int, h: int, title: str, subtitle: str = "") -> list[str]:
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
           f'font-family="{FONT}" role="img" aria-label="{_esc(title)}">',
           f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
           f'<text x="16" y="24" font-size="15" font-weight="600" fill="{INK}">{_esc(title)}</text>']
    if subtitle:
        out.append(f'<text x="16" y="42" font-size="11" fill="{MUTED}">{_esc(subtitle)}</text>')
    return out


def line_chart(series: Mapping[str, Sequence[tuple[float, Optional[float]]]], *, title: str, xlabel: str,
               ylabel: str, subtitle: str = "", hlines: Sequence[tuple[float, str, str]] = (),
               step: bool = False, ymin: Optional[float] = None, ymax: Optional[float] = None,
               w: int = 720, h: int = 380) -> str:
    L, R, T, B = 64, 150, 56, 48
    pts = [(x, y) for s in series.values() for x, y in s if y is not None]
    xs = [p[0] for p in pts] or [0, 1]
    ys = [p[1] for p in pts] + [hl[0] for hl in hlines] or [0, 1]
    x0, x1 = min(xs), max(xs)
    y0 = min(ys) if ymin is None else ymin
    y1 = max(ys) if ymax is None else ymax
    if y1 == y0:
        y1 = y0 + 1
    pad = (y1 - y0) * 0.05
    y0, y1 = (y0 - pad if ymin is None else y0), (y1 + pad if ymax is None else y1)
    X = lambda v: L + (v - x0) / ((x1 - x0) or 1) * (w - L - R)  # noqa: E731
    Y = lambda v: h - B - (v - y0) / (y1 - y0) * (h - T - B)  # noqa: E731
    o = _frame(w, h, title, subtitle)
    for t in _ticks(y0, y1):
        if y0 <= t <= y1:
            o.append(f'<line x1="{L}" x2="{w - R}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{GRID}"/>')
            o.append(f'<text x="{L - 6}" y="{Y(t) + 4:.1f}" font-size="10" text-anchor="end" fill="{MUTED}">{_fmt(t)}</text>')
    for t in _ticks(x0, x1, 8):
        if x0 <= t <= x1:
            o.append(f'<text x="{X(t):.1f}" y="{h - B + 16}" font-size="10" text-anchor="middle" fill="{MUTED}">{_fmt(t)}</text>')
    o.append(f'<line x1="{L}" x2="{w - R}" y1="{h - B}" y2="{h - B}" stroke="{MUTED}"/>')
    o.append(f'<text x="{(L + w - R) / 2:.0f}" y="{h - 10}" font-size="11" text-anchor="middle" fill="{MUTED}">{_esc(xlabel)}</text>')
    o.append(f'<text x="14" y="{(T + h - B) / 2:.0f}" font-size="11" fill="{MUTED}" transform="rotate(-90 14 {(T + h - B) / 2:.0f})" text-anchor="middle">{_esc(ylabel)}</text>')
    for yv, lab, dash in hlines:
        if y0 <= yv <= y1:
            o.append(f'<line x1="{L}" x2="{w - R}" y1="{Y(yv):.1f}" y2="{Y(yv):.1f}" stroke="{INK}" stroke-width="1" stroke-dasharray="{dash}"/>')
            o.append(f'<text x="{w - R + 4}" y="{Y(yv) + 4:.1f}" font-size="10" fill="{INK}">{_esc(lab)}</text>')
    for i, (name, s) in enumerate(series.items()):
        c = PALETTE[i % len(PALETTE)]
        seg, segs = [], []
        prev = None
        for x, y in sorted(s, key=lambda p: p[0]):
            if y is None:
                if seg:
                    segs.append(seg)
                seg, prev = [], None
                continue
            if step and prev is not None:
                seg.append((X(x), Y(prev)))
            seg.append((X(x), Y(y)))
            prev = y
        if seg:
            segs.append(seg)
        for sg in segs:
            d = " ".join(f"{'M' if j == 0 else 'L'}{px:.1f},{py:.1f}" for j, (px, py) in enumerate(sg))
            o.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2"/>')
            if not step:
                for px, py in sg:
                    o.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.5" fill="{c}"/>')
        ly = T + 6 + i * 16
        o.append(f'<rect x="{w - R + 8}" y="{ly}" width="10" height="3" fill="{c}"/>')
        o.append(f'<text x="{w - R + 22}" y="{ly + 5}" font-size="10" fill="{INK}">{_esc(name)[:22]}</text>')
    o.append("</svg>")
    return "\n".join(o)


def scatter_chart(groups: Mapping[str, Sequence[tuple[float, float, str]]], *, title: str, xlabel: str,
                  ylabel: str, subtitle: str = "", w: int = 720, h: int = 440,
                  ranges: Optional[Mapping[str, Sequence[tuple[float, float, float, float]]]] = None) -> str:
    L, R, T, B = 72, 150, 56, 48
    pts = [(x, y) for g in groups.values() for x, y, _ in g]
    if not pts:
        pts = [(0, 0), (1, 1)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    if ranges:
        for rs in ranges.values():
            for a, b, c, d in rs:
                xs += [a, b]
                ys += [c, d]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    px, py = (x1 - x0) * 0.08 or 1, (y1 - y0) * 0.08 or 0.05
    x0, x1, y0, y1 = x0 - px, x1 + px, y0 - py, y1 + py
    X = lambda v: L + (v - x0) / (x1 - x0) * (w - L - R)  # noqa: E731
    Y = lambda v: h - B - (v - y0) / (y1 - y0) * (h - T - B)  # noqa: E731
    o = _frame(w, h, title, subtitle)
    for t in _ticks(y0, y1):
        if y0 <= t <= y1:
            o.append(f'<line x1="{L}" x2="{w - R}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{GRID}"/>')
            o.append(f'<text x="{L - 6}" y="{Y(t) + 4:.1f}" font-size="10" text-anchor="end" fill="{MUTED}">{_fmt(t)}</text>')
    for t in _ticks(x0, x1, 6):
        if x0 <= t <= x1:
            o.append(f'<text x="{X(t):.1f}" y="{h - B + 16}" font-size="10" text-anchor="middle" fill="{MUTED}">{_fmt(t)}</text>')
    o.append(f'<text x="{(L + w - R) / 2:.0f}" y="{h - 10}" font-size="11" text-anchor="middle" fill="{MUTED}">{_esc(xlabel)}</text>')
    o.append(f'<text x="14" y="{(T + h - B) / 2:.0f}" font-size="11" fill="{MUTED}" transform="rotate(-90 14 {(T + h - B) / 2:.0f})" text-anchor="middle">{_esc(ylabel)}</text>')
    for i, (name, g) in enumerate(groups.items()):
        c = PALETTE[i % len(PALETTE)]
        for a, b, cc, d in (ranges or {}).get(name, []):
            o.append(f'<line x1="{X(a):.1f}" x2="{X(b):.1f}" y1="{Y((cc + d) / 2):.1f}" y2="{Y((cc + d) / 2):.1f}" stroke="{c}" stroke-opacity="0.18"/>')
            o.append(f'<line x1="{X((a + b) / 2):.1f}" x2="{X((a + b) / 2):.1f}" y1="{Y(cc):.1f}" y2="{Y(d):.1f}" stroke="{c}" stroke-opacity="0.18"/>')
        for x, y, lab in g:
            o.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="4" fill="{c}" fill-opacity="0.85"><title>{_esc(lab)}</title></circle>')
            if lab:
                o.append(f'<text x="{X(x) + 5:.1f}" y="{Y(y) - 5:.1f}" font-size="8" fill="{MUTED}">{_esc(lab)}</text>')
        ly = T + 6 + i * 16
        o.append(f'<circle cx="{w - R + 13}" cy="{ly + 2}" r="4" fill="{c}"/>')
        o.append(f'<text x="{w - R + 22}" y="{ly + 6}" font-size="10" fill="{INK}">{_esc(name)}</text>')
    o.append("</svg>")
    return "\n".join(o)


def heatmap(rows: Sequence[str], cols: Sequence[str], values: Mapping[tuple[str, str], Optional[float]], *,
            title: str, subtitle: str = "", threshold: Optional[float] = None, fmt: str = "{:.2f}",
            categorical: Optional[Mapping[Any, str]] = None, w: Optional[int] = None) -> str:
    cw, ch, L, T = 46, 20, 150, 60
    w = w or max(L + cw * len(cols) + 20, 32 + 15 * len(title), 32 + 11 * len(subtitle))
    h = T + ch * len(rows) + 30
    vals = [v for v in values.values() if isinstance(v, (int, float))]
    lo, hi = (min(vals), max(vals)) if vals else (0, 1)
    o = _frame(w, h, title, subtitle)
    for j, c in enumerate(cols):
        o.append(f'<text x="{L + j * cw + cw / 2:.0f}" y="{T - 6}" font-size="9" text-anchor="middle" fill="{MUTED}">{_esc(c)}</text>')
    for i, r in enumerate(rows):
        o.append(f'<text x="{L - 6}" y="{T + i * ch + 14}" font-size="10" text-anchor="end" fill="{INK}">{_esc(r)[:24]}</text>')
        for j, c in enumerate(cols):
            v = values.get((r, c))
            x, y = L + j * cw, T + i * ch
            if categorical is not None:
                fill = categorical.get(v, "#f3f4f6")
                txt = ""
            elif v is None:
                fill, txt = "#f3f4f6", "—"
            else:
                t = 0.5 if hi == lo else (v - lo) / (hi - lo)
                fill = _blend("#eff6ff", "#1d4ed8", t)
                txt = fmt.format(v)
            o.append(f'<rect x="{x}" y="{y}" width="{cw - 2}" height="{ch - 2}" fill="{fill}"/>')
            if threshold is not None and isinstance(v, (int, float)) and v < threshold:
                o.append(f'<rect x="{x + 1}" y="{y + 1}" width="{cw - 4}" height="{ch - 4}" fill="none" stroke="#dc2626" stroke-width="1.5"/>')
            if txt:
                tc = "#ffffff" if (isinstance(v, (int, float)) and hi != lo and (v - lo) / (hi - lo) > 0.6) else INK
                o.append(f'<text x="{x + cw / 2 - 1:.0f}" y="{y + 13}" font-size="9" text-anchor="middle" fill="{tc}">{txt}</text>')
    o.append("</svg>")
    return "\n".join(o)


def _blend(a: str, b: str, t: float) -> str:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


# =========================================================================== markdown helpers


def fmt_cell(v: Any) -> str:
    """表格数字格式：大数加千分位，小数保留 4 位有效数字；空值显示 NULL。"""
    if v in (None, "", "None"):
        return "NULL"
    f = fnum(v)
    if f is None:
        return str(v)
    if abs(f) >= 1e4:
        return f"{f:,.0f}"
    if float(f).is_integer() and str(v).lstrip("-").replace(".0", "").isdigit():
        return f"{int(f)}"
    return f"{f:.4g}"


def md_table(rows: Sequence[Mapping[str, Any]], cols: Sequence[tuple[str, str]], limit: Optional[int] = None) -> str:
    rows = list(rows)[:limit] if limit else list(rows)
    if not rows:
        return "_（无数据）_\n"
    head = "| " + " | ".join(lab for _, lab in cols) + " |\n| " + " | ".join("---" for _ in cols) + " |\n"
    body = ""
    for r in rows:
        cells = []
        for k, _ in cols:
            v = r.get(k)
            f = fnum(v)
            cells.append(fmt_cell(v))
        body += "| " + " | ".join(cells) + " |\n"
    return head + body


# =========================================================================== 图与报告


def build_figures(out: Path) -> dict[str, str]:
    figs: dict[str, str] = {}
    fdir = out / "report" / "figures"
    fdir.mkdir(parents=True, exist_ok=True)

    def save(name: str, svg: str) -> None:
        (fdir / f"{name}.svg").write_text(svg, encoding="utf-8")
        figs[name] = f"figures/{name}.svg"

    # 1 覆盖矩阵（公开文件清单）
    cov = read_csv(out / "observations" / "repo_file_coverage.csv")
    if cov:
        cols = ["SD战斗", "DA战斗", "角色", "配装"]
        keys = ["has_sd_battle", "has_da_battle", "has_char", "has_build"]
        rows = [c["collection_period"] for c in cov]
        vals = {}
        for c in cov:
            # 配装文件不是每期必备（源仓库较晚才开始发布）：缺失记“无此文件”，不判缺期
            expected = {"has_sd_battle": bool(c["sd_season"]), "has_da_battle": bool(c["da_season"]),
                        "has_char": c["has_sd_battle"] == "True" or c["has_da_battle"] == "True", "has_build": False}
            for k, lab in zip(keys, cols):
                present = c[k] == "True"
                vals[(c["collection_period"], lab)] = "yes" if present else ("missing" if expected[k] else "n/a")
        save("coverage_matrix", heatmap(rows, cols, vals, title="公开文件覆盖矩阵（ShiyuStats 固定修订）",
                                        subtitle="蓝=清单中存在；红=应有而缺失；灰=无此模式/非必备文件。清单存在≠数据可用",
                                        categorical={"yes": "#60a5fa", "missing": "#f87171", "n/a": "#e5e7eb"}))
    # 2 参考适用性矩阵（L2）
    traj = read_csv(out / "benchmarks" / "reference_trajectories.csv") if stage_usable(out, "build_benchmarks") else []
    if traj:
        sel = [r for r in traj if r["plan"] == "0+1" and r["gear_policy"] == "growth_from_debut"
               and r["support_policy"] == "none"] or traj
        anchors = sorted({r["anchor"] for r in sel})
        ts = sorted({int(r["t_idx"]) for r in sel})
        vals = {(r["anchor"], f"t{int(r['t_idx'])}"): fnum(r["ci"]) for r in sel}
        save("reference_applicability", heatmap(anchors, [f"t{t}" for t in ts], vals, threshold=1.0,
                                                title="参考适用性矩阵：CI（0+1、无支援、出道起养成）",
                                                subtitle="红框=CI<1（模型未达标）；合成区间单位42天；代理模型，非真实满星"))
        # KM
        km = read_csv(out / "benchmarks" / "km_steps.csv")
        series = {}
        for r in km:
            if r["investment_plan"] == "0+1" and r["gear_policy"] == "growth_from_debut":
                series.setdefault(r["support_policy"], [(0.0, 1.0)]).append((fnum(r["age_days"]), fnum(r["survival"])))
        if series:
            save("km_first_failure", line_chart(series, title="首次观测失效 KM（L2 参考轨迹，0+1，养成）",
                                                xlabel="出道后天数 age_days", ylabel="未失效比例 S(t)", step=True,
                                                subtitle="描述性；观测单位=参考主体；不插值、不外推",
                                                hlines=[(0.5, "S=0.5", "4 3")], ymin=0, ymax=1.05))
    # 3 κ 敏感性与支援配对
    pairs = read_csv(out / "benchmarks" / "benchmark_pairs.csv") if stage_usable(out, "build_benchmarks") else []
    if pairs:
        acc: dict[str, dict[int, list[float]]] = {}
        for p in pairs:
            key = f"{p['changed_factor']}: {p['alt_label'].split('|')[-1]}"
            t = int(p["period_id"].lstrip("t"))
            acc.setdefault(key, {}).setdefault(t, []).append(fnum(p["abs_diff"]))
        series = {k: [(t, sum(v) / len(v)) for t, v in sorted(d.items())] for k, d in sorted(acc.items())}
        save("benchmark_pairs", line_chart(series, title="固定配对：只改变一项时 ΔCI 的平均（各期环境）",
                                           xlabel="区间 t", ylabel="平均 ΔCI",
                                           subtitle="支援=增加支援资产（非等成本）；κ=同一未减伤输出总量的时序分配",
                                           hlines=[(0.0, "0", "2 2")]))
    # 4 参照情景（L3）
    refs = reference_records(out)
    if refs:
        ret, cash, srcs, money = {}, {}, {}, {}
        for r in refs:
            tag = Path(r["path"]).stem
            rc = read_csv(out / "tables" / f"{tag}_retention_cash.csv")
            ret[r["scenario"]] = [(int(x["t_idx"]), fnum(x["retention_end"])) for x in rc]
            cash[r["scenario"]] = [(int(x["t_idx"]), (fnum(x["cum_cash_per_initial_fen"]) or 0) / 100) for x in rc]
            rs = read_csv(out / "tables" / f"{tag}_revenue_structure.csv")
            if r["scenario"] == refs[0]["scenario"]:
                for k, lab in (("curr_free_used", "本期免费"), ("hoarded_free_used", "历史免费"),
                               ("sub_pulls_used", "订阅"), ("topup_pulls_used", "直购")):
                    srcs[lab] = [(int(x["t_idx"]), fnum(x[k])) for x in rs]
                money["订阅现金(元)"] = [(int(x["t_idx"]), (fnum(x["sub_revenue_fen"]) or 0) / 100) for x in rs]
                money["直购现金(元)"] = [(int(x["t_idx"]), (fnum(x["topup_revenue_fen"]) or 0) / 100) for x in rs]
        save("ref_retention", line_chart(ret, title="模拟：期末留存率（三个假设参照情景）", xlabel="区间 t",
                                         ylabel="retention_end（/初始人数）", ymin=0, ymax=1.02,
                                         subtitle="假设情景；分母为初始人数"))
        save("ref_cash", line_chart(cash, title="模拟：累计人均现金（三个假设参照情景）", xlabel="区间 t",
                                    ylabel="元 / 初始玩家", subtitle="假设价格与行为；不除以幸存者"))
        save("ref_sources", line_chart(srcs, title=f"模拟：四来源抽数（{refs[0]['scenario']}）", xlabel="区间 t",
                                       ylabel="抽数", subtitle="假设情景；抽与元分开展示"))
        save("ref_money", line_chart(money, title=f"模拟：两类现金（{refs[0]['scenario']}）", xlabel="区间 t",
                                     ylabel="元", subtitle="假设情景"))
        # 固定队列三口径（取样本最多的队列）
        tag = Path(refs[0]["path"]).stem
        coh = read_csv(out / "tables" / f"{tag}_cohort_availability.csv")
        if coh:
            sizes: dict[tuple, int] = {}
            for r in coh:
                sizes[(r["unit_id"], r["acquisition_period"])] = int(r["n"])
            u, g = max(sizes, key=lambda k: (sizes[k], k))
            rows = [r for r in coh if r["unit_id"] == u and r["acquisition_period"] == g]
            series = {lab: [(int(r["t_idx"]), fnum(r[k])) for r in rows] for k, lab in (
                ("avail_rate_active", "avail_rate_active"), ("cohort_active_clear_share", "cohort_active_clear_share"),
                ("avail_rate_counterfactual_frozen", "counterfactual_frozen"), ("churned_holder_share", "churned_holder_share"))}
            save("cohort_rates", line_chart(series, title=f"模拟：固定获取队列三口径（{u}，t{g} 获取，N={sizes[(u, g)]}）",
                                            xlabel="评估区间 t", ylabel="比率", ymin=0, ymax=1.02,
                                            subtitle=f"假设情景 {refs[0]['scenario']}；未知结局使比率为 NULL"))
    # 5 候选集合
    rob = search_records(out, "seed_robustness.csv")
    if rob:
        groups, ranges = {}, {}
        for split in ("train", "validation"):
            rs = [r for r in rob if r["split"] == split and r["perturbation_id"] == "P0"]
            if not rs:
                continue
            lab = "筛选均值" if split == "train" else "复核均值"
            top = max((fnum(r["inclusion_frequency"]) or 0) for r in rs)
            groups[lab] = [((fnum(r["revenue_mean"]) or 0) / 100, fnum(r["retention_mean"]) or 0,
                            r["candidate_id"] if (split == "validation" and top > 0
                                                  and (fnum(r["inclusion_frequency"]) or 0) >= top) else "")
                           for r in rs]
            ranges[lab] = [((fnum(r["revenue_min"]) or 0) / 100, (fnum(r["revenue_max"]) or 0) / 100,
                            fnum(r["retention_min"]) or 0, fnum(r["retention_max"]) or 0) for r in rs]
        man = read_json(out / "search" / "experiment_manifest.json") or {}
        save("candidates", scatter_chart(groups, ranges=ranges, xlabel="模拟累计现金（元）",
                                         ylabel="期末核心预算层留存", title="模拟：25 点候选的收入 × 核心层留存",
                                         subtitle=f"假设情景；须线=种子 min–max；只标注复核入选频率最高的点；筛选与复核种子不同，人群不同；规模={man.get('scale', '?')}"))
    return figs


def build_report(out_dir: Path | str, *, rebuild_figures: bool = True) -> dict:
    out = Path(out_dir)
    if (out / "publication_manifest.json").is_file():
        from .publication_report import build_publication_report
        return build_publication_report(out)
    figs = (build_figures(out) if rebuild_figures else
            {p.stem: f"figures/{p.name}" for p in (out / "report" / "figures").glob("*.svg")})
    if observations_available(out) and (out / "report/figures/archetype_evolution.svg").is_file():
        figs["archetype_evolution"] = "figures/archetype_evolution.svg"
    rep = out / "report"
    v = read_json(out / "validate.json") or {}
    man = read_json(out / "pipeline_manifest.json") or {}
    smf = search_metadata(out)
    refs = reference_records(out)
    obs_cov = read_csv(out / "observations" / "repo_file_coverage.csv")
    obs_imported = observations_available(out)
    lines: list[str] = []
    A = lines.append
    from .analytics import insights as IN
    base_scn = v.get("base_scenario") or (refs[0]["scenario"] if refs else "")
    bgp = v.get("baseline_grid_point") or {}
    km_rows = read_csv(out / "benchmarks" / "km_first_failure.csv") if stage_usable(out, "build_benchmarks") else []
    find_l2 = []
    if stage_usable(out, "build_benchmarks"):
        find_l2 = (IN.lifetime_findings(km_rows)
                   + IN.applicability_findings(read_csv(out / "benchmarks" / "reference_trajectories.csv"))
                   + IN.pairing_findings(read_csv(out / "benchmarks" / "benchmark_pairs.csv")))
    ret_tabs = {r["scenario"]: read_csv(out / "tables" / f"{Path(r['path']).stem}_retention_cash.csv") for r in refs}
    find_l3 = IN.reference_findings(refs, ret_tabs, base_scn) if refs else []
    base_ref = next((r for r in refs if r["scenario"] == base_scn), None)
    if base_ref:
        stem = Path(base_ref["path"]).stem
        find_l3 += IN.revenue_mix_findings(read_csv(out / "tables" / f"{stem}_revenue_structure.csv"))
        find_cohort = IN.cohort_findings(read_csv(out / "tables" / f"{stem}_cohort_availability.csv"))
    else:
        find_cohort = []
    find_search = IN.search_findings(
        search_records(out, "search_results.csv"), search_records(out, "seed_robustness.csv"),
        search_records(out, "rank_stability.csv"), search_records(out, "parameter_sensitivity.csv"),
        float(bgp.get("mechanism_strength", 1)), float(bgp.get("support_gain_scale", 1))) if smf else []
    find_budget = []
    if base_ref:
        cross = read_csv(out / "tables" / f"{Path(base_ref['path']).stem}_budget_fatigue_cross.csv")
        if cross:
            fp = v.get("fatigue_params") or {}
            factors = sorted({float(r["factor"]) for r in (search_records(out, "parameter_sensitivity.csv") if smf else [])
                              if str(r.get("parameter_name", "")).endswith("fatigue.budget_weight")})
            find_budget = IN.budget_fatigue_findings(cross, fp.get("budget_weight"), fp.get("success_recovery"),
                                                     factors, label=f"基准参照 {base_scn}：")

    def fig(name: str, cap: str) -> None:
        if name in figs:
            A(f"![{cap}]({figs[name]})\n")

    A("# 研究报告：机制强度、支援与玩家生态（代理模型 + 假设情景）\n")
    A(f"> 生成：{REPORT_VERSION}｜配置 `{v.get('config_id', '?')}`｜情景解析 "
      f"`{(v.get('algorithm_versions') or {}).get('scenario_resolver', '?')}`\n")
    summary = (find_l2[:2] + find_l3[:2] + find_search[:3])
    if summary:
        A("## 执行摘要（模拟 · 假设情景）\n")
        A(IN.render(summary))
    notes = v.get("module_notes") or {}
    if notes:
        A("**情景标定声明**：以下参数是按目标区间选取的假设值（标定种子与筛选/复核种子不相交），"
          "因此“基准留存落在目标区间”“无支援寿命为若干区间”属于标定结果，不是模型发现；"
          "报告中 λ、a、支援与痛感扰动的方向和幅度才是在此基线下的情景推演输出。\n\n")
        for m, d in notes.items():
            A(f"- `{m}`：{d}\n")
        A("\n")
    A("## 1. 问题、模式与实际覆盖\n")
    A("本报告只陈述已实际执行并通过校验的阶段。阶段状态：\n")
    A(md_table([{"stage": k, "status": s.get("status"), "seconds": s.get("wall_seconds")}
                for k, s in (man.get("stages") or {}).items() if k not in ("report", "dashboard")],
               [("stage", "阶段"), ("status", "状态"), ("seconds", "耗时(s)")]))
    availability = database_availability_note(out)
    if availability:
        A(f"\n**本机证据范围**：{availability}\n")
    scale = (smf or {}).get("scale")
    if scale and scale != "full_protocol":
        A(f"\n**规模声明**：候选搜索以缩减规模运行（`{scale}`，公共覆盖 "
          f"`{json.dumps((smf or {}).get('common_overrides'), ensure_ascii=False)}`）。该覆盖改变 config_id，"
          "结果只验证协议与流程，样本量不足以支持商业结论。\n")
    A("\n## 2. 公开样本（Layer 1）\n")
    if obs_cov:
        complete = sum(1 for c in obs_cov if c["status"] == "complete")
        A(f"固定修订 `d930cec5` 的公开文件清单覆盖 {len(obs_cov)} 个采集期，其中 {complete} 个文件齐全。"
          "清单存在不等于数据已获取或可用。\n")
        fig("coverage_matrix", "覆盖矩阵")
    if obs_imported:
        obs_manifest = read_json(out / "observations" / "observations_manifest.json") or {}
        coverage = read_csv(out / "observations" / "obs_coverage.csv")
        A(f"\n**真实公开战斗记录已导入**：共 {int(obs_manifest.get('n_battles', 0)):,} 条队伍战斗记录，"
          f"其中 {int(obs_manifest.get('n_eligible', 0)):,} 条符合各时期的有效通关规则。"
          "这里的记录数包含同一玩家的多场战斗，不能当成玩家总人数。\n\n")
        A("这些是社区公开样本，可以描述样本中的角色出场与成绩。提交样本存在选择偏差，"
          "无法代表全部玩家，也不能用来推算真实充值、购买行为或退坑率。"
          "下文的行为与收入模拟仍使用显式假设，尚未用这些观测数据标定。\n\n")
        modes = {"shiyu_defense": "式舆防卫战", "deadly_assault": "危局强袭战"}
        by_mode = []
        for mode in sorted({r["mode"] for r in coverage}):
            group = [r for r in coverage if r["mode"] == mode]
            by_mode.append({"mode": modes.get(mode, mode),
                            "raw": sum(int(r["raw_records"]) for r in group),
                            "eligible": sum(int(r["eligible_records"]) for r in group),
                            "scopes": len({(r["source_snapshot_id"], r["combat_season"]) for r in group})})
        A(md_table(by_mode, [("mode", "模式"), ("raw", "原始战斗记录"),
                             ("eligible", "合格战斗记录"), ("scopes", "采集期与赛季组合数")]))
        A(f"\n原始数据来自 [LvlUrArti / ShiyuData](https://huggingface.co/datasets/LvlUrArti/ShiyuData)，"
          f"固定数据修订 `{obs_manifest.get('revision', '?')}`。"
          "同一采集期内关联战斗与角色资料；不同采集期不合并为同一个玩家。\n\n")
        A("导入范围限定为项目当前登记的采集期；数据集新增的其他时期尚未并入本报告。\n\n")
        for issue in obs_manifest.get("quality_issues") or []:
            if issue.startswith("eligible_without_result:"):
                n_missing = int(issue.split(":")[1])
                A(f"**成绩缺失**：{n_missing:,} 条合格记录没有用时或分数。"
                  "它们仍可用于角色出场统计，成绩分布只使用成绩已知的记录。\n\n")
            else:
                A(f"**待核查的数据问题**：`{issue}`；详见质量记录。\n\n")
        A("完整的分期覆盖、排除原因和质量检查分别见 "
          "[覆盖表](../observations/obs_coverage.csv)、[排除原因](../observations/exclusions.csv)和"
          "[质量记录](../observations/quality_issues.json)。下表展示有合格通关记录的关卡。\n\n")
        from .boss_presentation import enrich_boss_coverage
        visible_coverage = enrich_boss_coverage(out, [r for r in coverage if int(r["eligible_records"]) > 0])
        A(md_table(visible_coverage,
                   [("combat_season", "赛季"), ("mode", "模式"), ("stage_id", "关卡"), ("raw_records", "原始"),
                    ("eligible_records", "合格"), ("unique_players", "唯一ID"), ("boss_display", "对应首领与机制"),
                    ("status", "状态")], limit=30))
        app = read_csv(out / "observations" / "appearance.csv")
        A("\n以下出场比例只在合格通关样本内计算，不等于角色持有率或全体玩家使用率。"
          "同一采集期内按唯一 ID 去重，并与每条队伍的出场率分开列出：\n\n")
        A(md_table(app, [("combat_season", "赛季"), ("mode", "模式"), ("unit_id", "角色"),
                         ("appearance_player_share", "出场人数占比"), ("appearance_per_clear", "每队伍出场率"),
                         ("sample_players", "样本人数"), ("small_sample", "小样本")], limit=15))
    else:
        A("\n**原始战斗记录未导入或当前导入状态未确认**：本次未提供已确认导入的原始CSV；"
          "不展示旧观测派生表，也不以替代数据填充出场率、成绩或投资关联。"
          "未导入状态本身不能说明网络访问失败。导入使用 `prepare-observations --snapshot-dir`。\n")
    from .archetype_presentation import archetype_markdown, bridge_text
    if obs_imported:
        A(archetype_markdown(out))
        from .boss_presentation import boss_markdown
        A(boss_markdown(out))
    A("\n## 3. 代理模型与固定配对（Layer 2）\n")
    A("广义机制换锁流（`Ref_Mechanism`）以秽盾、紫光控制技、暴伤锁与侵蚀壁垒等真实机制作为业务原型。"
      "当前数值实现仍是盾期防御加成、额外减伤、输出对齐与支援增益的参数代理，"
      "没有逐项复刻这些首领技能；下文 λ 与 a 的推演保持原科学配置。\n\n")
    if obs_imported:
        observed_bridge = bridge_text(out, layer="layer2")
        if observed_bridge:
            A(observed_bridge + "\n\n")
    A(IN.render(find_l2))
    A("公式见 combat.md 4.1–4.4；盾内相对保留率按分项计算（不重复乘 0.5222）。CI≥1 为模型达标代理，"
      "不代表真实多队满星。\n")
    fig("benchmark_pairs", "固定配对")
    fig("reference_applicability", "参考适用性矩阵")
    km = read_csv(out / "benchmarks" / "km_first_failure.csv") if stage_usable(out, "build_benchmarks") else []
    if km:
        A("\n首次观测失效（KM，描述性；观测单位为预注册参考主体）：\n")
        A(md_table(km, [("investment_plan", "投资"), ("gear_policy", "养成"), ("support_policy", "支援"),
                        ("subjects", "主体"), ("events", "事件"), ("median_age_days", "中位age(天)"),
                        ("S_at_last", "末期S")]))
        fig("km_first_failure", "KM")
        cox = read_json(out / "benchmarks" / "cox_status.json") or {}
        A(f"\n时变 Cox：{cox.get('status')}（{cox.get('reason')}）。\n")
    A("\n## 4. 模拟行为：三个假设参照情景（Layer 3）\n")
    A(IN.render(find_l3))
    if refs:
        from .dashboard_data import label as business_label
        display_refs = [{**r, "scenario_label": business_label(r["scenario"])} for r in refs]
        A(md_table(display_refs, [("scenario_label", "情景"), ("seed", "种子"), ("n_players", "人数"),
                          ("total_revenue_fen", "累计现金(分)"), ("core_retention_end", "核心层期末留存"),
                          ("retention_end_all", "全体期末留存")]))
        for n, c in (("ref_retention", "留存"), ("ref_cash", "现金"), ("ref_sources", "四来源"), ("ref_money", "两类现金")):
            fig(n, c)
    else:
        A("_未运行_\n")
    A("\n## 5. 固定获取队列三口径\n")
    A(IN.render(find_cohort))
    fig("cohort_rates", "三口径")
    A("流失贡献 0 表示退出生态而非战斗失败；冻结口径是“停止投入的配置情景”，不是未退坑预测。\n")
    A("\n## 6. 25 点候选、独立复核与假设敏感性\n")
    A(IN.render(find_search + find_budget))
    if smf:
        A(f"实验 `{smf['experiment_id']}`：候选 {len(smf['candidates'])} 个，筛选种子 {smf['train_seeds']}，"
          f"复核种子 {smf['validation_seeds']}，扰动 {len(smf['perturbations'])} 组；逻辑 run "
          f"{smf['summary'].get('logical_runs')}，去重后不同 run {smf['summary'].get('unique_runs')}。"
          "同seed候选共享CRN，不把这些运行数量当成独立统计重复数。\n")
        fig("candidates", "候选集合")
        rob = [r for r in search_records(out, "seed_robustness.csv")
               if r["perturbation_id"] == "P0" and r["split"] == "validation"]
        rob.sort(key=lambda r: (-(fnum(r["inclusion_frequency"]) or 0), -(fnum(r["revenue_mean"]) or 0)))
        A("\n复核种子下的非支配入选频率（只描述这几次结果，不称真实最优概率）：\n")
        A(md_table(rob, [("candidate_id", "候选"), ("revenue_mean", "收入均值(分)"), ("revenue_sd", "收入SD"),
                         ("retention_mean", "留存均值"), ("retention_sd", "留存SD"),
                         ("inclusion_frequency", "入选频率")], limit=25))
        A("\n秩稳定（并列平均秩；无变异时 NULL）：\n")
        A(md_table(search_records(out, "rank_stability.csv"),
                   [("perturbation_id", "扰动"), ("comparison", "比较"), ("rho_rev", "ρ收入"), ("rho_ret", "ρ留存")]))
        trn = [r for r in search_records(out, "seed_robustness.csv")
               if r["perturbation_id"] == "P0" and r["split"] == "train"]
        if trn:
            def marg(idx):
                acc: dict[str, list] = {}
                for r in trn:
                    k = r["candidate_id"].split("_")[idx]
                    acc.setdefault(k, []).append(r)
                return [{"level": k, "revenue_mean": sum(fnum(x["revenue_mean"]) for x in v) / len(v),
                         "retention_mean": sum(fnum(x["retention_mean"]) or 0 for x in v) / len(v),
                         "revenue_sd_mean": sum(fnum(x["revenue_sd"]) or 0 for x in v) / len(v), "n": len(v)}
                        for k, v in sorted(acc.items(), key=lambda kv: float(kv[0][1:]))]
            cols = [("level", "水平"), ("n", "候选数"), ("revenue_mean", "收入均值(分)"),
                    ("revenue_sd_mean", "平均种子SD(分)"), ("retention_mean", "核心层留存均值")]
            A("\n按 λ（机制强度）汇总的筛选种子均值（对 5 个 a 取平均，描述用）：\n")
            A(md_table(marg(0), cols))
            A("\n按 a（支援增益缩放）汇总：\n")
            A(md_table(marg(1), cols))
        sens = search_records(out, "parameter_sensitivity.csv")
        if sens:
            acc2: dict[str, list] = {}
            for r in sens:
                acc2.setdefault(r["perturbation_id"], []).append(r)
            A("\n假设参数扰动（与种子波动分表；对 25 个候选取平均的相对变化）：\n")
            A(md_table([{"perturbation_id": k,
                         "rev": sum(fnum(x["rel_change_revenue_vs_P0"]) or 0 for x in v) / len(v),
                         "ret": sum(fnum(x["rel_change_retention_vs_P0"]) or 0 for x in v) / len(v)}
                        for k, v in sorted(acc2.items())],
                       [("perturbation_id", "扰动"), ("rev", "收入相对变化"), ("ret", "留存相对变化")]))
        from .analytics.paired import paired_summary
        paired = paired_summary(search_records(out, "search_results.csv"))
        if paired:
            from .pipeline import write_csv
            write_csv(out / "search" / "paired_differences.csv", paired)
            A("\nCRN同种子配对差（相对同一扰动/种子的预设基准；P0复核）：\n")
            A(md_table([r for r in paired if r["perturbation_id"] == "P0" and r["split"] == "validation"],
                       [("candidate_id", "候选"), ("delta_revenue_n", "有效配对n"),
                        ("delta_revenue_mean", "收入差均值(分)"), ("delta_revenue_sd", "配对差SD"),
                        ("delta_revenue_min", "差值最小"), ("delta_revenue_max", "差值最大"),
                        ("delta_retention_mean", "留存差均值")], limit=25))
        A("\n随机种子波动与参数扰动分表列出。CRN比较应查看同种子差值的均值、SD、范围及有效配对数；"
          "各候选各自的种子SD不能用来判断配对差异是否可区分。当前仅少量复核种子，配对摘要是描述性结果，"
          "不构成显著性、等效性或商业最优检验。ρ衡量排序一致性，不能单独确定排序变化的原因。"
          "痛感系数扰动通过疲劳只改变流失概率；其对现金的影响取决于流失发生在观察期中的哪一期。\n")
    else:
        A("_未运行_\n")
    A("\n## 7. 解释边界、局限与复现\n")
    A("- 所有行为、价格与疲劳系数为假设情景参数，未由公开数据估计；结果是机制推演，不是市场预测。\n"
      "- 情景解析含三条显式假设（HP 复利、首发强度复利、理想化贯穿占比），版本 `scenario_resolve_v1_assumed`。\n"
      "- 代理模型不模拟逐段属性、异常积蓄与真实排轴；完整 ZSim 未运行。\n"
      "- 小样本或缩减规模下的排序差异可能完全来自随机波动。\n"
      "- 部分参数经情景标定（见文首声明）：标定目标本身不能作为发现引用。\n")
    A("\n从已有产物重新生成报告与静态看板（复用已导入的观测数据）：\n\n```powershell\n"
      f'python -m zzz_research report --dir "{out.as_posix()}"\n'
      f'python -m zzz_research app --dir "{out.as_posix()}"\n'
      "```\n\n分步重建基准、运行与候选搜索的命令见项目 README；已发布实验的配置保存在 `config_snapshot/`。\n")
    A("\n数据来源：ShiyuStats（LvlUrArti，MIT，修订 d930cec5）；ZSim 防御公式（GPL-3.0，修订 e248e9f1）仅作公式参考。\n")
    rep.mkdir(parents=True, exist_ok=True)
    text = "".join(lines)
    (rep / "REPORT.md").write_text(text, encoding="utf-8")
    def root_link(match):
        target = match.group(2)
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(("#", "/")):
            return match.group(0)
        path, separator, fragment = target.partition("#")
        rebased = Path(os.path.relpath(rep / path, out)).as_posix()
        return match.group(1) + rebased + (separator + fragment if separator else "") + match.group(3)
    root_text = re.sub(r"(!?\[[^\]\n]*\]\()([^\)\n]+)(\))", root_link, text)
    (out / "REPORT.md").write_text(root_text, encoding="utf-8")
    return {"path": str(rep / "REPORT.md"), "n_figures": len(figs)}
