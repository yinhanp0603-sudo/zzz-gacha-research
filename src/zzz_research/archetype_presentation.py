"""Shared presentation of observed archetypes for the report and local dashboard."""

from __future__ import annotations

import csv
import html
import json
from collections import Counter
from pathlib import Path


def _json(path: Path):
    return json.loads(path.read_text("utf-8-sig")) if path.exists() else None


def _csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _version(value: str) -> tuple[int, ...]:
    return tuple(map(int, value.split(".")))


def load_archetype_presentation(out: Path | str) -> dict | None:
    """Read existing descriptive outputs; never query or change a simulation run."""
    out = Path(out)
    observed = _json(out / "observations/observations_manifest.json") or {}
    stages = (_json(out / "pipeline_manifest.json") or {}).get("stages", {})
    if (observed.get("status") not in ("imported", "imported_with_issues")
            or observed.get("observed_data_available") is not True
            or ("prepare_observations" in stages
                and stages["prepare_observations"].get("status") != "completed")):
        return None
    units = _csv(out / "dim_observed_archetype.csv")
    compositions = _csv(out / "observed_comp_archetype.csv")
    if not units or not compositions:
        return None
    by_unit = {row["unit_id"]: row for row in units}
    valid = []
    for row in compositions:
        if row.get("is_valid_3man") != "1":
            continue
        valid.append({**row, "records": int(row["eligible_records"]),
                      "members": set(json.loads(row["comp_units"])),
                      "core_count": int(row["core_count"]),
                      "is_multi_core": row["is_multi_core"] == "1"})
    valid_total = sum(row["records"] for row in valid)
    if not valid_total:
        return None
    counts, labels = Counter(), {}
    for row in valid:
        counts[row["archetype_key"]] += row["records"]
        labels[row["archetype_key"]] = row["archetype_label"]
    top12 = [{"label": labels[key], "records": n, "share": n / valid_total}
             for key, n in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]]
    rupture_units = sorted((u for u in units if u["specialty"] == "Rupture"),
                           key=lambda u: int(u["game_id"]))
    rupture = []
    for unit in rupture_units:
        selected = [row for row in valid if unit["unit_id"] in row["members"]]
        rupture.append({"unit_id": unit["unit_id"], "name": unit["name_zh"],
                        "family": unit["mechanism_family"],
                        "records": sum(row["records"] for row in selected),
                        "single_core_records": sum(row["records"] for row in selected if row["core_count"] == 1),
                        "lucia_records": sum(row["records"] for row in selected if "Lucia" in row["members"]),
                        "pan_records": sum(row["records"] for row in selected if "Pan Yinhu" in row["members"])})
    rupture_ids = {unit["unit_id"] for unit in rupture_units}
    rupture_total = sum(row["records"] for row in valid if row["members"] & rupture_ids)
    support_rows = []
    for unit_id in ("Lucia", "Pan Yinhu"):
        unit = by_unit.get(unit_id)
        if unit:
            support_rows.append({"name": unit["name_zh"], "role": unit["anchor_role"],
                                 "support_role": unit.get("support_role_label", ""),
                                 "tags": "、".join(json.loads(unit.get("support_tags") or "[]")),
                                 "condition": unit.get("support_condition", ""),
                                 "source": unit["mechanism_source_url"]})
    pairings = []
    for core, support in (("Yixuan", "Lucia"), ("Yixuan", "Pan Yinhu"),
                          ("Ye Shunguang", "Zhao"), ("Ye Shunguang", "Sunna")):
        if core not in by_unit or support not in by_unit:
            continue
        core_n = sum(row["records"] for row in valid if core in row["members"])
        support_n = sum(row["records"] for row in valid if support in row["members"])
        both = sum(row["records"] for row in valid if {core, support} <= row["members"])
        pairings.append({"core": by_unit[core]["name_zh"], "support": by_unit[support]["name_zh"],
                         "core_records": core_n, "support_records": support_n, "both_records": both,
                         "support_given_core": both / core_n if core_n else None,
                         "core_given_support": both / support_n if support_n else None})
    evolution = _csv(out / "observations/archetype_evolution.csv")
    first_seen = {}
    phase_counts = Counter()
    phase_denominators = {}
    for row in evolution:
        season = row["combat_season"]
        family = row["mechanism_group"]
        n = int(row["records"])
        if n and (family not in first_seen or _version(season) < _version(first_seen[family])):
            first_seen[family] = season
        phase = ".".join(season.split(".")[:2])
        phase_counts[phase, family] += n
        phase_denominators[phase, season] = int(row["valid_3man_records"])
    phase_den = Counter()
    for (phase, _), n in phase_denominators.items():
        phase_den[phase] += n
    rupture_expands = (phase_den["1.7"] > 0 and phase_den["2.0"] > 0
                       and phase_counts["2.0", "命破贯穿"] / phase_den["2.0"]
                       > phase_counts["1.7", "命破贯穿"] / phase_den["1.7"])
    roles = Counter(row["anchor_role"] for row in units)
    return {"unit_count": len(units), "core_count": sum(row["is_archetype_core"] == "1" for row in units),
            "flex_count": sum(row["is_archetype_core"] == "0" for row in units), "role_counts": dict(roles),
            "composition_count": len(compositions), "valid_composition_count": len(valid),
            "eligible_records": sum(int(row["eligible_records"]) for row in compositions),
            "valid_records": valid_total, "top12": top12,
            "multi_core_compositions": sum(row["is_multi_core"] for row in valid),
            "multi_core_records": sum(row["records"] for row in valid if row["is_multi_core"]),
            "rupture": rupture, "rupture_total": rupture_total, "rupture_supports": support_rows,
            "pairings": pairings, "evolution": evolution, "first_seen": first_seen,
            "rupture_expands_in_2_0": rupture_expands,
            "evolution_manifest": _json(out / "observations/archetype_evolution_manifest.json") or {}}


