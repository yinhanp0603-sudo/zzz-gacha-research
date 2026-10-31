"""Offline dashboard for the completed, population-expanded V2 experiment."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from zzz_research.dashboard_ui import CSS, SCRIPT, callout, chart, escape, table


PROXY_DESCRIPTION = (
    "当前 Layer 2/3 使用防御、输出窗口、职业／体系壁垒三个通道表达机制对口度；"
    "贯穿只绕过防御通道。支援提供相对可用旧辅的净增益和预设机制适配提升，"
    "不逐项还原真实 Boss 技能。公开成绩提供条件锚点，不能直接识别因果参数。"
)
TABS = (("data", "公开样本与口径"), ("combat", "战斗寿命与恢复"),
        ("behavior", "囤抽、付费与留存"), ("search", "候选策略与稳健性"))
LABELS = {
    "fixed_G1": "初始面板（不刷盘）", "growth_from_debut": "持续刷盘养成（上限+16%）",
    "none": "无限定支援", "train": "筛选（5种子）", "validation": "独立复核（3种子）",
    "cash_yuan": "累计现金", "new_main_topup_yuan": "新主C池直购",
    "support_topup_yuan": "专拐直购", "subscription_yuan": "月卡续订",
    "retention_core": "初始核心层留存", "retention_all": "初始全队列留存",
}


def _num(value, default=0):
    return default if value in (None, "") else float(value)


def _truth(value):
    return value is True or str(value).lower() == "true"


def _fmt(key, value):
    if value is None or value == "":
        return "—"
    if isinstance(value, str) and value in LABELS:
        return LABELS[value]
    if key == "mean_nondominated":
        return "★ 均值非支配" if _truth(value) else "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, str) and value.startswith("join_on_debut:"):
        return "配支援 " + value.split(":", 1)[1] + "（支援首发起）"
    if key in {"cash_yuan", "new_main_topup_yuan", "support_topup_yuan", "subscription_yuan",
               "mean_diff_yuan", "ci95_low_yuan", "ci95_high_yuan", "mean_cash_per_initial_player_yuan",
               "paired_cash_per_player_diff_yuan", "paired_cash_per_player_ci95_low_yuan",
               "paired_cash_per_player_ci95_high_yuan"}:
        return f"¥{float(value):,.2f}"
    if key in {"retention_core", "retention_all", "inclusion_frequency"}:
        return f"{float(value):.2%}"
    if key == "rho":
        return "未定义" if value is None else f"{float(value):.3f}"
    if key in {"mean_contribution_pp", "contribution_ci95_low_pp", "contribution_ci95_high_pp",
               "mean_relative_pct", "relative_ci95_low", "relative_ci95_high"}:
        return f"{float(value):+.2f}"
    if isinstance(value, (int, float)):
        return f"{value:g}" if abs(value) < 1000 else f"{value:,.0f}"
    return str(value)


def _table(rows, columns, ident, **kwargs):
    return table(rows, columns, table_id=ident, formatter=_fmt, **kwargs)


def _card(title, body, note=""):
    return (f'<article class="card"><h3>{escape(title)}</h3>'
            + (f'<p class="mini-note">{escape(note)}</p>' if note else "") + body + "</article>")


def _figure(out, name, note=""):
    path = out / "report/figures" / name
    return chart(path.read_text("utf-8"), note) if path.is_file() else ""


def _find(data, name, **criteria):
    return next((r for r in data["tables"]["n1000"].get(name, [])
                 if all(r.get(k) == v for k, v in criteria.items())), {})


def _endpoint(data, axis, metric="cash_yuan", horizon=20):
    return _find(data, "axis_effects", axis=axis, metric=metric, horizon=horizon,
                 split="validation", contrast_kind="endpoint")


def _signed(value, suffix="", digits=2):
    return "—" if value is None else f"{float(value):+.{digits}f}{suffix}"


def effect_text(row):
    """Cash effects are percentages; exported retention differences are already pp."""
    if not row:
        return "暂无配对估计"
    if row.get("metric") == "cash_yuan":
        keys, unit = ("mean_relative_pct", "relative_ci95_low", "relative_ci95_high"), "%"
    else:
        keys, unit = ("mean_diff", "ci95_low", "ci95_high"), "pp"
    values = [_signed(row.get(k)) for k in keys]
    return f"{values[0]}{unit} [{values[1]}, {values[2]}]{unit}"


def _design(data):
    m = data["manifest"]
    return (m.get("players", "—"), len(m.get("train_seeds", [])),
            len(m.get("validation_seeds", [])), m.get("simulation_horizon", 20))


def _rho(data, metric, horizon=20):
    row = _find(data, "rank_stability", horizon=horizon, metric=metric)
    return "—" if row.get("rho") is None else f'{float(row["rho"]):.3f}'


def _fixed_rows(data, name):
    return [r for r in data["tables"]["fixed"].get(name, [])
            if r.get("policy_id") == "L1_A1" and r.get("combat_variant") == "v2"
            and int(r.get("horizon", 0)) == 20]


def lifetime_summary(rows):
    """KM medians retain censoring and use the first observed failure endpoint."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row["investment_plan"], row["gear_policy"], row["support_policy"])].append(row)
    result = []
    for (investment, gear, support), subjects in sorted(groups.items()):
        survival, median = 1.0, None
        times = sorted({_num(r["first_failure_or_censor_days"]) for r in subjects})
        for time in times:
            at_risk = sum(_num(r["first_failure_or_censor_days"]) >= time for r in subjects)
            events = sum(_num(r["first_failure_or_censor_days"]) == time
                         and _truth(r["first_failure_event"]) for r in subjects)
            if at_risk:
                survival *= 1 - events / at_risk
            if survival <= .5 and median is None:
                median = time
        result.append({"investment_plan": investment, "gear_policy": gear, "support_policy": support,
                       "subjects": len(subjects),
                       "events": sum(_truth(r["first_failure_event"]) for r in subjects),
                       "median_days": median if median is not None else "观察期内未达中位数"})
    return result


def _tuples(rows, headers, ident, **kwargs):
    """Report helpers return display tuples; the dashboard table expects keyed rows."""
    keys = [f"c{i}" for i in range(len(headers))]
    return table([dict(zip(keys, row)) for row in rows], list(zip(keys, headers)), table_id=ident, **kwargs)


