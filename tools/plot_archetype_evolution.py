"""Export the observed seven-family coverage chart as SVG using Matplotlib."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GROUPS = (
    ("常规直伤/失衡", "常规直伤 / 失衡"),
    ("异常/紊乱异放", "异常积蓄 / 紊乱异放"),
    ("追加攻击", "追加攻击 · 零号安比 / 奥菲丝"),
    ("命破贯穿", "命破贯穿 · 仪玄 / 伊德海莉等"),
    ("帷幕无视失衡", "帷幕无视失衡 · 叶瞬光"),
    ("耀变体系", "耀变体系 · 蕾米埃尔（含多核）"),
    ("锐化防御", "锐化防御 · 克拉蕾"),
)


def render(data, output, font, preview=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.ticker import PercentFormatter

    font_manager.fontManager.addfont(str(font))
    family = font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams.update({
        "font.family": family, "font.size": 11, "axes.unicode_minus": False,
        "svg.fonttype": "path", "axes.edgecolor": "#9ca3af", "axes.linewidth": 0.7,
        "text.color": "#111827", "xtick.color": "#4b5563", "ytick.color": "#4b5563",
    })
    with data.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    seasons = sorted({r["combat_season"] for r in rows}, key=lambda s: tuple(map(int, s.split("."))))
    values = {(r["combat_season"], r["mechanism_group"]): r for r in rows}
    assert len(values) == len(rows) == len(seasons) * len(GROUPS)
    denominators = {}
    for season in seasons:
        ns = {int(values[season, group]["valid_3man_records"]) for group, _ in GROUPS}
        assert len(ns) == 1 and next(iter(ns)) > 0
        denominators[season] = next(iter(ns))
        for group, _ in GROUPS:
            row = values[season, group]
            assert abs(float(row["share"]) - int(row["records"]) / denominators[season]) < 1e-10

    ticks = [s for s in (seasons[0], "1.4.1", "2.0.1", "2.5.1", "3.0.1", seasons[-1]) if s in seasons]
    ticks = sorted(set(ticks), key=seasons.index)
    positions = [seasons.index(s) for s in ticks]
    fig, axes = plt.subplots(4, 2, figsize=(15, 13.2))
    fig.patch.set_facecolor("white")
    fig.subplots_adjust(left=0.062, right=0.968, top=0.855, bottom=0.085, hspace=0.49, wspace=0.19)
    fig.text(0.062, 0.975, "公开通关样本的核心流派演替", fontsize=22, weight="bold", va="top")
    fig.text(0.062, 0.938, "各机制的实际队伍覆盖率 · 每个面板统一为 0–100% · 多核队可同时进入多个面板", fontsize=12, color="#4b5563")
    x = list(range(len(seasons)))
    for ax, (group, title) in zip(axes.flat, GROUPS):
        y = [float(values[season, group]["share"]) * 100 for season in seasons]
        ax.fill_between(x, 0, y, color="#2563eb", alpha=0.16, linewidth=0)
        ax.plot(x, y, color="#2563eb", linewidth=1.8, marker="o", markersize=2.5)
        ax.set(xlim=(0, max(1, len(seasons) - 1)), ylim=(0, 100))
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
        ax.set_xticks(positions, ticks)
        ax.tick_params(axis="x", labelsize=10, pad=5, length=3)
        ax.tick_params(axis="y", labelsize=10, length=0)
        ax.set_axisbelow(True)
        ax.grid(axis="y", color="#e5e7eb", linewidth=0.7)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.set_title(title, loc="left", fontsize=13, weight="bold", pad=25)
        first = next((season for season, share in zip(seasons, y) if share > 0), "未观测到")
        ax.text(0, 1.04, f"首次观测 {first}", transform=ax.transAxes, fontsize=10, color="#6b7280")
        ax.text(1, 1.04, f"末期 {y[-1]:.1f}%", transform=ax.transAxes, ha="right", fontsize=10, color="#1d4ed8")
    note = axes.flat[-1]
    note.axis("off")
    total = sum(denominators.values())
    note.text(0, 1.10, "怎么读这张图", transform=note.transAxes, fontsize=14, weight="bold", va="top")
    paragraphs = [
        f"合法、互异的完整三人记录：{total:,} 条",
        f"观测赛季：{seasons[0]}–{seasons[-1]}（{len(seasons)} 个）",
        "分母：当季全部合法完整三人记录（两种模式合并）。",
        "分子：含该机制主核的队伍记录，同机制每队只计一次。",
        "多核可以覆盖多个机制，各面板比例不能相加。",
        "模式构成和采集覆盖的变化会影响走势。",
        "社区样本中的构成变化，不等同于全体玩家使用率。",
    ]
    for index, paragraph in enumerate(paragraphs):
        note.text(0, 0.85 - index * 0.135, paragraph, transform=note.transAxes,
                  fontsize=11, va="top", color="#4b5563")
    fig.text(0.062, 0.035, "横轴为实际观测赛季，按编号排列；等距位置不表示等天数。曲线节点对应真实观测，缺期不补造记录。",
             fontsize=10.5, color="#6b7280")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, format="svg", facecolor="white", metadata={
        "Title": "跨赛季核心流派覆盖率", "Creator": None,
        "Description": "Seven independent area panels. Numerator: complete valid teams containing each core family. Multiple families may overlap.",
    })
    svg = output.read_text("utf-8")
    svg = svg.replace("<svg ", f'<svg role="img" aria-label="{len(seasons)}个观测赛季的七类核心机制覆盖率，独立面积分面，多核覆盖可重叠" ', 1)
    output.write_text(svg, encoding="utf-8")
    if preview:
        preview.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(preview, dpi=130, facecolor="white")
    plt.close(fig)
    print(json.dumps({"path": str(output), "seasons": len(seasons), "panels": len(GROUPS),
                      "valid_3man_records": total}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "outputs/final_release/observations/archetype_evolution.csv")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/final_release/report/figures/archetype_evolution.svg")
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/msyh.ttc"))
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    render(args.data.resolve(), args.out.resolve(), args.font.resolve(),
           args.preview.resolve() if args.preview else None)


if __name__ == "__main__":
    main()
