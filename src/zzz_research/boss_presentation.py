"""Observed Deadly Assault bosses and archetype distributions for local outputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from zzz_research.dashboard_data import display
from zzz_research.dashboard_ui import escape, table
from zzz_research.report import observations_available


GROUPS = ("常规直伤/失衡", "异常/紊乱异放", "追加攻击", "命破贯穿",
          "帷幕无视失衡", "耀变体系", "锐化防御")
SHORT_GROUPS = ("直伤/失衡", "异常/紊乱", "追加攻击", "命破贯穿", "帷幕", "耀变", "锐化")
STAGE_SLOTS = {"1-1": "S1", "1-2": "S2", "1-3": "S3", "2-1": "ADV"}
PROXY_NOTE = ("广义机制换锁流（Ref_Mechanism）的业务原型涵盖秽盾、紫光控制技、暴伤锁与侵蚀壁垒。"
              "当前 L2/L3 数值实现仍以盾防御、盾期减伤、输出时间和支援增益作参数代理，"
              "没有逐项实现这些真实 Boss 的技能或对策。")


def _csv(path):
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _num(value):
    if value in (None, "", "None", "null"):
        return None
    return float(value)


def _version(value):
    return tuple(map(int, value.split("."))) if value else ()


def load_boss_presentation(out_dir):
    out = Path(out_dir)
    catalog = _csv(out / "dim_deadly_assault_bosses.csv")
    if not catalog:
        return None
    stats = _csv(out / "boss_mechanism_archetype_stats.csv") if observations_available(out) else []
    path = out / "boss_mechanism_stats_manifest.json"
    manifest = json.loads(path.read_text("utf-8")) if path.is_file() else {}
    return {"catalog": catalog, "stats": stats, "manifest": manifest}


def enrich_boss_coverage(out_dir, rows):
    """Annotate verified room codes without changing observation denominators."""
    data = load_boss_presentation(out_dir)
    catalog = {(r["combat_season"], r["stage_slot"]): r for r in data["catalog"]} if data else {}
    unresolved = {(r.get("combat_season"), r.get("stage_slot")) for r in data["stats"]
                  if r.get("scope") == "unresolved_room" and (_num(r.get("eligible_records")) or 0) > 0} if data else set()
    result = []
    for row in rows:
        row = dict(row)
        row["boss_display"] = "—"
        if row.get("mode") == "deadly_assault":
            stage = STAGE_SLOTS.get(row.get("stage_id"))
            boss = catalog.get((row.get("combat_season"), stage))
            if boss:
                row["boss_display"] = f'{boss["boss_zh"]} [{boss["mech_tag"]}]' + (" · 进阶路" if stage == "ADV" else "")
                if (row.get("combat_season"), stage) in unresolved:
                    row["boss_display"] += " · 存在原始 Boss 标签冲突"
            else:
                row["boss_display"] = "未关联 Boss（保留原始关卡）"
        result.append(row)
    return result


def _pivot(data, scope, *, stage_scope="normal"):
    groups = {}
    for row in data["stats"]:
        if row["scope"] != scope or row.get("stage_scope", "normal") != stage_scope:
            continue
        key = (row.get("combat_season", ""), row.get("stage_slot", ""), row.get("boss_zh", ""),
               row.get("mech_regime", ""), row.get("mechanism_code", ""))
        result = groups.setdefault(key, {**row})
        if row["archetype_group"] in GROUPS:
            result[f'family_{GROUPS.index(row["archetype_group"])}_share'] = _num(row.get("archetype_share"))
    return list(groups.values())


def _regimes(data):
    starts = {}
    for row in data["catalog"]:
        version = _version(row["combat_season"])
        key = row["mech_regime"]
        starts[key] = min(starts.get(key, version), version)
    return sorted(_pivot(data, "regime"), key=lambda r: starts.get(r["mech_regime"], (99,)))


def _columns(base):
    return base + [(f"family_{i}_share", group) for i, group in enumerate(SHORT_GROUPS)]


def _table(rows, columns, identity, **kwargs):
    return table(rows, columns, table_id=identity, formatter=display, **kwargs)


def _distribution_note():
    return ("各流派比例以身份有效、成员互异的完整三人通关记录为分母；多核可覆盖多个机制，比例不能相加。"
            "合格记录数另含人数不足或身份异常的队伍。成绩只对已知分数取中位数，普通三路与进阶路分开计算。")


def _limitations():
    return ("世代按赛季时间分组，不表示当期每个 Boss 都具有该世代的新机制。"
            "配队变化还受角色上线、模式与提交样本构成影响，描述同期间关联，不作为机制导致流派迁移的因果证据。")


def _source_note(data):
    catalog = data["catalog"]
    supplied_schedule = sum(row.get("schedule_source_status") == "supplied_schedule" for row in catalog)
    supplied_mechanism = sum(row.get("mechanism_source_status") == "supplied_mechanism_classification" for row in catalog)
    supplied_dates = len({row["combat_season"] for row in catalog if row.get("date_source_status") == "supplied_date"})
    pending = []
    if supplied_schedule:
        pending.append(f"{supplied_schedule} 条房间排期")
    if supplied_mechanism:
        pending.append(f"{supplied_mechanism} 条机制分类")
    if supplied_dates:
        pending.append(f"{supplied_dates} 期起始日期")
    note = "维表分别标注历史排期、日期与机制标签的来源状态。"
    if pending:
        note += "、".join(pending) + "采用提供资料，尚缺独立来源核验；其余条目保留原始快照、社区页面或公告依据。"
    note += "维表中的适配流派是机制推定，真实流派覆盖率按观测另算，二者不混用。"
    return note


def _summary(data):
    room_rows = _pivot(data, "season_room")
    active = [r for r in room_rows if (_num(r.get("eligible_records")) or 0) > 0]
    seasons = {r["combat_season"] for r in data["catalog"]}
    observed = {r["combat_season"] for r in _pivot(data, "season")
                if (_num(r.get("eligible_records")) or 0) > 0} or {r["combat_season"] for r in active}
    ordinary_records = sum(_num(r.get("eligible_records")) or 0 for r in _regimes(data))
    advanced_records = sum(_num(r.get("eligible_records")) or 0 for r in _pivot(data, "regime", stage_scope="advanced"))
    return (f'历史维表登记 {len(seasons)} 期、{len(data["catalog"])} 路 Boss；'
            f'本地合格通关覆盖其中 {len(observed)} 期、{len(active)} 路普通关卡。'
            f'普通三路共 {ordinary_records:,.0f} 条合格记录，进阶路共 {advanced_records:,.0f} 条，分开统计。'
            "观测缺期只保留历史映射，不补造通关记录。")


def _mechanism_examples(data):
    codes = ("miasma_shield", "miasma_plus_control_skill", "control_skill_ether_veil",
             "crit_lock_125_into_flames", "corruptive_barrier_anomaly", "corruptive_barrier_armorer")
    result = []
    for code in codes:
        boss = next((r for r in data["catalog"] if r.get("mechanism_code") == code and r["stage_slot"] != "ADV"), None)
        if boss is None:
            continue
        stats = [r for r in data["stats"] if r["scope"] == "mechanism" and r.get("mechanism_code") == code
                 and r.get("stage_scope", "normal") == "normal" and _num(r.get("archetype_share")) is not None]
        largest = max(stats, key=lambda r: _num(r["archetype_share"]), default=None)
        result.append({"boss_zh": boss["boss_zh"], "mech_tag": boss["mech_tag"],
                       "eligible_records": largest["eligible_records"] if largest else 0,
                       "observed_response": (f'{largest["archetype_group"]} {_num(largest["archetype_share"]):.2%}'
                                             if largest else "暂无本地合格样本")})
    return result


def _mechanism_comparison(data):
    codes = ("miasma_shield", "control_skill_ether_veil", "crit_lock_125_into_flames",
             "corruptive_barrier_anomaly", "corruptive_barrier_armorer")
    mechanisms = {row["mechanism_code"]: row for row in _pivot(data, "mechanism")}
    rows = []
    for code in codes:
        record = mechanisms.get(code)
        if record is None or not (_num(record.get("valid_3man_records")) or 0):
            continue
        shares = [(group, record.get(f"family_{i}_share")) for i, group in enumerate(GROUPS)]
        leading = sorted(((group, share) for group, share in shares if share is not None and share > 0),
                         key=lambda pair: pair[1], reverse=True)[:3]
        rows.append({**record, "leading_groups": "；".join(f"{group} {share:.2%}" for group, share in leading)})
    return rows


def _association_note(data):
    unresolved = _pivot(data, "unresolved_room") + _pivot(data, "unresolved_room", stage_scope="advanced")
    count = sum(_num(r.get("eligible_records")) or 0 for r in unresolved)
    if not count:
        return ""
    return (f"{count:,.0f} 条合格记录的原始 Boss 标签与对应排期不一致，仍计入赛季与世代统计，"
            "在具体 Boss、机制锁和精确房间统计中另列未决关联，不强行归入排期 Boss。")


def boss_html(out_dir, compact=False):
    data = load_boss_presentation(out_dir)
    if not data:
        return ""
    prefix = "boss-prototypes" if compact else "boss-evidence"
    if compact:
        table_rows = _regimes(data)
        return ('<article class="card section-gap"><h3>从真实 Boss 换锁到广义机制代理</h3>'
                f'<p>{escape(PROXY_NOTE)}</p><p class="mini-note">{escape(_limitations())}</p>'
                + _table(_mechanism_examples(data), [("boss_zh", "真实 Boss 原型"), ("mech_tag", "机制锁"),
                         ("eligible_records", "该机制普通路合格记录"), ("observed_response", "该机制最高覆盖流派")],
                         prefix + "-examples", page_size=6)
                + '<details><summary>五代机制背景下的真实配队分布</summary><div>'
                + _table(table_rows, _columns([("mech_regime", "真实机制世代"), ("valid_3man_records", "完整三人通关")]),
                         prefix + "-regimes", page_size=5)
                + '</div></details></article>')
    pieces = ['<article class="card section-gap" id="boss-evidence"><h3>危局 Boss 机制演进与实证阵容分布</h3>',
              f'<p>{escape(_summary(data))}</p><p class="mini-note">{escape(_distribution_note())}</p>',
              (f'<p class="mini-note">{escape(_association_note(data))}</p>' if _association_note(data) else ''),
              '<h3>五大首领机制锁世代 × 玩家真实流派响应</h3>',
              _table(_regimes(data), _columns([("mech_regime", "机制世代"), ("eligible_records", "合格通关"),
                     ("valid_3man_records", "完整三人通关"), ("known_score_records", "已知分数"),
                     ("median_score", "分数中位数")]), prefix + "-regimes", page_size=5),
              f'<p class="mini-note">{escape(_limitations())}</p>']
    pieces += ['<h3>五种具体机制锁的真实配队对照</h3>',
               _table(_mechanism_comparison(data), [("mech_tag", "具体机制锁"), ("valid_3man_records", "完整三人通关"),
                      ("leading_groups", "主要核心机制覆盖率（前三）"), ("median_score", "分数中位数")],
                      prefix + "-key-mechanisms", page_size=5),
               '<p class="mini-note">对照使用具体机制的普通路记录，区别于上表按时代汇总。'
               '多核覆盖可以重叠；配队差异描述样本中的关联，不单独归因为 Boss 机制。</p>']
    mechanisms = _pivot(data, "mechanism")
    pieces += ['<details class="collapsible-card"><summary>具体机制锁 × 配队流派覆盖率</summary><div>',
               _table(mechanisms, _columns([("mech_tag", "具体机制锁"), ("eligible_records", "合格通关"),
                      ("valid_3man_records", "完整三人通关"), ("median_score", "分数中位数")]),
                      prefix + "-mechanisms", page_size=12, search=True), '</div></details>']
    bosses = _pivot(data, "boss")
    pieces += ['<details class="collapsible-card"><summary>具体 Boss × 配队流派覆盖率与成绩</summary><div>',
               _table(bosses, _columns([("boss_zh", "真实 Boss"), ("mech_tag", "机制"), ("eligible_records", "合格通关"),
                      ("valid_3man_records", "完整三人通关"), ("median_score", "分数中位数")]),
                      prefix + "-bosses", page_size=12, search=True), '</div></details>']
    room_rows = _pivot(data, "season_room")
    room_rows.sort(key=lambda row: (_version(row["combat_season"]), row["stage_slot"]), reverse=True)
    pieces += ['<details class="collapsible-card"><summary>逐赛季、逐房间的 Boss 记录与分数</summary><div>',
               _table(room_rows, [("combat_season", "赛季"), ("stage_slot", "路数"), ("boss_zh", "Boss"),
                      ("mech_tag", "机制锁"), ("eligible_records", "合格通关"), ("valid_3man_records", "完整三人通关"),
                      ("known_score_records", "已知分数"), ("median_score", "分数中位数")],
                      prefix + "-rooms", page_size=12, search=True), '</div></details>']
    advanced = _pivot(data, "season_room", stage_scope="advanced")
    if advanced:
        pieces += ['<details class="collapsible-card"><summary>进阶路单列 · 不与普通三路混算</summary><div>',
                   _table(advanced, [("combat_season", "赛季"), ("boss_zh", "进阶 Boss"), ("mech_tag", "机制"),
                          ("eligible_records", "合格通关"), ("median_score", "分数中位数")], prefix + "-advanced", page_size=8),
                   '</div></details>']
    long_rows = [row for row in data["stats"] if row["scope"] == "season_room"
                 and (_num(row.get("archetype_records")) or 0) > 0]
    pieces += ['<details class="collapsible-card"><summary>逐 Boss、逐流派成绩明细 · 支持筛选与导出</summary><div>',
               _table(long_rows, [("combat_season", "赛季"), ("stage_slot", "路数"), ("boss_zh", "Boss"),
                      ("archetype_group", "主核机制"), ("archetype_records", "该流派记录"), ("archetype_share", "流派覆盖率"),
                      ("archetype_known_score_records", "流派已知分数"), ("archetype_median_score", "流派分数中位数")],
                      prefix + "-archetype-scores", page_size=12, search=True), '</div></details>',
               '<details><summary>Boss 排期、机制标签与来源口径</summary><div>',
               f'<p class="mini-note">{escape(_source_note(data))}</p>',
               '<p class="mini-note">数据文件：<a href="../dim_deadly_assault_bosses.csv">完整 Boss 排期与来源状态</a> · '
               '<a href="../boss_mechanism_archetype_stats.csv">机制与流派成绩统计</a>。'
               '</p></div></details></article>']
    return "".join(pieces)


def _md_table(rows, columns):
    clean = lambda value: str(value).replace("|", r"\|").replace("\n", " ")
    result = ['| ' + ' | '.join(label for _, label in columns) + ' |',
              '| ' + ' | '.join('---' for _ in columns) + ' |']
    for row in rows:
        result.append('| ' + ' | '.join(clean(display(key, row.get(key))) for key, _ in columns) + ' |')
    return '\n'.join(result) + '\n'


def boss_markdown(out_dir):
    data = load_boss_presentation(out_dir)
    if not data:
        return ""
    pieces = ['\n### 2.7 危局 Boss 机制演进与实证阵容分布\n', _summary(data) + '\n\n',
              _distribution_note() + '\n\n',
              (_association_note(data) + '\n\n' if _association_note(data) else ''),
              _md_table(_regimes(data), _columns([("mech_regime", "首领机制世代"), ("eligible_records", "合格通关"),
                        ("valid_3man_records", "完整三人通关"), ("median_score", "分数中位数")])),
              '\n五种具体机制锁的真实配队对照（普通路）：\n\n',
              _md_table(_mechanism_comparison(data), [("mech_tag", "具体机制锁"), ("valid_3man_records", "完整三人通关"),
                        ("leading_groups", "主要核心机制覆盖率（前三）"), ("median_score", "分数中位数")]),
              '\n具体机制对照与按时代汇总分别计算；多核覆盖可以重叠，不将配队差异单独归因为 Boss 机制。\n',
              '\n' + _limitations() + '\n\n' + _source_note(data) + '\n\n',
              '逐房间的 Boss、流派覆盖及各流派分数中位数见[Boss 机制与流派统计](../boss_mechanism_archetype_stats.csv)。'
              '完整排期、普通三路与进阶路、来源链接见[危局 Boss 维表](../dim_deadly_assault_bosses.csv)。\n',
              '\n' + PROXY_NOTE + '\n']
    bosses = _pivot(data, "boss")
    if bosses:
        pieces += ['\n具体 Boss 的普通路汇总：\n\n',
                   _md_table(bosses, [("boss_zh", "Boss"), ("mech_tag", "机制锁"), ("eligible_records", "合格通关"),
                             ("valid_3man_records", "完整三人通关"), ("known_score_records", "已知分数"),
                             ("median_score", "分数中位数")])]
    return ''.join(pieces)