def _real_tracks(out, data):
    from zzz_research.publication_report import (GENERATIONS, event_window_rows, generation_sentence,
                                                 real_dps_generation_rows, real_dps_summary_rows)
    if not data.get("evidence", {}).get("real_dps_longevity"):
        return ""
    body = '<h3 class="section-gap">实证轨 · 真实主C的跨赛季保值轨迹</h3>'
    body += '<div class="chart-grid">' + _figure(out, "l1_real_dps_retention.svg", "Δt=0 为该主C首次进入危局合格样本的赛季；艾莲、朱鸢、简首发早于数据起点。") + '</div>'
    body += _card("经验半衰期 · 出场占比跌破自身峰值 50%／25% 的时点", _tuples(real_dps_summary_rows(data),
                  ["主C", "模式", "Δt=0 赛季", "峰值出场占比（赛季）", "跌破峰值50%", "跌破峰值25%", "最新赛季／峰值", "观测赛季数"],
                  "real-dps-longevity", page_size=16, search=True),
                  "分母为当期合法完整三人记录。出场占比下降同时包含新角色竞争、机制对口与样本构成变化，不等同于持有者退坑。")
    body += _card("0+1 单主核的危局相对分数 · 按机制世代与对口度", _tuples(real_dps_generation_rows(data), ["主C", *GENERATIONS],
                  "real-dps-generation", page_size=8),
                  "相对分数 = 分数 ÷ 同赛季同房间全部合法记录中位数；单元格为各房间相对分 P50 的中位数。" + generation_sentence(data))
    body += _card("售后专拐上线的事件窗口 · 首发前2期 vs 首发当期及后2期", _tuples(event_window_rows(data),
                  ["主C + 插件", "危局事件期", "主C出场占比变化（危局／式舆）", "带新插件／带旧辅 n", "带新插件 相对分 P50 [P25–P75]",
                   "带旧辅 相对分 P50 [P25–P75]", "层内中位数比几何均值（稳定层）", "事件前 旧辅 P50 [P25–P75]"],
                  "real-support-events", page_size=8),
                  "成绩对照固定主核0+1、第三名队友身份，并要求旧辅助与新插件同职业；比值为条件关联，不是售后因果增益。潘引壶、照与主C同期首发，无事件前窗口。")
    return body


def _support_systems(data):
    from zzz_research.publication_report import anomaly_ecosystem_rows, armorer_room_rows, support_archetype_rows
    if not data.get("evidence", {}).get("support_archetypes"):
        return ""
    body = '<h3 class="section-gap">实证 · 辅助为谁服务：体系归属、混合型与换代</h3>'
    body += _card("辅助的体系类别（2025 年起上线）", _tuples(support_archetype_rows(data),
                  ["辅助", "职业", "上线", "类别", "机制流派组", "强搭配主核数", "强搭配主核（* 售后）"], "support-archetypes", page_size=14, search=True),
                  "搭配率 = 主核的完整三人记录中带该辅助的比例，只计辅助上线后的赛季；≥30% 为强搭配。单核／体系／跨体系／泛用按强搭配主核的数量与流派组划分。")
    body += _card("蕾米埃尔前后的异常体系（危局）", _tuples(anomaly_ecosystem_rows(data),
                  ["赛季", "开始", "三异常队伍占比", "异常队中带蕾米埃尔", "带维琳娜", "带浮波柚叶", "带南宫羽", "带耀嘉音", "简出场", "柏妮思出场", "克拉蕾出场"],
                  "anomaly-ecosystem", page_size=14),
                  "混合型蕾米埃尔上线后，三异常队伍从 0% 升至三到四成；被挤出的是非异常的浮波柚叶、南宫羽、耀嘉音，旧异常主C简、柏妮思回到场上。")
    body += _card("锋御换代：针对锋御、压制异常的首领房间", _tuples(armorer_room_rows(data),
                  ["赛季", "房间", "首领", "机制", "对策组", "异常队占比", "锋御队占比"], "armorer-rooms", page_size=12),
                  "库萨里库（异常伤害 −40%、锋御识破）房间里异常队降到个位数，同期偏向异常的房间仍为九成左右。之后上线的菲欧妮为异常职业、蕾米埃尔队友（项目方说明）。")
    return body


def _archetype_experiment(data):
    from zzz_research.publication_report import (archetype_b_rows, archetype_decomposition_rows,
                                                 archetype_displacement_rows, archetype_selection_rows)
    if not data["tables"].get("archetype"):
        return ""
    body = '<h3 class="section-gap">虚拟实验 · 售后类型、环境与新体系换代</h3>'
    body += callout("同一批玩家、不同组合：把专拐、环境与新角色竞争拆开",
                    "合成世界含大体系 L（异常型）、小体系 S（命破型）与第 14 期起的新体系 N（锋御型）。A 组在第 7 期给 L 上线无效对照／窄专拐／体系专拐／混合型（副输出份额 ρ 四档），"
                    "并切换第 7–9 期是否对口与 λ；B 组在第 14–16 期加入压制 L 的换代锁，并在第 15 期给 L 出售后。每格 N=200、3 个复核种子、全日志。")
    body += _card("A 组 · 旧主C上场比例的拆分（pp）", _tuples(archetype_decomposition_rows(data),
                  ["λ", "商品", "朴素前后变化（对口）", "时间趋势", "环境效应", "商品效应·对口 [95%]", "商品效应·非对口", "交互", "核心留存差·对口 [95%，pp]"],
                  "archetype-decomposition", page_size=12),
                  "朴素前后变化 = 时间趋势 + 环境效应 + 商品效应。环境效应是专拐效应的数倍，现实事件窗口中的出场变化不能直接读作专拐效果。")
    body += _card("A 组 · 旧专拐被替代", _tuples(archetype_displacement_rows(data),
                  ["λ", "商品", "环境", "新商品占辅助位", "旧专拐占辅助位（对照格 → 本格）", "新商品作为主C上场", "含混合型队伍中它坐辅助位"], "archetype-displacement", page_size=8),
                  "混合型坐辅助位时按份额 ρ 计入自身伤害。现实中带蕾米埃尔的队伍几乎都含其他异常主核，对应 ρ≥0.5；此时混合型挤掉旧专拐、让旧主C重回场上。")
    body += _card("A 组 · 选择偏差：观测型 vs 实验型比值", _tuples(archetype_selection_rows(data),
                  ["λ", "商品", "环境", "观测型比值", "实验型比值", "选择偏差", "持有／未持有人数"], "archetype-selection", page_size=12),
                  "模拟玩家只有预算带来的养成差异，没有操作差异，所以这里的选择偏差是下限。")
    body += _card("B 组 · 新体系换代锁与旧主流售后（λ=1）", _tuples(archetype_b_rows(data),
                  ["指标", "换代锁效应（无售后）[95%]", "售后效应（有换代锁）[95%]", "售后挽回比例"], "archetype-generation", page_size=8),
                  "换代锁改变上场结构的方向与现实一致，但幅度弱于现实（现实按房间换队），辅助池需求偏低使现金结论不可用。")
    return body


