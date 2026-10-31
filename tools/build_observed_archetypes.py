"""Build source-backed observed unit tags and classify every eligible composition."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCOPES_SQL = """
SELECT snapshot_id, combat_season, mode, COUNT(*)
FROM obs_battle WHERE is_eligible=1
GROUP BY snapshot_id, combat_season, mode
"""
ROSTERS_SQL = """
SELECT m1.unit_id, m2.unit_id, m3.unit_id
FROM obs_battle b
LEFT JOIN obs_team_member m1 ON m1.battle_id=b.battle_id AND m1.team_slot=1
LEFT JOIN obs_team_member m2 ON m2.battle_id=b.battle_id AND m2.team_slot=2
LEFT JOIN obs_team_member m3 ON m3.battle_id=b.battle_id AND m3.team_slot=3
WHERE b.snapshot_id=? AND b.combat_season=? AND b.mode=? AND b.is_eligible=1
"""
UNIT_COLUMNS = (
    "unit_id", "game_id", "name_zh", "specialty", "specialty_zh", "source_role",
    "anchor_kind", "anchor_role", "is_archetype_core", "mechanism_family", "mechanism_tags",
    "support_for_specialties", "support_role_label", "support_tags", "support_condition",
    "role_source_url", "specialty_source_url", "mechanism_source_url", "mechanism_evidence",
    "source_kind", "review_status", "classification_basis",
)
COMP_COLUMNS = (
    "comp_key", "comp_units", "comp_units_zh", "eligible_records", "member_count", "unique_member_count",
    "quality_status", "is_valid_3man", "has_invalid_identifier", "has_duplicate_units", "invalid_units",
    "core_units", "core_units_zh", "core_count", "carry_units", "engine_units", "flex_units", "flex_units_zh",
    "has_rupture_core", "rupture_core_units_zh", "rupture_support_units_zh", "support_mechanism_tags",
    "archetype_key", "archetype_label", "archetype_family", "mechanism_family", "mechanism_tags",
    "is_multi_core", "anomaly_member_count", "remielle_condition", "review_status",
)


def read_json(path):
    return json.loads(Path(path).read_text("utf-8"))


def as_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def alpha(values):
    return tuple(sorted(values, key=lambda unit: (unit.casefold(), unit)))


def write_csv(path, rows, columns):
    with Path(path).open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_unit_dimension(catalog, names, rules):
    if rules["schema_version"] != "observed_archetype_v1":
        raise ValueError("Unsupported observed archetype rule schema")
    rows = {}
    labels = {"carry": "Core Carry", "engine": "Core Engine", "flex": "Flex / Support",
              "carry_engine": "Core Carry / Engine"}
    for unit, info in sorted(catalog.items(), key=lambda item: int(item[1]["id"])):
        game_id, specialty = str(info["id"]), info["specialty"]
        profile = {**rules["defaults_by_specialty"][specialty], **rules["overrides"].get(game_id, {})}
        kind = profile.get("anchor_kind", rules["anchor_roles"][info["role"]]["kind"])
        if game_id in rules["user_flex_ids"]:
            kind = "flex"
        if kind not in labels:
            raise ValueError(f"Unknown anchor role: {unit}: {kind}")
        rows[unit] = {
            "unit_id": unit, "game_id": game_id, "name_zh": names[unit]["name_zh"],
            "specialty": specialty, "specialty_zh": rules["profession_labels"][specialty],
            "source_role": info["role"], "anchor_kind": kind, "anchor_role": labels[kind],
            "is_archetype_core": int(kind != "flex"), "mechanism_family": profile["family"],
            "mechanism_tags": as_json(profile["tags"]), "_tags": list(profile["tags"]),
            "support_for_specialties": as_json(profile.get("support_for_specialties", [])),
            "support_role_label": profile.get("support_role_label", ""),
            "support_tags": as_json(profile.get("support_tags", [])),
            "support_condition": profile.get("support_condition", ""),
            "_support_for_specialties": list(profile.get("support_for_specialties", [])),
            "_support_tags": list(profile.get("support_tags", [])),
            "role_source_url": rules["metadata_sources"]["role"],
            "specialty_source_url": rules["metadata_sources"]["specialty"],
            "mechanism_source_url": profile["source_url"], "mechanism_evidence": profile["mechanism_evidence"],
            "source_kind": profile["source_kind"], "review_status": profile["review_status"],
            "classification_basis": rules["anchor_role_basis"],
        }
    return rows


def classify_composition(members, units):
    """Keep raw membership; classification never chooses a winner among multiple cores."""
    members = alpha(members)
    unique = set(members)
    invalid = alpha(unique - units.keys())
    repeated = len(unique) != len(members)
    status = ("invalid_identifier" if invalid else "duplicate_units" if repeated else
              "complete_3man" if len(members) == 3 else "partial" if members else "empty_roster")
    known = sorted((units[u] for u in unique if u in units), key=lambda row: int(row["game_id"]))
    cores = [row for row in known if row["is_archetype_core"]]
    flex = [row for row in known if not row["is_archetype_core"]]
    carry = [row for row in cores if row["anchor_kind"] in ("carry", "carry_engine")]
    engine = [row for row in cores if row["anchor_kind"] in ("engine", "carry_engine")]
    rupture_cores = [row for row in cores if row["specialty"] == "Rupture"]
    rupture_support = [row for row in flex if "Rupture" in row["_support_for_specialties"]] if rupture_cores else []
    support_tags = sorted({tag for row in flex for tag in row["_support_tags"]})
    anomaly_count = sum(row["specialty"] == "Anomaly" for row in known)
    remielle = any(row["game_id"] == "1581" for row in cores)
    remielle_condition = ("three_anomaly" if status == "complete_3man" and anomaly_count == 3
                          else "not_three_anomaly") if remielle else "not_applicable"
    families = sorted({row["mechanism_family"] for row in cores})
    tags = {tag for row in cores for tag in row["_tags"]}
    if remielle:
        tags.difference_update(("耀变三异常", "耀变非三异常"))
        if remielle_condition != "three_anomaly":
            tags.discard("三异常强化")
        tags.add("耀变三异常" if remielle_condition == "three_anomaly" else "耀变非三异常")
    if invalid or repeated or not members:
        key, label, family = status, "待核对：" + status, ""
        families, tags = [], set()
    elif not cores:
        key, label, family = "no_core", "无已标注主核／纯插件队", "无已标注主核"
    else:
        signature = "+".join(f"{row['game_id']}:{row['specialty']}:{row['mechanism_family']}" for row in cores)
        key = "core=" + signature
        if remielle:
            key += ";remielle=" + remielle_condition
        label = " + ".join(f"{row['name_zh']}（{row['specialty_zh']}×{row['mechanism_family']}）" for row in cores)
        if len(cores) > 1:
            label = "多核组合：" + label
        if remielle:
            label += "［耀变三异常］" if remielle_condition == "three_anomaly" else "［耀变非三异常］"
        family = " + ".join(sorted({f"{row['specialty_zh']}×{row['mechanism_family']}" for row in cores}))
    return {
        "comp_key": " + ".join(members), "comp_units": as_json(members),
        "comp_units_zh": " + ".join(units[u]["name_zh"] if u in units else u for u in members),
        "member_count": len(members), "unique_member_count": len(unique), "quality_status": status,
        "is_valid_3man": int(status == "complete_3man"), "has_invalid_identifier": int(bool(invalid)),
        "has_duplicate_units": int(repeated), "invalid_units": as_json(invalid),
        "core_units": as_json([row["unit_id"] for row in cores]),
        "core_units_zh": " + ".join(row["name_zh"] for row in cores), "core_count": len(cores),
        "carry_units": as_json([row["unit_id"] for row in carry]),
        "engine_units": as_json([row["unit_id"] for row in engine]),
        "flex_units": as_json([row["unit_id"] for row in flex]),
        "flex_units_zh": " + ".join(row["name_zh"] for row in flex),
        "has_rupture_core": int(bool(rupture_cores)),
        "rupture_core_units_zh": " + ".join(row["name_zh"] for row in rupture_cores),
        "rupture_support_units_zh": " + ".join(row["name_zh"] for row in rupture_support),
        "support_mechanism_tags": as_json(support_tags),
        "archetype_key": key, "archetype_label": label, "archetype_family": family,
        "mechanism_family": " + ".join(families), "mechanism_tags": as_json(sorted(tags)),
        "is_multi_core": int(len(cores) > 1), "anomaly_member_count": anomaly_count,
        "remielle_condition": remielle_condition,
        "review_status": "|".join(sorted({row["review_status"] for row in cores})) or "not_applicable",
    }


def check_rules(units):
    classify = lambda members: classify_composition(members, units)
    a = classify(["Miyabi", "Astra Yao", "Nicole"])
    b = classify(["Miyabi", "Caesar", "Lighter"])
    assert a["archetype_key"] == b["archetype_key"]
    assert a == classify(["Nicole", "Miyabi", "Astra Yao"])
    assert all(not units[u]["is_archetype_core"] for u in ("Astra Yao", "Caesar", "Nicole", "Lighter", "Yuzuha"))
    hybrid = classify(["Miyabi", "Yixuan", "Astra Yao"])
    assert hybrid["core_count"] == 2 and hybrid["is_multi_core"]
    ordinary = classify(["Corin", "Ellen", "Astra Yao"])
    assert ordinary["core_count"] == 2 and len(json.loads(ordinary["core_units"])) == 2
    assert classify(["Astra Yao", "Nicole", "Caesar"])["archetype_key"] == "no_core"
    assert classify(["Miyabi", "126", "Astra Yao"])["quality_status"] == "invalid_identifier"
    assert classify(["Miyabi", "Agent Gulliver", "Nicole"])["quality_status"] == "invalid_identifier"
    assert classify(["Miyabi", "Miyabi", "Nicole"])["quality_status"] == "duplicate_units"
    partial = classify(["Miyabi", "Nicole"])
    assert partial["quality_status"] == "partial" and partial["archetype_key"] == a["archetype_key"]
    three = classify(["Remielle", "Velina", "Yuzuha"])
    assert three["remielle_condition"] == "not_three_anomaly" and "耀变三异常" not in json.loads(three["mechanism_tags"])
    assert "三异常强化" not in json.loads(three["mechanism_tags"])
    three = classify(["Remielle", "Velina", "Miyabi"])
    assert three["remielle_condition"] == "three_anomaly" and "耀变三异常" in json.loads(three["mechanism_tags"])
    assert "三异常强化" in json.loads(three["mechanism_tags"])
    lucia = classify(["Yixuan", "Lucia", "Ju Fufu"])
    pan = classify(["Yixuan", "Pan Yinhu", "Ju Fufu"])
    assert lucia["archetype_key"] == pan["archetype_key"]
    assert units["Lucia"]["anchor_kind"] == units["Pan Yinhu"]["anchor_kind"] == "flex"
    assert lucia["has_rupture_core"] and pan["has_rupture_core"]
    assert lucia["rupture_support_units_zh"] == units["Lucia"]["name_zh"]
    assert pan["rupture_support_units_zh"] == units["Pan Yinhu"]["name_zh"]
    assert "全队贯穿力增益" in json.loads(lucia["support_mechanism_tags"])
    assert not classify(["Miyabi", "Lucia", "Ju Fufu"])["rupture_support_units_zh"]
    print("Rule checks passed: flex invariance, support affinity, order, mixed/same-family cores, no-core, invalid/partial and three-anomaly condition.", flush=True)


def read_compositions(db):
    start = time.perf_counter()
    before = db.stat()
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA cache_size=-65536")
    con.execute("BEGIN")
    compositions, read_count = Counter(), 0
    try:
        scopes = list(con.execute(SCOPES_SQL))
        expected = sum(row[3] for row in scopes)
        for snapshot, season, mode, expected_scope in sorted(scopes, key=lambda row: (
                tuple(map(int, row[1].split("."))), row[0], row[2])):
            actual_scope = 0
            for row in con.execute(ROSTERS_SQL, (snapshot, season, mode)):
                compositions[alpha(u for u in row if u is not None and u.strip())] += 1
                actual_scope += 1
            assert actual_scope == expected_scope, (snapshot, season, mode, actual_scope, expected_scope)
            read_count += actual_scope
            print(f"{season} {mode}: {read_count:,}/{expected:,} eligible records", flush=True)
        assert read_count == expected == sum(compositions.values())
    finally:
        con.close()
    after = db.stat()
    assert (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns)
    seconds = time.perf_counter() - start
    print(f"SQLite read and composition aggregation: {seconds:.2f}s; {len(compositions):,} distinct compositions", flush=True)
    return compositions, expected, seconds


def md_table(headers, rows):
    escape = lambda value: str(value).replace("|", r"\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(map(escape, headers)) + " |",
                      "| " + " | ".join("---" for _ in headers) + " |",
                      *("| " + " | ".join(map(escape, row)) + " |" for row in rows)]) + "\n"


def write_summary(out, units, rows, expected, seconds, rules, refresh_only=False):
    quality_records, quality_comps, kinds = Counter(), Counter(), Counter()
    archetypes, labels = Counter(), {}
    variants = defaultdict(list)
    for row in units.values():
        kinds[row["anchor_role"]] += 1
    for row in rows:
        quality_records[row["quality_status"]] += row["eligible_records"]
        quality_comps[row["quality_status"]] += 1
        if row["is_valid_3man"]:
            archetypes[row["archetype_key"]] += row["eligible_records"]
            labels[row["archetype_key"]] = row["archetype_label"]
            variants[row["archetype_key"]].append(row)
    assert sum(quality_records.values()) == expected
    valid = quality_records["complete_3man"]
    core_total = sum(row["is_archetype_core"] for row in units.values())
    checked_cores = sum(row["is_archetype_core"] and row["review_status"] == "skill_text_checked"
                        for row in units.values())
    mixed_rows = [row for row in rows if row["is_valid_3man"] and row["is_multi_core"]]
    valid_rows = [(row, set(json.loads(row["comp_units"]))) for row in rows if row["is_valid_3man"]]
    rupture_units = [row for row in units.values() if row["specialty"] == "Rupture"]
    rupture_total = sum(row["eligible_records"] for row, _ in valid_rows if row["has_rupture_core"])
    rupture_comps = sum(row["has_rupture_core"] for row, _ in valid_rows)
    rupture_records = []
    for unit in rupture_units:
        selected = [(row, members) for row, members in valid_rows if unit["unit_id"] in members]
        rupture_records.append((
            unit["name_zh"], unit["anchor_role"], unit["mechanism_family"],
            f"{sum(row['eligible_records'] for row, _ in selected):,}",
            f"{sum(row['eligible_records'] for row, _ in selected if row['core_count'] == 1):,}",
            f"{sum(row['eligible_records'] for row, members in selected if 'Lucia' in members):,}",
            f"{sum(row['eligible_records'] for row, members in selected if 'Pan Yinhu' in members):,}",
        ))
    rupture_support_rows = []
    for unit in (units["Lucia"], units["Pan Yinhu"]):
        rupture_support_rows.append((
            unit["name_zh"], unit["anchor_role"], unit["support_role_label"],
            "、".join(unit["_support_tags"]), unit["support_condition"],
            "[技能依据](" + unit["mechanism_source_url"] + ")",
        ))
    yixuan_rows = sorted((row for row, members in valid_rows if "Yixuan" in members),
                        key=lambda row: (-row["eligible_records"], row["comp_key"]))[:5]
    examples = []
    for key in sorted(variants, key=lambda key: (-archetypes[key], key)):
        group = sorted(variants[key], key=lambda row: (-row["eligible_records"], row["comp_key"]))
        if len({row["flex_units"] for row in group}) > 1 and group[0]["core_count"] == 1:
            first = group[0]
            second = next(row for row in group[1:] if row["flex_units"] != first["flex_units"])
            examples.extend([first, second])
            break
    if mixed_rows:
        examples.append(max(mixed_rows, key=lambda row: row["eligible_records"]))
        examples.append(min(mixed_rows, key=lambda row: (row["eligible_records"], row["comp_key"])))
    content = [
        "# 公开队伍主核锚定与机制标签\n",
        f"为 **{len(units)}** 名角色建立标签，覆盖 **{expected:,}** 条合格战斗记录、"
        f"**{len(rows):,}** 种原始组合。合法完整三人记录 **{valid:,}** 条。\n",
        "## 维表与组合表\n",
        "- [dim_observed_archetype.csv](dim_observed_archetype.csv)：每角色一行，保留原始英文标识、游戏 ID、中文名、官方职业、社区定位、研究主核角色与机制证据。\n",
        "- [observed_comp_archetype.csv](observed_comp_archetype.csv)：每个无序原始组合一行，保留重复成员、异常标签与出场数；不以出现频次筛掉罕见混搭。\n",
        "职业由角色字典映射，并与 Enka 职业核对；社区 dps/subdps/support 是主输出、体系引擎、泛用插件的研究初始化，"
        "不等于官方队伍流派，也不识别战斗中的实际伤害份额。耀嘉音、凯撒、妮可、莱特、柚叶固定为 Flex / Support。\n",
        md_table(["研究定位", "角色数"], sorted(kinds.items())),
        f"\n规则版本 `{rules['schema_version']}`，资料口径日期 {rules['definition_date']}。"
        f"{core_total} 个主核／引擎中，{checked_cores} 个的特殊机制已按技能文本核对，"
        f"其余 {core_total-checked_cores} 个保留职业能力族基线；不据此推定具体失衡或异常结算事件。"
        "这套标签不逐历史版本复原技能平衡调整。\n",
        "\n## 组合判定\n",
        "核心签名包含所有主输出与体系引擎的游戏 ID、职业和机制族，按 ID 排序；不包含泛用插件。"
        "多核全部保留，双普通强攻也不合并成一个主核。更换普通插件不改变签名。"
        "纯插件队记为 `no_core`；未收录角色、邦布、数字标签归为 `invalid_identifier`，不猜成角色。\n",
        "不足三人的合法角色队伍仍可带主核签名，但与合法完整三人分析分母分开；重复成员和无效标签保留质量状态与次数。"
        "蕾米埃尔只有在完整互异三人队且三个角色官方职业均为异常时标“耀变三异常”，否则为“耀变非三异常”。\n",
        md_table(["质量状态", "组合数", "合格记录数"], [(key, f"{quality_comps[key]:,}", f"{n:,}")
                    for key, n in sorted(quality_records.items())]),
        f"\n合法完整三人中的多核组合 **{len(mixed_rows):,}** 种，覆盖 **{sum(row['eligible_records'] for row in mixed_rows):,}** 条记录；"
        "所有质量状态的记录数之和与输入合格记录数一致。\n",
        "## 机制族定义\n",
        "这些是基于职业能力和技能文本建立的分析族。仅有名单不能证明失衡、紊乱、异放、追加攻击或其他条件在该场战斗实际触发；"
        "具体能力与条件见 `mechanism_tags` 和每角色证据。\n",
        md_table(["机制族", "定义"], rules["family_definitions"].items()),
        "\n## 合法完整三人中的高频主核分类\n",
        md_table(["主核与机制", "记录数", "占合法完整三人记录"], [
            (labels[key], f"{n:,}", f"{n/valid:.2%}" if valid else "—") for key, n in archetypes.most_common(12)]),
        "\n## 命破队与贯穿辅助\n",
        f"合法完整三人中，至少含一名命破主核的组合 **{rupture_comps:,}** 种、记录 **{rupture_total:,}** 条；"
        "此总数按每个组合只统计一次，不重复计算双命破队。\n",
        "下表完整列出五名命破角色，不按高频榜截断。分母范围均为合法、互异的完整三人记录。"
        "“含该角色”包括单核与混搭队，“单核”要求整队只有一个已标注 Core；"
        "因此含角色统计与上方单核流派计数不同。双命破混搭会分别进入两位角色的行，"
        "同队辅助列也可能重叠，不能逐行相加当作总记录数。\n",
        md_table(["命破角色", "主核定位", "机制族", "含该角色记录", "单核记录", "与卢西娅同队", "与潘引壶同队"], rupture_records),
        "\n卢西娅与潘引壶仍为 Flex / Support，贯穿辅助能力单独记录，不进入主核签名。"
        "两者均非命破独占：卢西娅的基础增益不限职业，额外暴伤存在队友条件；"
        "潘引壶给特定入场代理人贯穿力，额外增伤另有职业或阵营条件。\n",
        md_table(["辅助", "主核分类", "专项适配定位", "能力标签", "条件", "来源"], rupture_support_rows),
        "\n" + rules.get("support_affinity_definition", "专项辅助档案记录能力与适配条件，不代表战斗中实际触发。") + "\n",
        "其他角色的 `support_for_specialties`、`support_tags` 或专项定位为空，表示尚未登记专项适配档案，"
        "不表示不能辅助命破。组合表的 `support_mechanism_tags` 记录队内插件的已登记辅助能力，"
        "不是据名单认定本场已触发；`rupture_support_units_zh` 仅在存在命破主核时列出已登记适配者。\n",
        "\n### 仪玄的真实高频完整三人队 Top 5\n",
        md_table(["完整配队", "主核", "全部泛用插件", "命破专项适配插件", "记录数"], [
            (row["comp_units_zh"], row["core_units_zh"], row["flex_units_zh"], row["rupture_support_units_zh"],
             f"{row['eligible_records']:,}") for row in yixuan_rows]),
        "\n## 实际观测组合示例\n",
        md_table(["配队", "主核分类", "记录数"], [(row["comp_units_zh"], row["archetype_label"], f"{row['eligible_records']:,}") for row in examples]),
        "\n## 来源与复现\n",
        "- [分类规则及技能证据](../../tools/observed_archetype_rules.json)；[构建脚本](../../tools/build_observed_archetypes.py)。\n",
        "- 统计仅来自已有 `observations/observations.sqlite` 的 `is_eligible=1` 记录；SQLite 使用 `mode=ro` 和 `query_only=ON`，"
        "不重导入、不建索引，数据库文件身份、长度、修改时间未改变。\n",
        (f"- 本轮仅从既有 {len(rows):,} 个组合的 `comp_units` 与 `eligible_records` 刷新标签和展示，"
         f"未重新读取观测库；原查询及组合汇总耗时 **{seconds:.2f} 秒**。\n" if refresh_only else
         f"- 查询及组合汇总耗时 **{seconds:.2f} 秒**。\n"),
        "- 规则语义检查覆盖插件替换、命破辅助适配、顺位、双核、纯插件、异常标识、不满三人与三异常条件。\n",
        "- 同一个机制标签可存在于多个主核或组合中，不能把重叠标签的记录数相加当作总量；"
        "社区成功通关样本的流派占比不代表全部玩家持有率、通关成功率或因果强度比较。\n",
        "\n```powershell\n.\\.venv\\Scripts\\python.exe tools/build_observed_archetypes.py\n```\n",
    ]
    (out / "observed_archetype_summary.md").write_text("\n".join(content), encoding="utf-8")


def build(db, out, rules_path):
    started = time.perf_counter()
    rules = read_json(rules_path)
    catalog = read_json(ROOT / "data/raw/SRC_SHIYU_meta_d930cec50bfd/characters.json")
    names = read_json(ROOT / "tools/observed_unit_names_zh.json")["characters"]
    units = build_unit_dimension(catalog, names, rules)
    check_rules(units)
    compositions, expected, seconds = read_compositions(db)
    rows = []
    for members, count in sorted(compositions.items(), key=lambda item: (-item[1], item[0])):
        row = classify_composition(members, units)
        row["eligible_records"] = count
        rows.append(row)
    assert sum(row["eligible_records"] for row in rows) == expected
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "dim_observed_archetype.csv", units.values(), UNIT_COLUMNS)
    write_csv(out / "observed_comp_archetype.csv", rows, COMP_COLUMNS)
    write_summary(out, units, rows, expected, seconds, rules)
    print(f"Published {len(units)} unit rows and {len(rows):,} compositions covering {expected:,} eligible records; total {time.perf_counter()-started:.2f}s", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "outputs/final_release/observations/observations.sqlite")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "outputs/final_release")
    parser.add_argument("--rules", type=Path, default=ROOT / "tools/observed_archetype_rules.json")
    args = parser.parse_args()
    build(args.db.resolve(), args.out_dir.resolve(), args.rules.resolve())


if __name__ == "__main__":
    main()
