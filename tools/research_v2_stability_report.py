"""跨种子配对复核报告；只读取已汇总的模拟结果。"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

from research_v2_stability_stats import compute_statistics
from zzz_research.config import resolve_config
from zzz_research.pipeline import from_project, write_csv


METRICS = {
    "cash_yuan": "总现金",
    "new_main_topup_yuan": "新主 C 池直购",
    "support_topup_yuan": "专拐直购",
    "subscription_yuan": "月卡续订",
    "retention_core": "核心层留存",
    "retention_all": "全队列留存",
}
SPLITS = {"train": "筛选", "validation": "复核"}
FONT = "'Microsoft YaHei','Noto Sans CJK SC','PingFang SC',sans-serif"
BLUE, GREEN, INK, MUTED = "#2563eb", "#059669", "#172033", "#667085"
CASH_COMPONENTS = ("new_main_topup_yuan", "support_topup_yuan", "subscription_yuan", "cash_yuan")


def _number(value: Any, digits: int = 2, suffix: str = "") -> str:
    return "—" if value is None else f"{float(value):,.{digits}f}{suffix}"


def _rate(value: Any) -> str:
    return "—" if value is None else f"{float(value):.1%}"


def _cash(value: Any) -> str:
    return "—" if value is None else f"¥{float(value):,.0f}"


def _rho(row: dict | None) -> str:
    if row is None or row.get("rho") is None:
        return "未定义（候选均值为常数）" if row else "—"
    return f"{float(row['rho']):.3f}"


def _interval(row: dict, *, relative: bool = False, currency: bool = False) -> str:
    mean = row.get("mean_relative_pct" if relative else "mean_diff")
    low = row.get("relative_ci95_low" if relative else "ci95_low")
    high = row.get("relative_ci95_high" if relative else "ci95_high")
    if mean is None:
        return "—"
    suffix = "%" if relative else "pp" if row["metric"].startswith("retention_") else ""
    point = f"{float(mean):+,.0f}" if currency else f"{float(mean):+.2f}{suffix}"
    if low is None or high is None:
        return f"{point}（区间未估计）"
    interval = f"[{float(low):+,.0f}, {float(high):+,.0f}]" if currency else f"[{float(low):+.2f}, {float(high):+.2f}]{suffix}"
    return f"{point} {interval}"


def _svg_start(title: str, subtitle: str, width: int, height: int) -> list[str]:
    esc = html.escape
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">',
            f'<rect width="{width}" height="{height}" rx="16" fill="#fff"/>',
            f'<g font-family="{FONT}" fill="{INK}">',
            f'<text x="28" y="34" font-size="19" font-weight="700">{esc(title)}</text>',
            f'<text x="28" y="60" font-size="12" fill="{MUTED}">{esc(subtitle)}</text>']


def _rank_figure(rows: list[dict], horizons: list[int]) -> str:
    ordered = [r for metric in METRICS for h in horizons for r in rows
               if r["metric"] == metric and r["horizon"] == h]
    width, height = 940, 122 + 36 * len(ordered)
    left, right, top = 225, 770, 100
    x = lambda value: left + (value + 1) / 2 * (right - left)
    out = _svg_start("模拟现金结构与留存：排序复核一致到什么程度？",
                     "Spearman ρ 比较 25 个候选的筛选均值与复核均值；常数排名记为未定义", width, height)
    for tick in (-1, -.5, 0, .5, 1):
        out += [f'<line x1="{x(tick):.1f}" y1="{top-15}" x2="{x(tick):.1f}" y2="{height-42}" stroke="#e5e7eb"/>',
                f'<text x="{x(tick):.1f}" y="{height-20}" font-size="11" text-anchor="middle" fill="{MUTED}">{tick:g}</text>']
    for i, row in enumerate(ordered):
        y = top + i * 36
        color = BLUE if row["horizon"] == min(horizons) else GREEN
        label = f"{METRICS[row['metric']]} · {row['horizon']}期"
        out.append(f'<text x="28" y="{y+5}" font-size="12">{html.escape(label)}</text>')
        value = row.get("rho")
        if value is None:
            out.append(f'<text x="{right+18}" y="{y+5}" font-size="12" fill="{MUTED}">未定义</text>')
        else:
            point = x(float(value))
            out += [f'<rect x="{min(x(0),point):.1f}" y="{y-8}" width="{abs(point-x(0)):.1f}" height="16" rx="4" fill="{color}" fill-opacity=".78"/>',
                    f'<text x="{right+18}" y="{y+5}" font-size="13" font-weight="700">{float(value):.3f}</text>']
    out += ['</g></svg>']
    return "\n".join(out)


def _effect_figure(rows: list[dict], *, metric: str, relative: bool) -> str:
    chosen = sorted([r for r in rows if r["metric"] == metric and r["contrast_kind"] == "endpoint"],
                    key=lambda r: (r["axis"], r["horizon"], r["split"]))
    mean_key, lo_key, hi_key = (("mean_relative_pct", "relative_ci95_low", "relative_ci95_high")
                                 if relative else ("mean_diff", "ci95_low", "ci95_high"))
    finite = [float(r[k]) for r in chosen for k in (mean_key, lo_key, hi_key) if r.get(k) is not None]
    lo, hi = min([0., *finite]), max([0., *finite])
    pad = (hi - lo) * .1 or 1.
    lo, hi = lo - pad, hi + pad
    width, height, left, right, top = 1040, 145 + 48 * len(chosen), 270, 760, 100
    x = lambda v: left + (v - lo) / (hi - lo) * (right - left)
    unit = "%" if relative else "个百分点"
    title = "模拟现金的调参净变化与不确定性" if relative else "模拟核心留存的调参净变化与不确定性"
    out = _svg_start(title, "每个种子先配对，再跨种子求均值；横线为逐项 t 近似 95% 置信区间", width, height)
    for i in range(5):
        value = lo + i * (hi-lo) / 4
        out += [f'<line x1="{x(value):.1f}" y1="{top-18}" x2="{x(value):.1f}" y2="{height-60}" stroke="#e5e7eb"/>',
                f'<text x="{x(value):.1f}" y="{height-37}" font-size="11" text-anchor="middle" fill="{MUTED}">{value:+.1f}</text>']
    out.append(f'<line x1="{x(0):.1f}" y1="{top-18}" x2="{x(0):.1f}" y2="{height-60}" stroke="#667085" stroke-dasharray="5 4"/>')
    for i, row in enumerate(chosen):
        y = top + i * 48
        axis = "λ" if row["axis"] == "mechanism_strength" else "a"
        label = f"{axis} {row['low_level']:g}→{row['high_level']:g} · {row['horizon']}期 · {SPLITS[row['split']]}"
        out.append(f'<text x="28" y="{y+5}" font-size="12">{html.escape(label)}</text>')
        color = BLUE if row["split"] == "train" else GREEN
        mean, low, high = row.get(mean_key), row.get(lo_key), row.get(hi_key)
        if mean is not None:
            out.append(f'<circle cx="{x(float(mean)):.1f}" cy="{y}" r="5" fill="{color}"/>')
        if low is not None and high is not None:
            a, b = x(float(low)), x(float(high))
            out += [f'<line x1="{a:.1f}" x2="{b:.1f}" y1="{y}" y2="{y}" stroke="{color}" stroke-width="2"/>',
                    f'<path d="M{a:.1f},{y-6}V{y+6} M{b:.1f},{y-6}V{y+6}" stroke="{color}"/>']
        suffix = "%" if relative else "pp"
        text = "—" if mean is None else f"{float(mean):+.2f}{suffix}"
        out.append(f'<text x="{right+18}" y="{y+5}" font-size="12" font-weight="700">{text}</text>')
    out += [f'<text x="{(left+right)/2:.0f}" y="{height-13}" text-anchor="middle" font-size="12" fill="{MUTED}">高档位 − 低档位（{unit}）</text>', '</g></svg>']
    return "\n".join(out)


def _frontier_figure(rows: list[dict], horizon: int) -> str:
    selected = [r for r in rows if r["split"] == "validation" and r["horizon"] == horizon]
    levels_l = sorted({r["mechanism_strength"] for r in selected})
    levels_a = sorted({r["support_gain_scale"] for r in selected})
    lookup = {(r["mechanism_strength"], r["support_gain_scale"]): r for r in selected}
    width, height, left, top, cell_w, cell_h = 920, 150 + len(levels_l) * 57, 160, 120, 130, 57
    out = _svg_start(f"模拟 {horizon} 期：哪些候选在复核种子中进入收入—留存前沿？",
                     "单元格=入选种子数/复核种子数；频率描述本轮有限种子，不是最优策略的概率", width, height)
    for j, a in enumerate(levels_a):
        out.append(f'<text x="{left+j*cell_w+cell_w/2}" y="{top-15}" text-anchor="middle" font-size="13">a={a:g}</text>')
    for i, l in enumerate(levels_l):
        out.append(f'<text x="{left-20}" y="{top+i*cell_h+35}" text-anchor="end" font-size="13">λ={l:g}</text>')
        for j, a in enumerate(levels_a):
            r = lookup[(l,a)]
            freq = float(r["inclusion_frequency"])
            red, green, blue = [round(start + freq * (end-start)) for start, end in ((244, 116), (247, 214), (250, 177))]
            out += [f'<rect x="{left+j*cell_w}" y="{top+i*cell_h}" width="{cell_w-6}" height="{cell_h-6}" rx="8" fill="rgb({red},{green},{blue})"/>',
                    f'<text x="{left+j*cell_w+(cell_w-6)/2}" y="{top+i*cell_h+32}" text-anchor="middle" font-size="18" font-weight="700">{r["included_seeds"]}/{r["n_seeds"]}</text>']
    out += ['</g></svg>']
    return "\n".join(out)


def _axis(tables: dict, horizon: int, split: str, axis: str, metric: str) -> dict | None:
    return next((r for r in tables["axis_effects"] if r["horizon"] == horizon and r["split"] == split
                 and r["axis"] == axis and r["metric"] == metric and r["contrast_kind"] == "endpoint"), None)


def _cash_decomposition(tables: dict, horizon: int, split: str) -> list[dict]:
    selected = {r["metric"]: r for r in tables["axis_cash_decomposition"]
                if r["horizon"] == horizon and r["split"] == split
                and r["axis"] == "support_gain_scale" and r["contrast_kind"] == "endpoint"}
    return [selected[m] for m in CASH_COMPONENTS if m in selected]


def _decomposition_interval(row: dict, *, contribution: bool) -> str:
    mean_key, low_key, high_key = (("mean_contribution_pp", "contribution_ci95_low_pp", "contribution_ci95_high_pp")
                                   if contribution else ("mean_diff_yuan", "ci95_low_yuan", "ci95_high_yuan"))
    mean, low, high = row.get(mean_key), row.get(low_key), row.get(high_key)
    if mean is None:
        return "—"
    digits = 2 if contribution else 0
    point = f"{float(mean):+,.{digits}f}"
    if low is None or high is None:
        return f"{point}（区间未估计）"
    return f"{point} [{float(low):+,.{digits}f}, {float(high):+,.{digits}f}]"


def _decomposition_reading(rows: list[dict]) -> str:
    components = [r for r in rows if r["metric"] != "cash_yuan"]
    if not components:
        return ""
    positive = [METRICS[r["metric"]] for r in components if r["mean_diff_yuan"] > 0]
    negative = [METRICS[r["metric"]] for r in components if r["mean_diff_yuan"] < 0]
    if positive and negative:
        reading = f"三项点估计存在抵消：{'、'.join(positive)}增加，{'、'.join(negative)}减少。"
    elif positive:
        reading = f"三项点估计未呈现正负抵消，增加项为{'、'.join(positive)}。"
    elif negative:
        reading = f"三项点估计未呈现正负抵消，减少项为{'、'.join(negative)}。"
    else:
        reading = "三项现金点估计均未变化。"
    uncertain = [METRICS[r["metric"]] for r in components
                 if r.get("ci95_low_yuan") is None or r["ci95_low_yuan"] <= 0 <= r["ci95_high_yuan"]]
    if uncertain:
        reading += f"按绝对现金差口径，{'、'.join(uncertain)}的区间覆盖零或尚未估计，方向仍有不确定性。"
    return reading


def _decomposition_figure(rows: list[dict], horizon: int, players: int) -> str:
    finite = [float(r[k]) for r in rows for k in
              ("mean_contribution_pp", "contribution_ci95_low_pp", "contribution_ci95_high_pp")
              if r.get(k) is not None]
    low, high = min([0., *finite]), max([0., *finite])
    padding = (high - low) * .1 or 1.
    low, high = low - padding, high + padding
    width, height, left, right, top = 1040, 185 + 58 * len(rows), 250, 760, 118
    x = lambda value: left + (value - low) / (high - low) * (right - left)
    first = rows[0]
    out = _svg_start(f"模拟 {horizon} 期：专拐增益提高后，三类现金分别贡献多少？",
                     f"N={players}，a={first['low_level']:g}→{first['high_level']:g}，复核样本；共同分母为同种子基期总现金；横线为逐项 t95 区间", width, height)
    for i in range(5):
        tick = low + i * (high-low) / 4
        out += [f'<line x1="{x(tick):.1f}" y1="{top-18}" x2="{x(tick):.1f}" y2="{height-65}" stroke="#e5e7eb"/>',
                f'<text x="{x(tick):.1f}" y="{height-42}" text-anchor="middle" font-size="11" fill="{MUTED}">{tick:+.1f}</text>']
    out.append(f'<line x1="{x(0):.1f}" y1="{top-18}" x2="{x(0):.1f}" y2="{height-65}" stroke="#667085" stroke-dasharray="5 4"/>')
    for i, row in enumerate(rows):
        y = top + 58 * i
        color = INK if row["metric"] == "cash_yuan" else BLUE
        out.append(f'<text x="28" y="{y+5}" font-size="13">{html.escape(METRICS[row["metric"]])}</text>')
        mean, lo, hi = row.get("mean_contribution_pp"), row.get("contribution_ci95_low_pp"), row.get("contribution_ci95_high_pp")
        if lo is not None and hi is not None:
            a, b = x(float(lo)), x(float(hi))
            out += [f'<line x1="{a:.1f}" x2="{b:.1f}" y1="{y}" y2="{y}" stroke="{color}" stroke-width="2"/>',
                    f'<path d="M{a:.1f},{y-6}V{y+6} M{b:.1f},{y-6}V{y+6}" stroke="{color}"/>']
        if mean is not None:
            out += [f'<circle cx="{x(float(mean)):.1f}" cy="{y}" r="5" fill="{color}"/>',
                    f'<text x="{right+18}" y="{y+5}" font-size="13" font-weight="700">{float(mean):+.2f}pp</text>']
    out += [f'<text x="{(left+right)/2:.0f}" y="{height-16}" font-size="12" text-anchor="middle" fill="{MUTED}">占基期总现金的变化（百分点）；三项贡献之和为总现金相对变化</text>', '</g></svg>']
    return "\n".join(out)


def _decomposition_section(tables: dict, horizons: list[int]) -> list[str]:
    longest = max(horizons)
    rows = _cash_decomposition(tables, longest, "validation")
    if not rows:
        return []
    first = rows[0]
    lines = ["", f"### 专拐增益 a {first['low_level']:g}→{first['high_level']:g}：总现金由什么组成？", "",
             f"{longest} 期复核样本中，每个种子先对五档 λ 等权平均，再分别计算三类现金的高档减低档。下表使用同一种子 a={first['low_level']:g} 的**总现金**作为统一分母；各分项的贡献百分点可相加。", "",
             "![专拐增益的三类现金贡献](figures/support_cash_decomposition.svg)", "",
             "| 现金项 | 配对种子数 | 现金差 [95%区间，元] | 基期总现金贡献 [95%区间，百分点] |",
             "|---|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {METRICS[row['metric']]} | {row['n_pairs']} | {_decomposition_interval(row,contribution=False)} | {_decomposition_interval(row,contribution=True)} |")
    lines += ["", _decomposition_reading(rows), "",
              "总现金的均值与区间由每个种子的三项变化求和后单独估计，保留分项之间的协方差；不把三个区间端点相加。金额均值和贡献均值的未四舍五入值严格可加。贡献使用共同的总现金分母，不是各分项自身的增长率。", "",
              "绝对现金差先对各个种子的金额差等权平均；归一化贡献先除以各个种子的基期总现金，再等权平均。基期现金随种子变化，因此二者的区间是否跨零可能不同，解释时使用相应口径。", "",
              "<details>", "<summary>展开其他观察窗口和筛选样本的三类现金配对拆解</summary>", "",
              "| 观察期 | 种子组 | 现金项 | 现金差 [95%区间，元] | 基期总现金贡献 [95%区间，百分点] |",
              "|---:|---|---|---:|---:|"]
    for horizon in horizons:
        for split in ("train", "validation"):
            if horizon == longest and split == "validation":
                continue
            for row in _cash_decomposition(tables, horizon, split):
                lines.append(f"| {horizon} | {SPLITS[split]} | {METRICS[row['metric']]} | {_decomposition_interval(row,contribution=False)} | {_decomposition_interval(row,contribution=True)} |")
    lines += ["", "</details>", "",
              "全部端点、相邻档位和 λ 轴的拆解保存在[现金配对拆解表](tables/axis_cash_decomposition.csv)，[种子级分项记录](tables/cash_decomposition_seed.csv)可核对每一项的分母、现金差和贡献。"]
    return lines


def _prior_population_experiment(out: Path, manifest: dict) -> tuple[Path, dict] | None:
    prior_out = out.parent / "research_v2_stability"
    prior_manifest_path = prior_out / "experiment_manifest.json"
    if prior_out.resolve() == out.resolve() or not prior_manifest_path.exists():
        return None
    prior = json.loads(prior_manifest_path.read_text("utf-8"))
    if (prior["players"] == manifest["players"] or prior["status"] != "completed"
            or set(prior["seeds"]) != set(manifest["seeds"])
            or set(prior["train_seeds"]) != set(manifest["train_seeds"])
            or set(prior["validation_seeds"]) != set(manifest["validation_seeds"])):
        return None
    matched = resolve_config(from_project(manifest["entry"]), overrides={"population.n_players": prior["players"]})
    if matched.config_id != prior["base_config_id"]:
        return None
    return prior_out, prior


def _population_rank_comparison(out: Path, manifest: dict, rank: list[dict]) -> list[dict]:
    previous = _prior_population_experiment(out, manifest)
    if previous is None:
        return []
    prior_out, prior = previous
    with (prior_out / "tables/rank_stability.csv").open(encoding="utf-8", newline="") as handle:
        old_rows = list(csv.DictReader(handle))
    old_index = {(int(r["horizon"]), r["metric"]): r for r in old_rows}
    compared = []
    for row in rank:
        old = old_index.get((row["horizon"], row["metric"]))
        if old is None:
            continue
        old_rho = None if old["rho"] == "" else float(old["rho"])
        rho = row["rho"]
        compared.append({"horizon": row["horizon"], "metric": row["metric"],
                         "metric_label": METRICS[row["metric"]],
                         "previous_players": prior["players"], "current_players": manifest["players"],
                         "train_seeds": row["train_seeds"], "validation_seeds": row["validation_seeds"],
                         "previous_rho": old_rho, "current_rho": rho,
                         "delta_rho": None if old_rho is None or rho is None else rho-old_rho,
                         "previous_rho_status": old["rho_status"], "current_rho_status": row["rho_status"]})
    write_csv(out / "tables/population_rank_comparison.csv", compared)
    return compared


def _population_comparison(out: Path, manifest: dict, tables: dict) -> list[str]:
    previous = _prior_population_experiment(out, manifest)
    if previous is None:
        return []
    prior_out, prior = previous
    prior_effects_path = prior_out / "tables/axis_effects.csv"
    with prior_effects_path.open(encoding="utf-8", newline="") as handle:
        previous_rows = list(csv.DictReader(handle))
    horizon = max(manifest["horizons"])
    prior_lookup = {r["axis"]: r for r in previous_rows if int(r["horizon"]) == horizon
                    and r["split"] == "validation" and r["metric"] == "cash_yuan"
                    and r["contrast_kind"] == "endpoint"}
    lines = ["", "### 扩大人口后，配对误差是否缩小？", "",
             f"在相同种子、模型和候选下，比较 N={prior['players']} 与 N={manifest['players']} 的 {horizon} 期复核结果。金额随人数扩大而增加，跨规模使用现金相对变化区间和人均现金差区间衡量精度。", "",
             "| 调参对照 | 每次人数 | 总现金相对变化 [95%区间] | 相对区间宽度（百分点） | 人均现金差 [95%区间，元/初始玩家] | 人均区间宽度（元） |",
             "|---|---:|---:|---:|---:|---:|"]
    comparison_rows = []
    for axis, label in (("mechanism_strength", "λ"), ("support_gain_scale", "a")):
        current = _axis(tables, horizon, "validation", axis, "cash_yuan")
        old = prior_lookup.get(axis)
        if not current or old is None:
            continue
        for players, row in ((prior["players"], old), (manifest["players"], current)):
            def number(key: str) -> float | None:
                value = row.get(key)
                return None if value in (None, "") else float(value)
            mean, low, high = number("mean_relative_pct"), number("relative_ci95_low"), number("relative_ci95_high")
            abs_mean, abs_low, abs_high = number("mean_diff"), number("ci95_low"), number("ci95_high")
            relative = "—" if mean is None else f"{mean:+.2f}%"
            if low is not None and high is not None:
                relative += f" [{low:+.2f}, {high:+.2f}]%"
            width = None if low is None or high is None else high - low
            person = "—" if abs_mean is None else f"{abs_mean/players:+.2f}"
            if abs_low is not None and abs_high is not None:
                person += f" [{abs_low/players:+.2f}, {abs_high/players:+.2f}]"
            person_width = None if abs_low is None or abs_high is None else (abs_high-abs_low)/players
            lines.append(f"| {label} {float(row['low_level']):g}→{float(row['high_level']):g} | {players} | {relative} | {_number(width)} | {person} | {_number(person_width)} |")
            comparison_rows.append({"axis": axis, "horizon": horizon, "split": "validation", "players": players,
                                    "mean_relative_pct": mean, "relative_ci95_low": low, "relative_ci95_high": high,
                                    "relative_ci_width_pp": width,
                                    "mean_per_initial_player_yuan": None if abs_mean is None else abs_mean/players,
                                    "per_player_ci95_low_yuan": None if abs_low is None else abs_low/players,
                                    "per_player_ci95_high_yuan": None if abs_high is None else abs_high/players,
                                    "per_player_ci_width_yuan": person_width})
    write_csv(out / "tables/population_precision_comparison.csv", comparison_rows)
    lines += ["", f"[N={prior['players']} 对照报告](../research_v2_stability/REPORT.md)与[精度比较表](tables/population_precision_comparison.csv)保留完整数值。两种人口规模共用种子与前段玩家，表中宽度变化是描述性比较。初始层级人数波动只是随机误差的一项，不能仅凭人数表确定配对差方差的主要来源。"]
    return lines


def _focus_candidates_section(out: Path, manifest: dict, tables: dict) -> list[str]:
    focus = ("L1_A1.4", "L1_A1.6")
    horizon = max(manifest["horizons"])
    sources = []
    previous = _prior_population_experiment(out, manifest)
    if previous is not None:
        prior_out, prior = previous
        prior_tables = {}
        for name in ("grid_summary", "paired_baseline", "inclusion_frequency"):
            with (prior_out / "tables" / f"{name}.csv").open(encoding="utf-8", newline="") as handle:
                prior_tables[name] = list(csv.DictReader(handle))
        sources.append((prior["players"], prior_tables))
    sources.append((manifest["players"], tables))
    exported = []
    for players, data in sources:
        selected = {r["policy_id"]: r for r in data["grid_summary"]
                    if int(r["horizon"]) == horizon and r["split"] == "validation"}
        paired = {r["candidate_id"]: r for r in data["paired_baseline"]
                  if int(r["horizon"]) == horizon and r["split"] == "validation" and r["metric"] == "cash_yuan"}
        frequency = {r["policy_id"]: r for r in data["inclusion_frequency"]
                     if int(r["horizon"]) == horizon and r["split"] == "validation"}
        for candidate in focus:
            if candidate not in selected:
                continue
            row, comparison, inclusion = selected[candidate], paired[candidate], frequency[candidate]
            def value(key: str) -> float | None:
                actual = comparison.get(key)
                return None if actual in (None, "") else float(actual)
            difference, low, high = value("mean_diff"), value("ci95_low"), value("ci95_high")
            exported.append({"horizon": horizon, "split": "validation", "players": players,
                "policy_id": candidate, "mechanism_strength": float(row["mechanism_strength"]),
                "support_gain_scale": float(row["support_gain_scale"]),
                "mean_cash_per_initial_player_yuan": float(row["cash_yuan"])/players,
                "retention_core": float(row["retention_core"]),
                "paired_cash_per_player_diff_yuan": None if difference is None else difference/players,
                "paired_cash_per_player_ci95_low_yuan": None if low is None else low/players,
                "paired_cash_per_player_ci95_high_yuan": None if high is None else high/players,
                "mean_relative_pct": value("mean_relative_pct"),
                "relative_ci95_low": value("relative_ci95_low"), "relative_ci95_high": value("relative_ci95_high"),
                "included_seeds": int(inclusion["included_seeds"]), "n_seeds": int(inclusion["n_seeds"])})
    if not exported:
        return []
    write_csv(out / "tables/focus_candidates_comparison.csv", exported)
    lines = ["", "### 预先指定的两项候选：λ=1，a=1.4 / 1.6", "",
             f"以下两项在扩样前由研究问题指定，统一比较 {horizon} 期复核样本。现金差以 λ=1、a=1 为基准，并除以各自初始人数；前沿入选按同一批 25 个候选的收入与核心留存共同计算。", "",
             "| 每次人数 | 候选 | 人均累计现金（元） | 核心留存 | 相对基准人均现金差 [95%区间，元] | 相对基准现金变化 [95%区间] | 前沿入选 |",
             "|---:|---|---:|---:|---:|---:|---:|"]
    for row in exported:
        person = row["paired_cash_per_player_diff_yuan"]
        low, high = row["paired_cash_per_player_ci95_low_yuan"], row["paired_cash_per_player_ci95_high_yuan"]
        person_text = "—" if person is None else f"{person:+.2f}"
        if low is not None and high is not None:
            person_text += f" [{low:+.2f}, {high:+.2f}]"
        cash_row = {"metric": "cash_yuan", **row}
        lines.append(f"| {row['players']} | λ={row['mechanism_strength']:g}, a={row['support_gain_scale']:g} | {_number(row['mean_cash_per_initial_player_yuan'])} | {_rate(row['retention_core'])} | {person_text} | {_interval(cash_row,relative=True)} | {row['included_seeds']}/{row['n_seeds']} |")
    lines += ["", "入选次数描述本轮复核结果；即使三次全部入选，也不等于已证明它是总体最优策略。未入选也不等于已排除其全部合理取舍。[关注候选比较表](tables/focus_candidates_comparison.csv)保留数值。"]
    return lines


def write_stability_report(out: Path, manifest: dict, tables: dict[str, list[dict]]) -> None:
    """用种子级配对结果生成研究报告和现金贡献图。"""
    out = Path(out)
    figures = out / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    horizons = sorted(manifest["horizons"])
    longest = max(horizons)
    rank = tables["rank_stability"]
    rank_index = {(r["horizon"], r["metric"]): r for r in rank}
    population_rank = _population_rank_comparison(out, manifest, rank)
    population_rank_index = {(r["horizon"], r["metric"]): r for r in population_rank}
    pilot_seeds = {manifest["train_seeds"][0], manifest["validation_seeds"][0]}
    pilot = compute_statistics([r for r in tables["run_summary"] if r["seed"] in pilot_seeds])["rank_stability"]
    write_csv(out / "tables/rank_stability_pilot.csv", pilot)
    pilot_index = {(r["horizon"], r["metric"]):r for r in pilot}
    assets = {
        "rank_stability.svg": _rank_figure(rank, horizons),
        "axis_cash_ci.svg": _effect_figure(tables["axis_effects"], metric="cash_yuan", relative=True),
        "axis_retention_ci.svg": _effect_figure(tables["axis_effects"], metric="retention_core", relative=False),
        "frontier_inclusion.svg": _frontier_figure(tables["inclusion_frequency"], longest),
    }
    decomposition = _cash_decomposition(tables, longest, "validation")
    if decomposition:
        assets["support_cash_decomposition.svg"] = _decomposition_figure(decomposition, longest, manifest["players"])
    for name, svg in assets.items():
        (figures / name).write_text(svg, encoding="utf-8")

    n_train, n_val = len(manifest["train_seeds"]), len(manifest["validation_seeds"])
    prior_runs = manifest.get("prior_experiment_runs", manifest["reused_runs"])
    expanded_runs = manifest.get("current_experiment_runs", manifest["new_runs"])
    seed_counts = (f"每次 {manifest['players']} 名初始玩家；{n_train} 组筛选 + {n_val} 组独立复核种子；"
                   f"25 个 P0 候选，观察 {' / '.join(map(str,horizons))} 期。")
    summary = []
    if population_rank:
        prior_players = population_rank[0]["previous_players"]
        summary.append(f"- **人口从 N={prior_players} 扩至 N={manifest['players']} 后的总现金排序**：保持相同 {n_train}+{n_val} 组种子，" + "；".join(
            f"{h} 期 ρ 从 {_rho({'rho':population_rank_index[h,'cash_yuan']['previous_rho']})} 变为 {_rho(rank_index.get((h,'cash_yuan')))}"
            for h in horizons if (h,"cash_yuan") in population_rank_index) + "。")
    else:
        summary.append(f"- **种子数从 1+1 扩至 {n_train}+{n_val} 后的总现金排序**：" + "；".join(
            f"{h} 期 ρ 从 {_rho(pilot_index.get((h,'cash_yuan')))} 变为 {_rho(rank_index.get((h,'cash_yuan')))}"
            for h in horizons) + "。")
    for axis, label in (("mechanism_strength", "机制锁 λ"), ("support_gain_scale", "专拐净增益 a")):
        cash = _axis(tables, longest, "validation", axis, "cash_yuan")
        ret = _axis(tables, longest, "validation", axis, "retention_core")
        if cash and ret:
            summary.append(f"- **{label}从 {cash['low_level']:g} 到 {cash['high_level']:g}**：{longest} 期复核样本的现金相对变化为 {_interval(cash,relative=True)}，核心留存变化为 {_interval(ret)}。方括号为种子配对的逐项 95% 区间。")
    a_cash = _axis(tables, longest, "validation", "support_gain_scale", "cash_yuan")
    if a_cash:
        ambiguous = a_cash.get("relative_ci95_low") is None or a_cash["relative_ci95_low"] <= 0 <= a_cash["relative_ci95_high"]
        summary.append("- **细分名次的边界**：专拐轴的现金区间仍跨零，保留现金尚不可区分的候选，结合留存与现金结构判断取舍；只有现金精度会改变决策时，再预先增加独立复核种子。" if ambiguous
                       else "- **细分名次的边界**：专拐轴现金变化的区间不跨零；具体候选的优劣仍须结合配对区间和独立复核前沿判断。")
    if decomposition:
        summary.append(f"- **专拐增益的现金结构**：{_decomposition_reading(decomposition)}")

    lines = ["# 机制锁与售后支援：跨种子配对复核", "", seed_counts, "", "## 核心发现", "", *summary, "",
        "## 1. 总现金和三类现金分别有多稳？", "",
        "Spearman ρ 比较同一指标在 25 个候选中的筛选均值排序与独立复核均值排序。ρ 越接近 1，排序越一致；负值表示次序趋于相反。候选均值完全相同时，相关系数未定义。[1]", "",
        "![现金结构与留存排序](figures/rank_stability.svg)", "",
        f"1+1 种子子样本固定使用配置中的首个筛选种子 {manifest['train_seeds'][0]} 与首个复核种子 {manifest['validation_seeds'][0]}；与完整 {n_train}+{n_val} 组采用相同人口 N、候选与模型。两组共享种子，ρ 变化用于描述扩样后的结果，不构成独立显著性检验。[1]", ""]
    if population_rank:
        prior_players = population_rank[0]["previous_players"]
        lines += [f"人口扩样的主对照是相同 {n_train}+{n_val} 组种子下 N={prior_players} 与 N={manifest['players']} 的完整结果；当前 N 的 1+1 子样本另列。人口规模以外的模型参数、候选和种子分组相同。[人口排序比较表](tables/population_rank_comparison.csv)保留差值。", "",
                  f"| 指标 | 观察期 | N={prior_players} 完整 {n_train}+{n_val} 组 ρ | N={manifest['players']} 的 1+1 子样本 ρ | N={manifest['players']} 完整 {n_train}+{n_val} 组 ρ |",
                  "|---|---:|---:|---:|---:|"]
    else:
        lines += [f"| 指标 | 观察期 | 1+1 种子子样本 ρ | 完整 {n_train}+{n_val} 组 ρ |",
                  "|---|---:|---:|---:|"]
    for metric, label in METRICS.items():
        for h in horizons:
            prior_cell = (f" {_rho({'rho':population_rank_index[h,metric]['previous_rho']})} |"
                          if population_rank and (h,metric) in population_rank_index else " — |" if population_rank else "")
            lines.append(f"| {label} | {h} |{prior_cell} {_rho(pilot_index.get((h,metric)))} | {_rho(rank_index.get((h,metric)))} |")
    lines += ["", "新主 C 池直购包含其本体、影画与专武目标；专拐直购对应支援商品池，当前目标为支援本体。月卡按实际续订现金记账，免费囤抽和已购月卡权益的消耗不再计作现金。三类现金合计等于总现金。[2]", "",
        "## 2. 调参效应有多大，区间有多宽？", "",
        "先在每个种子内对同一批玩家配对，再对种子差值求均值、标准误和 Student-t 近似 95% 区间。留存先按各种子固定的初始核心层或总体人数计算，再对种子等权平均。λ 的端点对照在同一种子内等权平均五档 a；a 的端点对照同理。10/20 期分别计算，保留现金百分比与留存百分点两种单位。[3]", "",
        "![现金配对变化与区间](figures/axis_cash_ci.svg)", "",
        "![核心留存配对变化与区间](figures/axis_retention_ci.svg)", "",
        "| 观察期 | 种子组 | 调参对照 | 配对种子数 | 现金相对变化 [95%区间] | 核心留存变化 [95%区间] |", "|---:|---|---|---:|---:|---:|"]
    for h in horizons:
        for split in ("train", "validation"):
            for axis, label in (("mechanism_strength", "λ"), ("support_gain_scale", "a")):
                cash, ret = _axis(tables,h,split,axis,"cash_yuan"), _axis(tables,h,split,axis,"retention_core")
                if cash and ret:
                    lines.append(f"| {h} | {SPLITS[split]} | {label} {cash['low_level']:g}→{cash['high_level']:g} | {cash['n_pairs']} | {_interval(cash,relative=True)} | {_interval(ret)} |")
    lines += ["", f"各候选相对基准 λ=1、a=1 的差值见[基准配对表](tables/paired_baseline.csv)；完整候选两两对照见[全候选配对表](tables/paired_all_candidates.csv)。标准误反映种子间差值的波动，不把同一种子中的五档支援或 {manifest['players']} 名玩家当作独立种子。"]
    lines += _decomposition_section(tables, horizons)
    lines += _population_comparison(out, manifest, tables)
    lines += ["",
        "## 3. 复核种子里的非支配集合", "",
        "收入与核心留存是两个独立目标。每个复核种子分别求 25 个候选的非支配集合，再记录入选次数；种子均值上的前沿另列。表中的前沿频率使用独立复核结果，方便识别只在少数种子中占优的候选。[4]", "",
        "![复核非支配集合入选次数](figures/frontier_inclusion.svg)"]
    lines += _focus_candidates_section(out, manifest, tables)
    lines += ["",
        "<details>", "<summary>25 个候选的长期复核成绩、相对基准区间与入选次数</summary>", "",
        "| λ | a | 复核总现金均值 | 核心留存 | 相对基准现金差 [95%区间，元] | 前沿入选 |", "|---:|---:|---:|---:|---:|---:|"]
    paired = {(r["horizon"],r["split"],r["candidate_id"],r["metric"]):r for r in tables["paired_baseline"]}
    freq = {(r["horizon"],r["split"],r["policy_id"]):r for r in tables["inclusion_frequency"]}
    for row in sorted([r for r in tables["grid_summary"] if r["horizon"]==longest and r["split"]=="validation"],
                      key=lambda r:(r["mechanism_strength"],r["support_gain_scale"])):
        diff = paired.get((longest,"validation",row["policy_id"],"cash_yuan"))
        inc = freq[(longest,"validation",row["policy_id"])]
        label = _interval(diff,currency=True) if diff else "基准"
        lines.append(f"| {row['mechanism_strength']:g} | {row['support_gain_scale']:g} | {_cash(row['cash_yuan'])} | {_rate(row['retention_core'])} | {label} | {inc['included_seeds']}/{inc['n_seeds']} |")
    shares = manifest.get("population_shares")
    expected = ("" if not shares else
                f"每次 {manifest['players']} 人下，Budget_160 与 Budget_500 的合计人数期望为 {manifest['players']*(shares['Budget_160']+shares['Budget_500']):g}，实际人数按种子记录。")
    lines += ["", "</details>", "", "## 4. 每个种子实际抽到了怎样的人群？", "",
        expected + "同一种子内所有候选共用初始人群；跨种子的误差同时包含人群构成、抽卡、付费意愿与流失随机性。人数表展示人群波动，但不单独识别各误差来源的方差贡献。核心层分母始终是初始 Budget_40 人数，停氪但仍活跃者仍计留存。[5]", "",
        "| 种子 | 用途 | 初始人数 | Budget_40 | Budget_160 | Budget_500 | 高预算合计 |", "|---:|---|---:|---:|---:|---:|---:|"]
    population: dict[int,dict[str,int]] = {}
    groups = {}
    for row in tables.get("population_by_seed",[]):
        population.setdefault(row["seed"],{})[row["player_tier"]] = row["initial_players"]
        groups[row["seed"]] = row["split"]
    for seed, counts in sorted(population.items()):
        high = counts["Budget_160"]+counts["Budget_500"]
        lines.append(f"| {seed} | {SPLITS[groups[seed]]} | {sum(counts.values())} | {counts['Budget_40']} | {counts['Budget_160']} | {counts['Budget_500']} | {high} |")
    lines += ["", "## 5. 实验成本与复现", "",
        f"本次主网格为 25 个候选 × {n_train+n_val} 个种子，共 {manifest['completed_runs']} 个 20 期运行，包含 {prior_runs} 个既有实验运行和 {expanded_runs} 个扩样运行。10 期结果从同一数据库的前 10 期截取，两个观察窗口不作为两份独立重复。", "",
        "模型公式与商品目录保持一致，每个候选的不同种子共用相同科学配置。人口规模属于科学配置身份，不同 N 使用独立目录。模块消融对照保留在[已有中等规模实验](../research_v2/REPORT.md)，心理参数敏感性按所属科学配置单独解释。逐期三类现金、囤抽、订阅库存和留存保留在[逐期结果](tables/period_outcomes.csv)。", "",
        "```powershell", f".\\.venv\\Scripts\\python.exe tools\\run_research_v2_stability.py --players {manifest['players']} --workers 4 --out outputs\\{out.name}", "```", "",
        "人口扩至 1000 或 3000 时使用独立目录：", "", "```powershell",
        ".\\.venv\\Scripts\\python.exe tools\\run_research_v2_stability.py --players 1000 --workers 4 --out outputs\\research_v2_stability_n1000", "```", "",
        "## 6. 适用范围", "",
        "置信区间描述指定模型下的随机误差，参数设定的不确定性另行检验。三组复核种子的尾部估计仍较粗；候选点估计第一或多次进入前沿，不等于已确定唯一最优策略。全候选两两比较采用逐项 95% 区间，没有整组同时覆盖保证，不能把筛选后最有利的区间当作经过多重比较控制的结论。", "",
        "玩家相互独立、个体分布稳定且配对现金差方差有限时，人均现金配对标准误理论上按 1/√N 缩放，总现金差的绝对元标准误则按 √N 增长；个体内部的非线性状态转移本身不改变这一结论。仅三组复核种子估计出的区间宽度仍有抽样波动，相对现金变化还使用随机的基期现金作分母，因此实际宽度比不必恰好等于理论缩放比。种子数不变时自由度仍为 2，参数设定的不确定性也未因此消除。跨人口规模比较使用相对或人均现金区间。三项现金分解是模型内的收入归属，不单凭点估计的正负证明现实中的替代或续订因果机制。", "",
        "Layer 1 的[机制条件成绩](../final_release/empirical_knob1_boss_lock.csv)和[专拐条件成绩](../final_release/empirical_knob2_support_lift.csv)提供业务参照，机制映射与付费心理系数仍是情景假设。订阅价格与权益沿用情景配置，不代表官方月卡商品的真实规格。模型只规划当前卡池的部分获取结果，不预知未来排期或求解跨期最优囤抽。固定初始队列不含获客、回流和运营成本，累计现金不等于游戏全盘流水或完整 LTV。", "",
        "## 数据与方法索引", "", "| 来源 | 内容 |", "|---|---|",
        "| [1] [排序复核](tables/rank_stability.csv) | 10/20 期总现金、三类现金与留存的秩相关 |",
        "| [1] [1+1 种子子样本](tables/rank_stability_pilot.csv) | 同人口与模型下预设种子子样本的排名复核 |",
        "| [2] [独立运行](tables/run_summary.csv) | 每种子每候选的现金、留存与运行身份 |",
        "| [3] [调参配对](tables/axis_effects.csv) | 端点与相邻档位的种子配对差、标准误与 t 区间 |",
        "| [现金分项配对](tables/axis_cash_decomposition.csv) | 三类现金变化及统一基期总现金分母下的贡献 |",
        "| [分项种子记录](tables/cash_decomposition_seed.csv) | 每个种子的基期、变化和可加贡献 |",
        "| [4] [前沿入选频率](tables/inclusion_frequency.csv) | 各候选按种子分别计算的非支配入选次数 |",
        "| [指定候选](tables/focus_candidates_comparison.csv) | λ=1、a=1.4/1.6 的人均现金配对差、核心留存与复核入选次数 |",
        "| [5] [初始人群](tables/population_by_seed.csv) | 每个种子的实际固定预算层人数 |",
        "| [候选汇总](tables/grid_summary.csv) | 各分组均值、标准误和种子均值前沿 |",
        "| [实验清单](experiment_manifest.json) | 科学配置、物理运行、种子分组、缓存来源和观察窗定义 |",
        "| [统计实现](../../tools/research_v2_stability_stats.py) | CRN 配对、Student-t 区间与秩相关计算 |",
        "| [研究协议](../../docs/development/research_v2_stability_protocol.md) | 预算、观察窗口、收入分拆与扩样规则 |", ""]
    if population_rank:
        lines.append(f"| [人口排序对照](tables/population_rank_comparison.csv) | 相同 {n_train}+{n_val} 种子下 N={population_rank[0]['previous_players']} 与当前人口的完整样本排序比较 |")
    (out / "REPORT.md").write_text("\n".join(lines),encoding="utf-8")