def _combat(out, data):
    rows = _fixed_rows(data, "l2_reference_lifetimes")
    pairs = data.get("combat_pairs", {})
    paired = [r for r in _fixed_rows(data, "l2_support_pair_effects")
              if r.get("investment_plan") == "0+1" and r.get("gear_policy") == "growth_from_debut"
              and r.get("support_policy", "").split(":")[-1] in pairs.get(r.get("anchor_unit_id"), {})]
    heading = ('<div class="panel-heading"><h2>投资、养成与售后：老主C能用多久？</h2><p class="subtitle">Layer 2 · 真实主C经验轨迹 ＋ λ=1、a=1 固定参考队伍同条件对照</p></div>')
    body = heading + callout("为什么本页同时展示真实角色与 DPS_01～DPS_16？",
                             "真实角色（如仪玄、星见雅、简）反映 56 期实际排期下的经验出场率与条件分数轨迹；"
                             "DPS_01～DPS_16 则固定首发间隔与养成节奏，用于在完全相同的 Boss 排期下剥离登场时间混杂，"
                             "单独测量“首次失效寿命”与“后期专拐恢复”。")
    body += _real_tracks(out, data)
    body += _support_systems(data)
    body += _archetype_experiment(data)
    body += '<h3 class="section-gap">受控轨 · 固定参考队伍（DPS_*／S_*）</h3>'
    body += callout("先区分两种保值", "首次失效寿命衡量从首发到第一次低于满星线的时间；后期支援可以让已失效的队伍重回满星，却不能改写过去的失效事件。应同时看首次寿命、恢复时间与新增满星期数。", green=True)
    body += '<div class="chart-grid">' + _figure(out, "v2_l2_natural_km.svg") + _figure(out, "v2_l2_support_recovery.svg") + '</div>'
    body += _card("同条件配对 · 预先登记的主C—支援组合", _table(paired,
                  [("anchor_unit_id", "参考主C"), ("support_policy", "支援首发条件"),
                   ("natural_first_failure_or_censor_days", "无支援首次失效/删失（天）"),
                   ("first_failure_or_censor_days", "配支援首次失效/删失（天）"),
                   ("first_recovery_age_days", "首次重回满星（天）"),
                   ("incremental_full_star_periods", "净增满星期数"),
                   ("first_failure_event", "已观察到首次失效")], "v2-support-pairs", page_size=12, search=True),
                  "固定0+1与持续刷盘；展示配置中登记的组合，包含零延寿组合。天数均从主C首发起算；未观测到恢复以空值保留。")
    body += '<details class="collapsible-card"><summary>投资 × 养成 × 支援：首次失效寿命完整分组</summary><div>'
    body += _table(lifetime_summary(rows), [("investment_plan", "投资档位"), ("gear_policy", "养成"),
                   ("support_policy", "支援条件"), ("subjects", "参考主体数"), ("events", "首次失效数"),
                   ("median_days", "KM中位寿命（天）")], "v2-lifetime", search=True) + '</div></details>'
    body += _card("为什么后期支援的首次失效寿命可能更短？", '<p>比较的是“支援在自己的首发期才入队”的完整策略，包含到场时机。若老主C在支援登场前已经失效，首次失效已计入；复活会体现在恢复与总满星期数中。更晚的支援并不等于更早给同一队伍装上支援。</p><p class="mini-note">参考主体的首发期不同、随访长度不同，右删失按实际观察期保留。配置的支援组合、到场时间和机制适配共同决定结果。</p>')
    return body


def period_means(rows):
    """Equal-weight seed means; the 10-period view is the completed run prefix."""
    metrics = ("retention_core", "retention_all", "cash_yuan", "cumulative_cash_yuan",
               "subscription_yuan", "new_main_topup_yuan", "support_topup_yuan", "curr_free_used",
               "hoarded_free_used", "sub_pulls_used", "topup_pulls_used")
    groups = defaultdict(list)
    explicit = {(int(r["horizon"]), r["split"], r["policy_id"], int(r["t_idx"])) for r in rows}
    for row in rows:
        key = (int(row["horizon"]), row["split"], row["policy_id"], int(row["t_idx"]))
        groups[key].append(row)
        if key[0] == 20 and key[3] <= 10:
            prefix = (10, *key[1:])
            if prefix not in explicit:
                groups[prefix].append(row)
    return [{"horizon": h, "split": split, "policy_id": policy, "t_idx": t,
             **{metric: mean(_num(r.get(metric)) for r in items) for metric in metrics}}
            for (h, split, policy, t), items in sorted(groups.items())]


def _behavior(out, data):
    players, nt, nv, _ = _design(data)
    controls = '<div class="filter-bar">'
    for ident, title, values, selected in (
        ("behavior-horizon", "观察窗口", [(10, "10期 / 420天"), (20, "20期 / 840天")], 20),
        ("behavior-split", "种子组", [("validation", f"{nv}组独立复核均值"), ("train", f"{nt}组筛选均值")], "validation"),
        ("behavior-lambda", "机制锁 λ", [(x, str(x)) for x in (0, .5, 1, 1.5, 2)], 1),
        ("behavior-a", "专拐增益 a", [(x, str(x)) for x in (.8, 1, 1.2, 1.4, 1.6)], 1),
    ):
        controls += f'<label>{title}<select id="{ident}">' + ''.join(f'<option value="{value}" {"selected" if value == selected else ""}>{label}</option>' for value, label in values) + '</select></label>'
    controls += '</div>'
    insight = (f'λ 0→2：10期现金 {effect_text(_endpoint(data, "mechanism_strength", horizon=10))}；'
               f'20期现金 {effect_text(_endpoint(data, "mechanism_strength"))}，'
               f'核心留存 {effect_text(_endpoint(data, "mechanism_strength", "retention_core"))}。'
               '专拐轴现金的净效应由新主C池直购、专拐直购与月卡续订共同决定。')
    return (f'<div class="panel-heading"><h2>玩家何时补强，何时停氪？</h2><p class="subtitle">Layer 3 · 每次{players}名初始玩家，行为参数为显式情景假设</p></div>'
            + callout("中期逼氪与长期收入需要分开看", insight)
            + _card("选择同一候选与观察窗口", controls + '<div id="behavior-caption" class="scenario-caption" aria-live="polite"></div><div id="behavior-kpis" class="mini-kpi-grid"></div>',
                    "10期使用同一20期运行的前缀，不额外模拟。折线为种子等权均值；抽数与现金分别记账。")
            + '<div class="chart-grid section-gap"><figure class="chart" id="behavior-retention"></figure><figure class="chart" id="behavior-cash"></figure></div>'
            + '<div class="chart-grid"><figure class="chart" id="behavior-money"></figure><figure class="chart" id="behavior-pulls"></figure></div>'
            + _card("心理与付费回路", '<p>历史疲劳、连续卡关与上期“付费仍失败”会进入当期续订／直购意愿；预算容量不变，玩家可以停止付费并继续免费活跃。首次下池、逐抽保底、部分获取、抽后评价与期末流失按固定时序执行。</p><p>核心留存分母固定为初始核心付费层（Budget_40，月卡及中氪主力群）人数，按初始付费能力分组；停氪但仍活跃者仍计入留存。月卡续订是当期现金，历史已购权益的消耗只记抽数，不重复计收入。</p>')
            + _external(out, data))