def _bridge(data: dict, *, layer: str = "overview") -> str:
    if layer == "layer2":
        return ("上节的命破贯穿、帷幕易伤、耀变和锐化队伍，为防御区、机制覆盖和支援配对提供了业务原型。"
                "以下 Layer 2 以防御、盾期覆盖、输出对齐和支援增益作参数化代理，"
                "用固定队伍检验可控机制的敏感性，再由 Layer 3 推演显式购买与流失假设；"
                "模拟参数与行为结论仍按反事实情景解释。")
    first = data["first_seen"]
    if not first:
        return ("这些真实通关队伍中的主核与辅助搭配，为 Layer 2 的机制配对和 Layer 3 的抽卡、留存敏感性推演提供业务原型；"
                "后两层仍使用显式情景参数。")
    pieces = []
    rupture = first.get("命破贯穿")
    if rupture:
        suffix = "，2.0 阶段在样本中扩展" if data["rupture_expands_in_2_0"] else ""
        pieces.append(f"命破贯穿在观测赛季 {rupture} 已出现{suffix}")
    for label, family in (("叶瞬光帷幕队", "帷幕无视失衡"), ("蕾米埃尔耀变队", "耀变体系"),
                          ("克拉蕾锐化队", "锐化防御")):
        if family in first:
            pieces.append(f"{label}首见于 {first[family]}")
    return ("从早期常规直伤与异常体系出发，" + "；".join(pieces) + "。"
            "卢西娅／潘引壶、照／千夏与对应主核呈现高共现搭配。"
            "这些现象构成 Layer 2 参数化机制代理与 Layer 3 抽卡、留存敏感性推演的业务原型；"
            "观测赛季不等于角色上线版本，模拟参数与行为结论仍属显式情景假设。")


def bridge_text(out: Path | str, *, layer: str = "overview") -> str:
    data = load_archetype_presentation(out)
    return _bridge(data, layer=layer) if data else ""


def _md_table(headers, rows):
    def escape(value):
        return str(value).replace("|", r"\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(map(escape, headers)) + " |",
                      "| " + " | ".join("---" for _ in headers) + " |",
                      *("| " + " | ".join(map(escape, row)) + " |" for row in rows)]) + "\n"


def _pct(value):
    return "—" if value is None else f"{value:.2%}"


def _rule_paragraph(data):
    return (f"按现有角色字典为 {data['unit_count']} 名角色建立研究标签：{data['core_count']} 名主核／体系引擎、"
            f"{data['flex_count']} 名泛用插件。职业特性沿用游戏数据，Core Carry、Core Engine 与 Flex 是研究定位。"
            "耀嘉音、凯撒、妮可、莱特、柚叶等插件不进入主核签名；所有主核同时保留，换插件不改变流派。"
            "星见雅在角色字典中简称“雅”。标签按登记口径统一投射，不逐历史版本复原技能平衡调整。")


def _sample_paragraph(data):
    return (f"以下比例以 {data['valid_records']:,} 条身份有效、成员互异的完整三人通关记录为分母。"
            f"其中 {data['multi_core_compositions']:,} 种多核组合、{data['multi_core_records']:,} 条记录全部保留；"
            "人数不足或异常身份记录单列质量状态。")


def _evolution_note():
    return ("七个面积分面采用同一 0–100% 尺度：分子为当季包含对应核心机制的合法完整三人记录，"
            "分母为当季全部合法完整三人记录。同一多核队可覆盖多个机制，各分面占比可能合计超过 100%，"
            "不能相加或当作互斥份额。统计合并式舆与危局，按战斗记录而非去重玩家计数；"
            "模式构成与采集覆盖变化会影响走势。图示为队伍构成与能力标签，非战斗内技能实际触发率。")


def _top_rows(data):
    return [(row["label"], f"{row['records']:,}", _pct(row["share"])) for row in data["top12"]]


def _rupture_rows(data):
    return [(row["name"], row["family"], f"{row['records']:,}", f"{row['single_core_records']:,}",
             f"{row['lucia_records']:,}", f"{row['pan_records']:,}") for row in data["rupture"]]


def _pair_rows(data):
    return [(row["core"], row["support"], f"{row['both_records']:,}", f"{row['core_records']:,}",
             _pct(row["support_given_core"]), _pct(row["core_given_support"])) for row in data["pairings"]]


