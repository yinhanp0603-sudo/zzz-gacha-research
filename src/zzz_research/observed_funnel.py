"""Sample denominators for published observations, read from existing exports."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path


def _json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_sample_funnel(out_dir: Path | str) -> dict | None:
    """Keep record eligibility, roster validity and boss matching as separate filters."""
    out = Path(out_dir)
    path = out / "observations/observations_manifest.json"
    if not path.exists():
        return None
    observed = _json(path)
    if not observed.get("observed_data_available"):
        return None
    archetypes = _json(out / "observations/archetype_evolution_manifest.json")
    bosses = _json(out / "boss_mechanism_stats_manifest.json")
    missing = sum(int(issue.split(":", 1)[1]) for issue in observed.get("quality_issues", [])
                  if issue.startswith("eligible_without_result:"))
    with (out / "empirical_knob1_boss_lock.csv").open(encoding="utf-8-sig", newline="") as stream:
        mechanisms = [row for row in csv.DictReader(stream) if row["scope"] == "mechanism"]
    tracks = {track: sum(int(row["n_clears"]) for row in mechanisms if row["stage_scope"] == track)
              for track in ("normal", "advanced")}
    boss_valid = sum(int(row["valid_3man_records"]) for row in bosses["stage_scope_totals"].values())
    exact = sum(tracks.values())
    eligible = int(observed["n_eligible"])
    valid = int(archetypes["valid_3man_records"])
    return {
        "raw_records": int(observed["n_battles"]),
        "eligible_records": eligible,
        "missing_result_records": missing,
        "known_result_records": eligible - missing,
        "valid_3man_records": valid,
        "invalid_or_partial_records": eligible - valid,
        "deadly_assault_valid_records": boss_valid,
        "deadly_assault_missing_result_records": sum(
            int(row["eligible_records"]) - int(row["known_score_records"])
            for row in bosses["stage_scope_totals"].values()),
        "unresolved_valid_records": boss_valid - exact,
        "mechanism_records": exact,
        "mechanism_normal_records": tracks["normal"],
        "mechanism_advanced_records": tracks["advanced"],
    }


def sample_funnel_rows(data):
    f = lambda key: f"{data[key]:,}"
    return [
        ("① 原始战斗记录（已导入）", data["raw_records"],
         "全量已导入战斗记录，尚未应用有效通关筛选。", "覆盖率与数据清洗起点。"),
        ("② 合格记录", data["eligible_records"],
         f"通过有效通关筛选；含 {f('missing_result_records')} 条既无评分也无用时的记录。",
         f"合格出场与覆盖统计。成绩统计另取 {f('known_result_records')} 条已知结果，并按模式与制式分开。"),
        ("③ 合法完整三人记录", data["valid_3man_records"],
         f"队伍须有三名身份已确认且互异的角色；排除 {f('invalid_or_partial_records')} 条单/双人或身份异常记录。",
         "配队结构、流派占比；专拐匹配在此基础上再限定主核、投资、第三人及有效成绩。"),
        ("④ 精确首领机制比较池（危局）", data["mechanism_records"],
         f"从第③级先取危局完整三人 {f('deadly_assault_valid_records')} 条，再剔除 {f('unresolved_valid_records')} 条首领关联未决记录。",
         f"λ 机制表、机制格内对策率与 HHI；普通路 {f('mechanism_normal_records')} 条、进阶路 {f('mechanism_advanced_records')} 条分开统计。"),
    ]


def _notes(data):
    missing_scope = (f"本次 {data['missing_result_records']:,} 条缺结果记录均位于式舆样本。"
                     if data["missing_result_records"] and not data["deadly_assault_missing_result_records"] else "")
    return (f"缺成绩是成绩比较的并行筛选条件，不从出场和配队计数中统一扣除。"
            f"评分与通关用时分别统计，中位数、分位数和成绩比仅使用各模式、关卡及匹配格内已知且有效的成绩；"
            + missing_scope +
            "第④级已限定危局，不能用全模式完整三人总数直接减去首领未决数作为其分母。"
            "机制配装匹配还要求单主核、已知影画/专武及相应养成条件，每行保留自身样本数。")


def funnel_markdown(out_dir):
    data = load_sample_funnel(out_dir)
    if data is None:
        return ""
    lines = ["### 四级样本漏斗：每张表使用哪个分母\n",
             "| 样本层级 | 记录数 | 筛选口径 | 使用范围 |",
             "| --- | ---: | --- | --- |"]
    lines += [f"| {stage} | {count:,} | {definition} | {use} |"
              for stage, count, definition, use in sample_funnel_rows(data)]
    lines += ["", _notes(data), "",
              "来源：[观测清单](../observations/observations_manifest.json)、"
              "[流派清洗清单](../observations/archetype_evolution_manifest.json)、"
              "[首领统计清单](../boss_mechanism_stats_manifest.json)、"
              "[机制实证表](../empirical_knob1_boss_lock.csv)（`scope=mechanism`；普通/进阶分别汇总）。\n"]
    return "\n".join(lines)


def funnel_html(out_dir):
    data = load_sample_funnel(out_dir)
    if data is None:
        return ""
    rows = "".join(f'<tr><th scope="row">{html.escape(stage)}</th><td>{count:,}</td>'
                   f'<td>{html.escape(definition)}</td><td>{html.escape(use)}</td></tr>'
                   for stage, count, definition, use in sample_funnel_rows(data))
    return ('<article class="card section-gap" id="observed-sample-funnel">'
            '<h3>四级样本漏斗：先看分母，再看比例</h3><div class="table-scroll">'
            '<table><thead><tr><th>样本层级</th><th>记录数</th><th>筛选口径</th><th>使用范围</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div><p class="mini-note">{html.escape(_notes(data))}</p>'
            '<p class="mini-note">来源：<a href="../observations/observations_manifest.json">观测清单</a> · '
            '<a href="../observations/archetype_evolution_manifest.json">流派清洗清单</a> · '
            '<a href="../boss_mechanism_stats_manifest.json">首领统计清单</a> · '
            '<a href="../empirical_knob1_boss_lock.csv">机制实证表</a>（scope=mechanism）</p></article>')