def _external(out, data):
    from zzz_research.publication_report import benchmark_report_rows, external_ratio_rows, pity_tail_note
    if not data.get("evidence", {}).get("sim_benchmarks"):
        return ""
    body = '<h3 class="section-gap">外部校验 · 真实市场与全服调频统计 vs 模拟器</h3>'
    body += callout("模拟器哪里像现实，哪里不像",
                    "保底曲线、小保底与综合出率与 rng.moe 全服统计一致；但模拟现金约七成来自月卡续订，"
                    "真实头部主C池的月卡／常驻地板占比不超过约四分之一。模拟只有体系绑定型专拐，没有耀嘉音、浮波柚叶这类高需求泛用辅助，"
                    "且音擎抽取偏多。")
    body += '<div class="chart-grid">' + _figure(out, "ext_pity_curve.svg", "rng.moe 2024-07 至 2026-01 全部限定池合并。" + pity_tail_note(data)) + '</div>'
    body += _card("模拟器对照校验", _tuples(benchmark_report_rows(data), ["校验指标", "真实（外部数据）", "模拟器", "判定", "口径"],
                  "external-benchmarks", page_size=14, search=True),
                  "比率类以模拟中位数是否落在真实四分位区间内判定，单值指标以相对差15%为界。")
    body += _card("主C池 vs 专拐池 · 日本 game-i 与中国 iPhone 七麦", _tuples(external_ratio_rows(data),
                  ["类别", "专拐池", "参照主C池", "日本流水（专拐／主C）", "日本总额比", "日本日均比", "中国iPhone日均比", "扣除地板后增量比",
                   "中国iPhone畅销最高名次"], "external-revenue-pairs", page_size=8),
                  "第三方按畅销榜反推的移动端估算，不含PC与官方直充；仅作品类相对量级参照。")
    return body


