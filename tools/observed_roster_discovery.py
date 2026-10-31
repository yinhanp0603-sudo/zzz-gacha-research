"""Read-only Layer 1 roster discovery; uses only the Python standard library."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import time
from array import array
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from statistics import median


ROOT = Path(__file__).resolve().parents[1]
# The era boundaries are the user's requested reporting bins, not character labels.
ERAS = (
    ("1.0–1.3", (1, 0), (1, 3)),
    ("1.4–1.7", (1, 4), (1, 7)),
    ("2.0–2.4", (2, 0), (2, 4)),
    ("2.5–2.8", (2, 5), (2, 8)),
    ("3.0–3.2", (3, 0), (3, 2)),
)
SCOPES_SQL = """
SELECT snapshot_id, combat_season, mode, COUNT(*)
FROM obs_battle WHERE is_eligible=1
GROUP BY snapshot_id, combat_season, mode
"""
ROSTERS_SQL = """
SELECT b.local_uid, b.scoring_regime, b.score_value, b.clear_time_seconds,
       m1.unit_id, m2.unit_id, m3.unit_id
FROM obs_battle b
LEFT JOIN obs_team_member m1 ON m1.battle_id=b.battle_id AND m1.team_slot=1
LEFT JOIN obs_team_member m2 ON m2.battle_id=b.battle_id AND m2.team_slot=2
LEFT JOIN obs_team_member m3 ON m3.battle_id=b.battle_id AND m3.team_slot=3
WHERE b.snapshot_id=? AND b.combat_season=? AND b.mode=? AND b.is_eligible=1
"""


def version_key(value):
    if value is None or not re.fullmatch(r"\d+(?:\.\d+)+", value):
        raise ValueError(f"Unrecognized observed season: {value!r}")
    return tuple(map(int, value.split(".")))


def era_for(season):
    version = version_key(season)[:2]
    return next((name for name, lo, hi in ERAS if lo <= version <= hi), "其他时期")


def alpha(values):
    return tuple(sorted(values, key=lambda unit: (unit.casefold(), unit)))


def table(headers, rows):
    def cell(value):
        return str(value).replace("|", r"\|").replace("\n", " ")
    return "\n".join([
        "| " + " | ".join(map(cell, headers)) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(map(cell, row)) + " |" for row in rows),
    ]) + "\n"


def discover(db, output):
    started = time.perf_counter()
    names = json.loads(Path(__file__).with_name("observed_unit_names_zh.json").read_text("utf-8"))
    names_zh = {unit: entry["name_zh"] for unit, entry in names["characters"].items()}
    source_stat = db.stat()
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA cache_size=-65536")
    con.execute("BEGIN")
    try:
        all_units = {row[0] for row in con.execute(
            "SELECT DISTINCT unit_id FROM obs_team_member "
            "WHERE unit_id IS NOT NULL AND TRIM(unit_id)<>''")}
        scopes = list(con.execute(SCOPES_SQL))
        expected = sum(row[3] for row in scopes)
        by_season = defaultdict(list)
        for snapshot, season, mode, count in scopes:
            version_key(season)
            by_season[season].append((snapshot, mode, count))
        lifetimes = {unit: {"first": None, "last": None, "n": 0,
                            "peak_n": 0, "peak_d": 1, "peak_season": None}
                     for unit in all_units}
        comps = Counter()
        values = defaultdict(lambda: array("d"))
        result_groups = set()
        comp_members = Counter()
        duos = Counter()
        counts = Counter()
        era_eligible, era_complete = Counter(), Counter()
        excluded = Counter()
        numeric_scopes = Counter()
        for season in sorted(by_season, key=version_key):
            era = era_for(season)
            players, unit_players = set(), defaultdict(set)
            for snapshot, mode, expected_scope in sorted(by_season[season]):
                actual_scope = 0
                for uid, regime, score, seconds, u1, u2, u3 in con.execute(
                        ROSTERS_SQL, (snapshot, season, mode)):
                    actual_scope += 1
                    counts["eligible"] += 1
                    era_eligible[era] += 1
                    members = [u for u in (u1, u2, u3) if u is not None and u.strip()]
                    units = alpha(set(members))
                    if any(unit.isdecimal() for unit in units):
                        numeric_scopes[season, mode] += 1
                    identity = (snapshot, uid) if uid is not None and uid.strip() else None
                    if identity is not None:
                        players.add(identity)
                    else:
                        counts["missing_uid"] += 1
                    for unit in units:
                        stat = lifetimes[unit]
                        stat["first"] = stat["first"] or season
                        stat["last"] = season
                        stat["n"] += 1
                        if identity is not None:
                            unit_players[unit].add(identity)
                    if len(members) != 3:
                        excluded[f"{len(members)}人队"] += 1
                        continue
                    if len(units) != 3:
                        excluded["三席含重复unit_id"] += 1
                        continue
                    counts["complete"] += 1
                    era_complete[era] += 1
                    comps[era, units] += 1
                    comp_members.update(units)
                    duos.update(combinations(units, 2))
                    group = (mode, regime)
                    result_groups.add(group)
                    value = seconds if regime == "time" else score if regime == "score" else None
                    if value is not None and math.isfinite(value):
                        values[era, units, group].append(value)
                    else:
                        counts["complete_missing_result"] += 1
                assert actual_scope == expected_scope, (season, mode, actual_scope, expected_scope)
            for unit, identities in unit_players.items():
                stat = lifetimes[unit]
                n, d = len(identities), len(players)
                if n * stat["peak_d"] > stat["peak_n"] * d:
                    stat.update(peak_n=n, peak_d=d, peak_season=season)
            print(f"season {season}: {counts['eligible']:,}/{expected:,} eligible records", flush=True)
        assert counts["eligible"] == expected
        assert sum(comps.values()) == counts["complete"]
        assert sum(duos.values()) == 3 * counts["complete"]
        assert sum(comp_members.values()) == 3 * counts["complete"]
    finally:
        con.close()
    after = db.stat()
    assert (source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns) == (
        after.st_ino, after.st_size, after.st_mtime_ns), "Source database changed during discovery"

    seasons = sorted(by_season, key=version_key)
    era_order = [name for name, _, _ in ERAS] + (["其他时期"] if era_complete["其他时期"] else [])
    groups = sorted(result_groups, key=lambda g: (str(g[0]), str(g[1])))
    mode_names = {"shiyu_defense": "式舆", "deadly_assault": "危局"}
    lifetime_rows = []
    for unit in sorted(lifetimes, key=lambda u: (
            lifetimes[u]["first"] is None, version_key(lifetimes[u]["first"]) if lifetimes[u]["first"] else (),
            -lifetimes[u]["n"], u.casefold(), u)):
        stat = lifetimes[unit]
        peak = (f"{stat['peak_n']/stat['peak_d']:.2%}" if stat["peak_season"] else "—")
        lifetime_rows.append([names_zh.get(unit, "—"), unit, stat["first"] or "—", stat["last"] or "—", f"{stat['n']:,}",
                              peak, stat["peak_season"] or "—",
                              f"{stat['peak_n']:,}/{stat['peak_d']:,}" if stat["peak_season"] else "—"])
    comp_rows = []
    for era in era_order:
        top = sorted(((units, n) for (e, units), n in comps.items() if e == era),
                     key=lambda r: (-r[1], r[0]))[:10]
        for rank, (units, n) in enumerate(top, 1):
            measures = []
            for group in groups:
                observed = values.get((era, units, group), ())
                if not observed:
                    measures.append("— (n=0)")
                else:
                    result = sum(observed) / len(observed) if group[1] == "time" else median(observed)
                    measures.append(f"{result:,.2f} (n={len(observed):,})")
            comp_rows.append([era, rank, " + ".join(names_zh.get(unit, unit) for unit in units),
                              f"{n:,}", f"{n/era_complete[era]:.2%}", *measures])
    frequency_order = sorted(duos, key=lambda pair: (-duos[pair], pair))
    jaccard = {pair: duos[pair] / (comp_members[pair[0]] + comp_members[pair[1]] - duos[pair])
               for pair in duos}
    # First take the requested 25 frequency leaders, then rank their binding strength.
    frequent = frequency_order[:25]
    frequency_rank = {pair: i for i, pair in enumerate(frequency_order, 1)}
    duo_rows = []
    for rank, pair in enumerate(sorted(frequent, key=lambda p: (-jaccard[p], -duos[p], p)), 1):
        a, b = pair
        both, na, nb = duos[pair], comp_members[a], comp_members[b]
        duo_rows.append([rank, frequency_rank[pair], names_zh.get(a, a), names_zh.get(b, b),
                         f"{both:,}", f"{na:,}", f"{nb:,}",
                         f"{both/counts['complete']:.2%}", f"{jaccard[pair]:.4f}",
                         f"{both/na:.2%}", f"{both/nb:.2%}"])
    measure_headers = [f"{mode_names.get(mode, mode)}·{'用时均值(s)' if regime == 'time' else '得分中位数'} / 已知n"
                       for mode, regime in groups]
    zero_units = sum(stat["n"] == 0 for stat in lifetimes.values())
    numeric_units = {unit: stat for unit, stat in lifetimes.items() if unit.isdecimal()}
    numeric_present = {unit: stat for unit, stat in numeric_units.items() if stat["n"]}
    numeric_seasons = sorted({season for stat in numeric_present.values()
                              for season in (stat["first"], stat["last"])}, key=version_key)
    numeric_note = (
        f"**待清洗信号：** {len(numeric_units)} 种标签为纯数字，其中 {len(numeric_present)} 种"
        f"出现在合格记录中，累计出场 {sum(stat['n'] for stat in numeric_present.values()):,} 次，"
        f"合格首末出现边界涉及赛季 {', '.join(numeric_seasons) or '—'}。"
        "它们是来源字段需要核对的信号，不直接认定为无效，也未自动删除、改名或并入其他角色。\n"
    )
    numeric_scope_note = "含纯数字标签的合格记录分布：" + (
        "；".join(f"`{season} / {mode}` {n:,} 条" for (season, mode), n in
                  sorted(numeric_scopes.items(), key=lambda item: (version_key(item[0][0]), item[0][1])))
        or "无") + "。\n"
    elapsed = time.perf_counter() - started
    text = [
        "# Layer 1 真实配队与角色生命周期探索\n",
        f"读取 **{counts['eligible']:,}** 条合格战斗记录，队伍成员字段中共有 **{len(all_units)}** 种非空唯一 `unit_id` 标签"
        f"（{len(all_units)-zero_units} 种出现在合格通关中）。"
        f"其中完整、互异的三人队 **{counts['complete']:,}** 条（{counts['complete']/counts['eligible']:.2%}）；"
        f"完整三人队有 **{counts['complete_missing_result']:,}** 条成绩未知。\n",
        f"实际战斗赛季范围：**{seasons[0]}–{seasons[-1]}**。所有标识与配队均直接来自观测库，未指定主输出、支援或流派标签。\n",
        numeric_note,
        numeric_scope_note,
        "## 1. 全量角色出场生命周期\n",
        "枚举 `obs_team_member` 中全部非空唯一标签；首次/末次赛季和出场次数只使用 `is_eligible=1` 的记录。"
        "每个角色在一条战斗中最多计一次，含不满三人的合格队伍；赛季按整数版本排序。"
        "首次/末次是样本中观察到的边界，不是官方上线/退场日期。\n",
        "`peak_player_share` 的分子为该赛季使用过此角色的去重样本身份，分母为该赛季全部合格记录的去重样本身份。"
        "身份键为 `(snapshot_id, local_uid)`：同一快照内跨模式/关卡去重，跨采集期不合并。"
        "这是样本玩家期出场占比，非持有率；峰值并列时列最早赛季，末列给出实际分子/分母。\n",
        "角色名以中文展示，原始英文 `unit_id` 保留用于核对；不属于角色名称映射的标签在中文名列记为 —。\n",
        table(["角色中文名", "原始 unit_id", "first_season", "last_season", "总合格出场次数", "peak_player_share", "峰值赛季", "峰值人数/样本人数"], lifetime_rows),
        "\n## 2. 各时期完整三人配队 Top 10\n",
        "`comp_key` 为三名互异角色按字母顺序排列后以 ` + ` 连接；角色顺位不影响配队身份。"
        "表中配队显示中文名，组合身份、排序及所有计数仍按原始英文标签计算。"
        "每时期按完整三人战斗记录频次降序取前十，频次相同按角色键排序。"
        "占比分母为该时期**全部完整三人合格战斗记录**，不是前十合计，也不是玩家人数；同一玩家的不同战斗分别计数。"
        "这里的完整三人按三席均非空、标签互异判定，尚未据角色字典验证标签身份。\n",
        "排名合并两种模式；成绩按模式及 `scoring_regime` 分列：计时取算术均值（秒），积分取中位数，"
        "两者只纳入成绩已知者，并列出已知 n。式舆的积分不并入危局得分。各时期样本量：\n",
        *[f"- **{era}**：合格 {era_eligible[era]:,} 条，完整三人 {era_complete[era]:,} 条。\n" for era in era_order],
        "\n" + table(["时期", "频次名次", "配队（中文）", "完整三人频次", "时期占比", *measure_headers], comp_rows),
        "\n## 3. 高频双人组中的 Jaccard 绑定排序（Top 25）\n",
        "先选共同出场次数最多的 25 对，再按 Jaccard 降序展示；同时保留全库频次名次。"
        "因此这张表是**高频前 25 对中的紧密度排序**，不是全库 Jaccard 前 25，"
        "避免仅出现一两次的搭档凭 100% 相似度占据榜首。不设人工支持度阈值或综合权重。\n",
        "集合元素为上表相同的**完整三人合格战斗记录**。设 A、B 为含各自角色的战斗集合，"
        "`Jaccard = |A∩B| / (|A|+|B|−|A∩B|)`；联合出场占比的分母为全部完整三人合格记录。"
        "`P(B|A)`、`P(A|B)` 分别展示方向性依赖。\n",
        table(["紧密度名次", "频次名次", "角色A（中文）", "角色B（中文）", "共同出场", "A出场", "B出场", "联合出场占比", "Jaccard", "P(B|A)", "P(A|B)"], duo_rows),
        "\n## 口径与使用边界\n",
        f"- 完整三人榜和双人榜排除 {sum(excluded.values()):,} 条合格记录："
        + ("；".join(f"{key} {n:,} 条" for key, n in sorted(excluded.items())) or "无") + "。角色生命周期仍保留这些记录。\n",
        f"- 全库角色中，{zero_units} 个未在合格通关中出现（其合格生命周期标为 —）；"
        f"合格记录中缺失玩家身份 {counts['missing_uid']:,} 条，不进入人数分母。\n",
        "- 频次反映社区合格通关样本的常见搭配，不等于游戏强度或持有率。模式、关卡、制度、"
        "记录数量和角色出现时段会影响总体排序；成绩汇总未控制上述差异，不能用于因果或等条件强度比较。\n",
        "- 全期 Jaccard 是观察窗口内的同队关联，不自动说明机制上的专属绑定；流派清洗可在审阅这些真实组合后另行定义。\n",
        "\n## 来源与复现\n",
        f"- 唯一统计数据输入：`{db.relative_to(ROOT).as_posix() if db.is_relative_to(ROOT) else db.as_posix()}`，"
        "表 `obs_battle`、`obs_team_member`；未读取原始 CSV 或仿真输出。\n",
        "- [探索脚本与精确 SQL](../../tools/observed_roster_discovery.py)：SQLite `mode=ro` 与 `query_only=ON`；"
        "按赛季流式汇总，不建索引，不改观测库、模型或测试。\n",
        "- [中文显示名映射](../../tools/observed_unit_names_zh.json)：按 ShiyuStats 角色 ID 关联 "
        "[Enka 角色文本键](https://github.com/EnkaNetwork/API-docs/blob/master/store/zzz/avatars.json)与"
        "[简体中文本地化](https://github.com/EnkaNetwork/API-docs/blob/master/store/zzz/locs.json)，"
        "并核对英文名；展示转换不修改原始标识或统计结果。\n",
        f"- 查询行数与各 scope 合格计数一致；完整队伍频次守恒，双人共现累计等于完整队伍数的三倍。"
        f"观测数据库文件身份、长度、修改时间保持不变。查询与汇总耗时 {elapsed:.2f} 秒。\n",
        "\n```powershell\n.\\.venv\\Scripts\\python.exe tools/observed_roster_discovery.py\n```\n",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(text), encoding="utf-8")
    print(f"Written {output}; {len(lifetime_rows)} units, {len(comp_rows)} compositions, {len(duo_rows)} duos; {elapsed:.2f}s", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "outputs/final_release/observations/observations.sqlite")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/final_release/observed_roster_discovery.md")
    args = parser.parse_args()
    discover(args.db.resolve(), args.out.resolve())


if __name__ == "__main__":
    main()