def archetype_markdown(out: Path | str) -> str:
    out = Path(out)
    data = load_archetype_presentation(out)
    if not data:
        return ""
    parts = ["\n### 2.1 主核锚定与流派清洗\n", _rule_paragraph(data) + "\n\n",
             _sample_paragraph(data) + "\n\n",
             "[角色标签维表](../dim_observed_archetype.csv)与[全部组合映射](../observed_comp_archetype.csv)"
             "保留原始标识、机制证据及辅助适配条件；[完整分类说明](../observed_archetype_summary.md)列出维护口径。\n",
             "\n### 2.2 跨赛季核心机制覆盖率\n"]
    if (out / "report/figures/archetype_evolution.svg").exists() and data["evolution"]:
        parts += ["![真实公开样本：跨赛季核心机制覆盖率](figures/archetype_evolution.svg)\n", _evolution_note() + "\n\n",
                  "数据见[逐赛季机制覆盖表](../observations/archetype_evolution.csv)，"
                  "计算见[统计脚本](../../../tools/build_archetype_evolution.py)和"
                  "[绘图脚本](../../../tools/plot_archetype_evolution.py)。\n"]
    else:
        parts.append("跨赛季机制覆盖尚未计算；以下汇总仅使用已完成的全期组合表。\n")
    parts += ["\n### 2.3 高频主核与多核流派 Top 12\n",
              _md_table(["主核与机制", "记录数", "占合法完整三人记录"], _top_rows(data)),
              "\n### 2.4 命破贯穿主核与专项辅助\n",
              f"至少含一名命破主核的合法完整三人记录共 {data['rupture_total']:,} 条，按队伍去重。"
              "下表每行统计含该角色的队伍，混搭可能进入多行；单核要求整队仅一个已标注主核，辅助列也可能重叠。\n\n",
              _md_table(["命破主核", "机制", "含该主核", "单核记录", "与卢西娅同队", "与潘引壶同队"], _rupture_rows(data)),
              "\n卢西娅、潘引壶都保留 Flex 定位；适配命破不表示仅命破可用，也不使辅助成为流派定义核。\n\n",
              _md_table(["辅助", "定位", "能力", "条件", "技能依据"], [
                  (row["name"], row["support_role"] or row["role"], row["tags"], row["condition"],
                   "[来源](" + row["source"] + ")") for row in data["rupture_supports"]]),
              "\n### 2.5 主核与插件的高共现搭配\n",
              _md_table(["主核", "插件", "同队记录", "含该主核记录", "插件｜主核", "主核｜插件"], _pair_rows(data)),
              "\n后两列分别是“含该主核的队伍中出现插件的比例”与反向条件比例。"
              "这是全期合格样本的同队关联，受角色出现时段和模式构成影响，不能据此认定插件专属、付费动机或留存因果。\n",
              "\n### 2.6 从真实配队变化到反事实机制推演\n", _bridge(data) + "\n"]
    return "\n".join(parts)


def _html_table(headers, rows):
    escape = lambda value: html.escape(str(value), quote=True)
    return ('<div class="archetype-table"><table><thead><tr>'
            + "".join("<th>" + escape(value) + "</th>" for value in headers)
            + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join("<td>" + escape(value) + "</td>" for value in row) + "</tr>" for row in rows)
            + "</tbody></table></div>")


def archetype_html(out: Path | str) -> str:
    """Tables and interpretation only; the caller embeds the shared SVG once."""
    data = load_archetype_presentation(out)
    if not data:
        return ""
    p = lambda text: "<p>" + html.escape(text) + "</p>"
    parts = ["<section class=\"observed-archetypes\"><h3>真实主核、插件与流派</h3>",
             p(_rule_paragraph(data)), p(_sample_paragraph(data))]
    if data["evolution"]:
        parts.append(p(_evolution_note()))
    parts += ["<h3>高频主核与多核流派 Top 12</h3>",
              _html_table(["主核与机制", "记录数", "占合法完整三人记录"], _top_rows(data)),
              "<h3>命破贯穿主核与专项辅助</h3>",
              p(f"至少含一名命破主核的合法完整三人记录 {data['rupture_total']:,} 条，按队伍去重。"
                "下表含角色与辅助搭配可能重叠；单核仅指整队一个已标注主核。"),
              _html_table(["命破主核", "机制", "含该主核", "单核记录", "与卢西娅同队", "与潘引壶同队"], _rupture_rows(data)),
              _html_table(["辅助", "定位", "能力", "条件"], [(row["name"], row["support_role"] or row["role"],
                      row["tags"], row["condition"]) for row in data["rupture_supports"]]),
              p("辅助适配不是排他的职业白名单；两位辅助保持 Flex，不进入主核签名。"),
              "<h3>主核与插件的高共现搭配</h3>",
              _html_table(["主核", "插件", "同队记录", "含该主核记录", "插件｜主核", "主核｜插件"], _pair_rows(data)),
              p("条件比例描述全期同队关联，受角色出现时段与模式构成影响，不表示插件专属、付费动机或留存因果。"),
              "<h3>从真实队伍到机制推演</h3>", p(_bridge(data)), "</section>"]
    return "\n".join(parts)