def _search(out, data):
    tables = data["tables"]["n1000"]
    rho = tables.get("rank_stability", [])
    ends = [r for r in tables.get("axis_effects", []) if r.get("contrast_kind") == "endpoint"
            and r.get("metric") in {"cash_yuan", "retention_core"}]
    decomp = [r for r in tables.get("axis_cash_decomposition", []) if r.get("axis") == "support_gain_scale"
              and r.get("contrast_kind") == "endpoint"]
    focus = tables.get("focus_candidates_comparison", [])
    players, nt, nv, _ = _design(data)
    insight = (f'20期复核：λ 0→2，核心留存 {effect_text(_endpoint(data, "mechanism_strength", "retention_core"))}，'
               f'现金 {effect_text(_endpoint(data, "mechanism_strength"))}；'
               f'a 0.8→1.6，核心留存 {effect_text(_endpoint(data, "support_gain_scale", "retention_core"))}，'
               f'现金 {effect_text(_endpoint(data, "support_gain_scale"))}。留存和现金分别判断。')
    body = ('<div class="panel-heading"><h2>策划调参决策台</h2><p class="subtitle">25个预先指定候选 · 收入与留存分别评价</p></div>'
            + callout("先看稳健方向，再看具体候选", insight, green=True))
    body += '<div class="content-grid">'
    body += _card("旋钮①：机制锁强度 λ（0～2）", '<p>放大“不对口队伍”受到的额外机制惩罚，分为防御、输出窗口和体系壁垒三通道。贯穿只绕过防御，不自动绕过控制互动和职业／体系要求。</p><div class="dial"><b>0：关闭额外锁</b><span>0.5</span><b>1：基准</b><span>1.5</span><b>2：高压</b></div><p class="mini-note">不同通道与机制适配共同决定保留率。λ是模型旋钮，不等于公开成绩比，也不是单一“防+80%／减伤25%”的捆绑尺度。</p>')
    body += _card("旋钮②：专拐净增益 a（0.8～1.6）", '<p>只放大支援相对可用旧辅的附加数值收益：<strong>1＋a×净增益</strong>。强绑定组合净增益0.23，弱绑定0.05；预设机制适配提升另算，不随a倍增。</p><div class="dial"><b>0.8：八折增益</b><b>1：标准</b><b>1.6：增益放大60%</b></div><p class="mini-note">例如强绑定组合的数值倍率从1.184变为1.368。a=0.8不是总输出打八折；每位主C也不会共享同一收益率。</p>') + '</div>'
    body += _card("5×5候选策略全景 · 专拐增益向右，机制锁向下", _frontier_legend(data) + f'<div class="segmented" role="group" aria-label="矩阵观察窗口"><button data-matrix-horizon="10" aria-pressed="false">10期 / 420天</button><button data-matrix-horizon="20" aria-pressed="true">20期 / 840天</button></div><div id="strategy-matrix" aria-live="polite"></div><p class="mini-note">大字为{nv}组独立复核均值，小字为{nt}组筛选均值。绿色对应均值非支配，浅红标出λ=2高压档；颜色不代表显著性。</p>')
    body += '<div class="chart-grid">' + _figure(out, "v2_axis_cash_ci.svg") + _figure(out, "v2_axis_retention_ci.svg") + '</div>'
    body += '<div class="chart-grid">' + _figure(out, "v2_frontier_inclusion.svg") + _figure(out, "v2_rank_stability.svg") + '</div>'
    body += '<h3 class="section-gap">三个决策问题</h3><div class="content-grid">'
    body += _card("① 换一批随机数，25个方案会洗牌吗？", f'<p>20期累计现金排序ρ={_rho(data, "cash_yuan")}，核心留存排序ρ={_rho(data, "retention_core")}；10期现金ρ={_rho(data, "cash_yuan", 10)}。ρ比较筛选均值与独立复核均值的名次，越接近1越一致。</p>'
                  + _table(rho, [("horizon", "观察期"), ("metric_label", "指标"), ("rho", "Spearman ρ"), ("train_seeds", "筛选种子"), ("validation_seeds", "复核种子")], "v2-rank-stability", page_size=12))
    components = []
    for metric in ("new_main_topup_yuan", "support_topup_yuan", "subscription_yuan", "cash_yuan"):
        row = next((r for r in decomp if r.get("horizon") == 20 and r.get("split") == "validation" and r.get("metric") == metric), {})
        components.append(f'{LABELS[metric]} {_signed(_num(row.get("mean_diff_yuan"), None) / 10000 if row.get("mean_diff_yuan") is not None else None)}万元')
    body += _card("② 专拐增益提高后，现金流向哪些商品？", '<p>20期复核端点的点估计：' + '；'.join(components) + '。</p><p>各项的绝对金额区间见现金配对拆解表。共同分母贡献可相加；总现金区间需保留分项协方差，不能把三个区间端点相加。</p>') + '</div>'
    body += _figure(out, "v2_support_cash_decomposition.svg")
    current_focus = [r for r in focus if r.get("players") == players and r.get("horizon") == 20]
    focus_note = '；'.join(f'a={r["support_gain_scale"]:g} 入选{r["included_seeds"]}/{r["n_seeds"]}次' for r in current_focus)
    body += _card("③ λ=1、a=1.4或1.6，可以直接定为赢家吗？", '<p>这两项是扩样前指定的关注候选。当前20期复核：' + (focus_note or '暂无完整复核') + '。相对λ=1、a=1的现金差区间与入选次数一起判断，不能把单次前沿或一个点估计当成全局最优。</p>'
                  + _table(focus, [("players", "每次人数"), ("policy_id", "候选"), ("retention_core", "核心留存"),
                     ("mean_cash_per_initial_player_yuan", "人均现金"), ("paired_cash_per_player_diff_yuan", "相对基准人均现金差"),
                     ("paired_cash_per_player_ci95_low_yuan", "95%下界"), ("paired_cash_per_player_ci95_high_yuan", "95%上界"),
                     ("included_seeds", "入选种子数"), ("n_seeds", "复核种子数")], "v2-focus-candidates", search=True))
    body += '<details class="collapsible-card"><summary>端点效应与逐项95%区间 · 全部观察窗口</summary><div>'
    effect_rows = []
    for r in ends:
        row = dict(r)
        row["axis_label"] = "机制锁 λ 0→2" if r["axis"] == "mechanism_strength" else "支援增益 a 0.8→1.6"
        row["effect"] = effect_text(r)
        effect_rows.append(row)
    body += _table(effect_rows, [("horizon", "观察期"), ("split", "种子组"), ("axis_label", "调参对照"), ("metric_label", "指标"), ("n_pairs", "配对种子"), ("effect", "配对均值变化 [95%区间]")], "v2-axis-effects", search=True) + '</div></details>'
    body += '<details class="collapsible-card"><summary>现金结构配对拆解 · 共分母贡献与金额区间</summary><div>'
    body += _table(decomp, [("horizon", "观察期"), ("split", "种子组"), ("metric_label", "现金项"), ("mean_diff_yuan", "现金差"), ("ci95_low_yuan", "95%下界"), ("ci95_high_yuan", "95%上界"), ("mean_contribution_pp", "基期总现金贡献pp"), ("contribution_ci95_low_pp", "贡献95%下界"), ("contribution_ci95_high_pp", "贡献95%上界")], "v2-cash-decomposition", page_size=16, search=True) + '</div></details>'
    body += '<details class="collapsible-card"><summary>25候选完整成绩单 · 10/20期筛选与复核（100行，支持筛选与CSV导出）</summary><div>'
    body += _table(tables.get("grid_summary", []), [("horizon", "观察期"), ("split", "种子组"), ("mechanism_strength", "机制 λ"), ("support_gain_scale", "支援 a"), ("cash_yuan", "累计现金"), ("retention_core", "核心留存"), ("retention_all", "全体留存"), ("mean_nondominated", "均值前沿"), ("inclusion_frequency", "逐种子入选率")], "v2-candidate-detail", page_size=25, search=True) + '</div></details>'
    body += _ablation(data)
    body += '<aside class="takeaway section-gap"><h3>不确定性如何读</h3><p>3个复核种子的t区间自由度为2。区间按种子配对差计算，玩家人数不是独立种子数；95%区间为逐项区间，没有多重比较校正。当前V2尚未执行心理权重±25%的新扰动批次，旧基线扰动表保留在基线看板中。</p></aside>'
    return body


def _frontier_legend(data):
    """Explain mean-frontier stars versus per-seed inclusion with the current validation rows."""
    tables = data["tables"]["n1000"]
    grid = {r["policy_id"]: r for r in tables.get("grid_summary", [])
            if r.get("horizon") == 20 and r.get("split") == "validation"}
    starred = [r for r in grid.values() if _truth(r.get("mean_nondominated")) and _num(r.get("inclusion_frequency")) < 1]
    examples = "、".join(f'{r["policy_id"]}（{round(_num(r["inclusion_frequency"]) * _num(r["n_seeds"]))}/{r["n_seeds"]:g}）'
                         for r in sorted(starred, key=lambda r: r["policy_id"]))
    runs = defaultdict(dict)
    for r in tables.get("run_summary", []):
        if r.get("horizon") == 20 and r.get("split") == "validation":
            runs[r["seed"]][r["policy_id"]] = (r["mechanism_strength"], _num(r["cash_yuan"]), _num(r["retention_core"]))
    focus, dominated_all = [], True
    for policy in ("L1_A1.4", "L1_A1.6"):
        if not runs or any(policy not in c for c in runs.values()):
            continue
        dominated_all &= all(any(l < c[policy][0] and cash >= c[policy][1] and ret >= c[policy][2]
                                 for p, (l, cash, ret) in c.items() if p != policy) for c in runs.values())
        row = grid.get(policy, {})
        focus.append(f'{policy} 入选 {round(_num(row.get("inclusion_frequency")) * _num(row.get("n_seeds"), 3))}/{len(runs)}')
    focus_text = ('预设关注点 ' + '、'.join(focus)
                  + ('：在每个复核种子内，都有更低 λ 的候选在现金与核心留存上同时不劣于它们。' if dominated_all else '。'))
    text = ('<div class="legend-pair"><p><strong>★ 复核均值非支配</strong>：先对 3 个复核种子求均值，再在 25 个候选中取（累计现金，核心留存）的帕累托前沿。</p>'
            '<p><strong>逐种子入选率</strong>：在每个独立种子内单独取前沿，统计入选次数 / 3。</p></div>'
            '<p class="mini-note">均值落在前沿的候选，单个种子里可能被相邻候选以微小差距超过，所以带 ★ 的候选入选率可以低于 100%'
            + (f'（当前：{examples}）' if examples else '') + '；反之，个别种子入选的候选，均值也可能被支配。'
            + (focus_text if focus else '') + '</p>')
    return text


def _ablation(data):
    from zzz_research.publication_report import ABLATION_LABELS, ablation_verdict_rows, behavior_ablation_rows
    if not data["tables"].get("ablation"):
        return ""
    body = '<h3 class="section-gap">换一种行为函数，结论还成立吗？· 结构消融</h3>'
    body += _card("结论对行为假设的依赖性 · 20期，3组复核种子逐项95%区间", _tuples(
        ablation_verdict_rows(data), ["待检验的商业命题", *(label for _, label in ABLATION_LABELS)], "ablation-verdict", page_size=6),
        "N=200、9个角点／中心点（λ∈{0,1,2}×a∈{0.8,1,1.6}）；M0复用已有运行，M1／M2新增54次。区间跨零记为不可区分。")
    body += _card("三种行为设定的端点效应", _tuples(behavior_ablation_rows(data), ["行为设定", "λ 0→2 核心留存", "λ 0→2 现金（10期→20期）",
                  "a 0.8→1.6 核心留存", "a 0.8→1.6 总现金", "a 0.8→1.6 新主C直购贡献", "专拐直购／月卡续订贡献"], "ablation-effects", page_size=6),
                  "M0：现行停氪缓冲；M1：疲劳与连续失败不扣减付费意愿，只驱动退坑；M2：新主C池另加固定追新效用 β_novelty=1.5。")
    body += callout("跨设定稳健 vs 依赖心理假设",
                    "方向稳健：机制锁加重降低核心留存；专拐增益提高时新主C池直购下降。停氪缓冲主要影响月卡续订现金，不改变留存。"
                    "依赖“缺口驱动抽卡”：机制锁的长期现金负号与专拐的留存收益——玩家受追新驱动时，两者都不再可区分。", green=True)
    return body


EXTRA_CSS = """
#observed-sample-funnel th[scope=row]{position:static;white-space:normal;min-width:150px}
.mini-kpi-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.mini-kpi{padding:13px;border-radius:8px;background:#f3f7fc}.mini-kpi strong{display:block;font-size:21px;color:var(--blue)}.mini-kpi span{font-size:11px;color:var(--muted)}
.dial{display:flex;gap:8px;justify-content:space-between;align-items:center;margin:16px 0;padding:12px;background:#f3f7fc;border-radius:7px;font-size:11px}.matrix-scroll{overflow:auto}.strategy-matrix{min-width:880px;table-layout:fixed}.strategy-matrix th{position:static;font-size:12px}.strategy-matrix td{padding:7px;vertical-align:stretch;background:#fff}.matrix-cell{min-height:155px;border:1px solid #dbe4ef;border-radius:8px;padding:12px;background:#eff5fd}.matrix-cell.frontier{background:#e4f5ee;border-color:#82c9b1}.matrix-cell.high-lock{border-left:3px solid #c66c74;background:#fff0f0}.matrix-cell strong{display:block;font-size:20px;margin-top:13px}.matrix-cell small{display:block;font-size:10px;color:#65758b;margin-top:4px}.matrix-tag{display:block;color:#087c69;font-size:10px;min-height:17px}.matrix-coordinate{font-size:11px;color:#50647f}.matrix-cash{font-size:15px}.matrix-axis{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);padding:8px}.mini-stat{font-size:22px;color:#087c69;font-weight:700}.data-table td{overflow-wrap:anywhere}.chart:empty{display:none}
.guide-strip{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin-top:14px;padding:12px 14px;border:1px solid var(--line);border-radius:9px;background:#fff;font-size:12px;line-height:1.6}.guide-strip strong{margin-right:4px}.guide-strip i{color:var(--muted);font-style:normal}.guide-strip b{color:var(--blue)}
.jump-bar{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin:14px 0;padding:10px 14px;background:#f3f7fc;border-radius:8px;font-size:12px}.jump-bar a{color:var(--blue);text-decoration:none;font-weight:600}.jump-bar a:hover{text-decoration:underline}
.legend-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:0 0 6px}.legend-pair p{margin:0;padding:10px 12px;background:#f3f7fc;border-radius:7px;font-size:12px}
@media(max-width:700px){.mini-kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.dial{flex-wrap:wrap}.legend-pair{grid-template-columns:1fr}.guide-strip i{display:none}}
"""


PUBLICATION_SCRIPT = r"""
const publication = JSON.parse(document.getElementById('publication-data').textContent);
const percent=v=>(v*100).toFixed(1)+'%', money=v=>'¥'+v.toLocaleString('zh-CN',{maximumFractionDigits:0});
const svgEscape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function seriesChart(target,title,rows,series,ylabel,scale=1,fixedMax=null){
 const w=720,h=370,l=68,r=160,t=56,b=48,iw=w-l-r,ih=h-t-b,colors=['#2459d3','#087c69','#d58a20','#9b58a2'];
 const max=Math.max(1,...rows.flatMap(row=>series.map(s=>Number(row[s[0]])/scale)))*1.08,top=fixedMax??max;
 const x=i=>l+i/Math.max(1,rows.length-1)*iw,y=v=>h-b-v/top*ih;
 let s=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${svgEscape(title)}"><rect width="720" height="370" fill="#fff"/><text x="16" y="25" fill="#18283e" font-size="16" font-weight="600">${svgEscape(title)}</text><text x="16" y="44" fill="#65758b" font-size="11">种子等权均值 · ${svgEscape(ylabel)}</text>`;
 for(let i=0;i<=4;i++){let v=top*i/4,py=y(v);s+=`<line x1="${l}" x2="${w-r}" y1="${py}" y2="${py}" stroke="#e2e9f2"/><text x="${l-9}" y="${py+4}" text-anchor="end" font-size="10" fill="#65758b">${v.toFixed(top>1000?0:1)}</text>`;}
 rows.forEach((row,i)=>{if(i%Math.max(1,Math.floor(rows.length/8))===0||i===rows.length-1)s+=`<text x="${x(i)}" y="${h-b+19}" text-anchor="middle" font-size="10" fill="#65758b">${row.t_idx}</text>`;});
 series.forEach(([key,label],j)=>{const c=colors[j%colors.length],d=rows.map((row,i)=>(i?'L':'M')+x(i).toFixed(1)+','+y(Number(row[key])/scale).toFixed(1)).join(' ');s+=`<path d="${d}" fill="none" stroke="${c}" stroke-width="2.5"/><rect x="${w-r+12}" y="${t+j*22}" width="12" height="3" fill="${c}"/><text x="${w-r+30}" y="${t+j*22+5}" font-size="10" fill="#18283e">${svgEscape(label)}</text>`;});
 s+=`<text x="${l+iw/2}" y="${h-9}" text-anchor="middle" font-size="11" fill="#65758b">物理版本期（每期42天）</text></svg>`;document.getElementById(target).innerHTML=s;
}
function renderBehavior(){
 const horizon=Number(document.getElementById('behavior-horizon').value),split=document.getElementById('behavior-split').value,lam=Number(document.getElementById('behavior-lambda').value),a=Number(document.getElementById('behavior-a').value),id='L'+lam+'_A'+a;
 const rows=publication.periods.filter(r=>r.horizon===horizon&&r.split===split&&r.policy_id===id).sort((a,b)=>a.t_idx-b.t_idx);
 document.getElementById('behavior-caption').textContent=`λ=${lam}，a=${a} · ${horizon}期 · ${split==='validation'?publication.design.validation_seeds+'组独立复核':publication.design.train_seeds+'组筛选'} · 每次${publication.design.players}名初始玩家`;
 const summary=publication.grid.find(r=>r.horizon===horizon&&r.split===split&&r.policy_id===id);
 const stats=summary?[['核心层期末留存',percent(summary.retention_core)],['全体期末留存',percent(summary.retention_all)],['累计现金',money(summary.cash_yuan)],['月卡续订现金',money(summary.subscription_yuan)]]:[];
 document.getElementById('behavior-kpis').innerHTML=stats.map(([name,v])=>`<div class="mini-kpi"><span>${name}</span><strong>${v}</strong></div>`).join('');
 seriesChart('behavior-retention','模拟留存：仍活跃，而非仍付费',rows,[['retention_core','核心层'],['retention_all','全队列']],'留存率（%）',.01,100);
 seriesChart('behavior-cash','模拟累计现金：订阅＋直购',rows,[['cumulative_cash_yuan','累计现金']],'人民币（万元）',10000);
 seriesChart('behavior-money','当期现金结构',rows,[['new_main_topup_yuan','新主C池直购'],['support_topup_yuan','专拐直购'],['subscription_yuan','月卡续订']],'人民币（万元）',10000);
 seriesChart('behavior-pulls','当期抽数消耗：四类来源分别记账',rows,[['curr_free_used','本期免费'],['hoarded_free_used','历史囤积免费'],['sub_pulls_used','已购月卡权益'],['topup_pulls_used','当期直购']],'抽');
}
['behavior-horizon','behavior-split','behavior-lambda','behavior-a'].forEach(id=>document.getElementById(id).addEventListener('change',renderBehavior));
function renderMatrix(horizon){
 const rows=publication.grid.filter(r=>r.horizon===horizon),lams=[0,.5,1,1.5,2],gains=[.8,1,1.2,1.4,1.6];
 let s='<div class="matrix-axis"><span>锁越重 ↓</span><span>专拐净增益越大 →</span></div><div class="matrix-scroll"><table class="strategy-matrix"><thead><tr><th>机制 λ / 支援 a</th>'+gains.map(a=>'<th>a='+a+'</th>').join('')+'</tr></thead><tbody>';
 lams.forEach(lam=>{s+='<tr><th>λ='+lam+'</th>';gains.forEach(a=>{const find=split=>rows.find(r=>r.split===split&&Number(r.mechanism_strength)===lam&&Number(r.support_gain_scale)===a),v=find('validation'),t=find('train');if(!v){s+='<td>未运行</td>';return;}const frontier=v.mean_nondominated===true||v.mean_nondominated==='True',freq=Math.round(v.inclusion_frequency*Number(v.n_seeds));s+=`<td><article class="matrix-cell${frontier?' frontier':''}${lam===2?' high-lock':''}" data-candidate="${v.policy_id}"><div class="matrix-coordinate">λ=${lam}, a=${a}</div><span class="matrix-tag">${frontier?'★ 复核均值非支配':'复核逐种子'} · ${freq}/${v.n_seeds}次入选</span><strong>留存 ${percent(v.retention_core)}</strong><div class="matrix-cash">¥${(v.cash_yuan/10000).toFixed(2)}万</div><small>筛选：${t?percent(t.retention_core):'—'} · ¥${t?(t.cash_yuan/10000).toFixed(2):'—'}万</small></article></td>`;});s+='</tr>';});s+='</tbody></table></div>';document.getElementById('strategy-matrix').innerHTML=s;
 document.querySelectorAll('[data-matrix-horizon]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.matrixHorizon)===horizon)));
}
document.querySelectorAll('[data-matrix-horizon]').forEach(b=>b.addEventListener('click',()=>renderMatrix(Number(b.dataset.matrixHorizon))));
document.querySelectorAll('[data-goto-tab]').forEach(a=>a.addEventListener('click',event=>{const tab=document.getElementById('tab-'+a.dataset.gotoTab);if(!tab)return;event.preventDefault();tab.click();window.scrollTo({top:0,behavior:'smooth'});}));
renderBehavior();renderMatrix(20);
"""


def build_publication_dashboard(out_dir, data=None):
    """Build from exported summaries; never query battle or simulation databases."""
    from zzz_research.archetype_presentation import load_archetype_presentation
    from zzz_research.boss_presentation import PROXY_NOTE
    from zzz_research.dashboard_build import _data_section
    from zzz_research.observed_funnel import funnel_html

    out = Path(out_dir)
    if data is None:
        from zzz_research.publication_data import load_publication_data
        data = load_publication_data(out)
    data_intro = ('<p class="mini-note">本页展示真实配队与机制原型；如需直接查看付费模拟与策略取舍，请切换至第 3 页“囤抽、付费与留存”或第 4 页“候选策略与稳健性”。</p>'
                  + funnel_html(out))
    jumps = ('<nav class="jump-bar" aria-label="本页跳转"><span>继续阅读：</span>'
             '<a href="#boss-evidence">↓ 五大首领机制锁世代对照</a>'
             '<a href="#p-combat" data-goto-tab="combat">→ 第2页 真实主C保值与售后恢复</a>'
             '<a href="#p-search" data-goto-tab="search">→ 第4页 候选策略与稳健性</a>'
             '<span class="mini-note">下方逐赛季、逐房间明细表支持分页与搜索。</span></nav>')
    sections = {
        "data": _data_section(out, load_archetype_presentation(out), intro=data_intro,
                              after_overview=jumps).replace(PROXY_NOTE, PROXY_DESCRIPTION),
        "combat": _combat(out, data), "behavior": _behavior(out, data), "search": _search(out, data),
    }
    players, nt, nv, simulation_horizon = _design(data)
    layer1 = data.get("layer1") or {}
    valid_records = layer1.get("valid_records")
    seasons = (layer1.get("evolution_manifest") or {}).get("n_seasons")
    if seasons is None:
        seasons = len({r.get("combat_season") for r in layer1.get("evolution", [])}) or "—"
    support_ret = _endpoint(data, "support_gain_scale", "retention_core")
    support_cash = _endpoint(data, "support_gain_scale")
    planned = data["manifest"].get("planned_runs", len(data["tables"]["n1000"].get("grid_summary", [])))
    kpis = (
        ("真实完整三人通关样本", f'{valid_records / 10000:.1f}万条' if valid_records else "未提供",
         f'{seasons}个战斗赛季 · {layer1.get("unit_count", "—")}名代理人', "Layer 1 · 社区提交的合格样本"),
        ("当前主网格", f'{players:,}人 × {planned}次' if isinstance(players, int) else "当前导出汇总",
         f'25候选 × {nt}筛选＋{nv}复核种子', f'每次{simulation_horizon}期；10期取同一运行前缀'),
        ("20期现金排序稳定性", f'ρ = {_rho(data, "cash_yuan")}', f'核心留存排序 ρ = {_rho(data, "retention_core")}', "筛选均值与独立复核均值比较"),
        ("提高专拐净增益", f'留存 {_signed(support_ret.get("mean_diff"), "pp")}',
         f'专拐净增益倍率 a：0.8→1.6（相对旧辅净提升放大）；20期现金 {_signed(support_cash.get("mean_relative_pct"), "%")} '
         f'[{_signed(support_cash.get("relative_ci95_low"), "%")}, {_signed(support_cash.get("relative_ci95_high"), "%")}]',
         '模拟情景，非官方流水；留存为初始核心付费层期末仍活跃比例'),
    )
    cards = ''.join('<article class="kpi">' + ''.join(f'<div class="kpi-{key}">{escape(value)}</div>' for key, value in zip(("title", "value", "detail", "note"), values)) + '</article>' for values in kpis)
    tabs = ''.join(f'<button type="button" id="tab-{key}" role="tab" data-t="{key}" aria-controls="p-{key}" aria-selected="{str(i == 0).lower()}" tabindex="{0 if i == 0 else -1}">{title}</button>' for i, (key, title) in enumerate(TABS))
    panels = ''.join(f'<section role="tabpanel" id="p-{key}" aria-labelledby="tab-{key}" {"hidden" if i else ""}>{sections[key]}</section>' for i, (key, _) in enumerate(TABS))
    raw = {"grid": data["tables"]["n1000"].get("grid_summary", []),
           "periods": period_means(data["tables"]["n1000"].get("period_outcomes", [])),
           "design": {"players": players, "train_seeds": nt, "validation_seeds": nv}}
    embedded = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    manifest = data["manifest"]
    from zzz_research.publication_report import evidence_level_rows
    valid_text = f'{valid_records / 10000:.1f} 万条' if valid_records else '公开'
    guide = ('<div class="guide-strip" role="note"><strong>三层证据与四页导览</strong>'
             f'<span><b>第1页</b> {valid_text}真实通关记录的流派与机制演替（强实证）</span><i>→</i>'
             '<span><b>第2页</b> 真实主C与受控参考队的保值寿命及售后恢复（经验＋受控对照）</span><i>→</i>'
             '<span><b>第3、4页</b> 显式行为假设下的20期反事实模拟与策略稳健性（情景推演）</span></div>')
    footer = ('<footer class="shell footer"><details open><summary>研究内容与证据强度分级</summary><div>'
              + _tuples(evidence_level_rows(), ["研究模块", "现有数据支撑边界", "证据等级", "使用方式"], "evidence-levels", page_size=4)
              + '<p>模拟留存差与现金差是固定初始队列在显式行为函数下的结果，不是《绝区零》官方流水或真实退坑率。</p></div></details>'
              '<details><summary>研究边界、科学配置与文件入口</summary><div>'
              '<p>Layer 1为公开成功通关记录；Layer 2为真实主C经验轨迹与固定队伍同条件实验；Layer 3为参数化代理模型与显式行为假设下的反事实情景模拟。收入是模拟现金，不是官方流水。公开条件成绩比不构成因果识别。外部卡池流水为第三方估算，仅作品类量级参照。</p>'
              f'<p class="config">当前千人主网格基准配置：{escape(manifest.get("base_config_id", "—"))}</p>'
              '<p>当前目录的publication_manifest.json指定主报告数据。旧版V1参照与心理扰动实验另存基线入口，口径不并入V2结果。</p>'
              '<p><a href="../report/REPORT.md">总报告</a> · <a href="../research/n1000/tables/grid_summary.csv">当前完整成绩单</a> · <a href="baseline.html">V1基线看板</a></p>'
              '</div></details><p>金额：人民币元；变化率：%；留存差：百分点pp。每期42天。所有交互使用本地导出表，不启动模拟或重新导入观测。</p></footer>')
    document = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>机制锁与售后专拐 · 三层研究决策台</title><style>' + CSS + EXTRA_CSS + '</style></head><body>'
                '<header class="shell page-header"><h1>机制锁与售后专拐：老角色、玩家与长期现金</h1><p class="subtitle"><span class="badge">千人跨种子复核</span>真实公开样本 × 固定队伍寿命 × 20期玩家行为情景</p><div class="kpi-grid">'
                + cards + '</div>' + guide + '</header><div class="nav-shell"><nav class="shell" role="tablist" aria-label="研究视图">' + tabs + '</nav></div><main class="shell">' + panels + '</main>' + footer
                + '<script type="application/json" id="publication-data">' + embedded + '</script><script>' + SCRIPT + PUBLICATION_SCRIPT + '</script></body></html>')
    target = out / "dashboard/index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, "utf-8")
    return {"path": str(target), "publication": "research_v2_n1000", "tabs": len(TABS)}
