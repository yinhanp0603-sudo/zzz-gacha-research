"""Compose the local BI dashboard from completed research artifacts."""

from __future__ import annotations

from pathlib import Path

from zzz_research.archetype_presentation import load_archetype_presentation
from zzz_research.boss_presentation import boss_html, enrich_boss_coverage, PROXY_NOTE
from zzz_research.dashboard_charts import behavior_charts, combat_charts
from zzz_research.dashboard_data import appearance_rows, coverage_rows, display, kpi_data, label
from zzz_research.dashboard_decision import DECISION_CSS, render_decision
from zzz_research.dashboard_ui import CSS, SCRIPT, callout, chart, escape, table
from zzz_research.report import (database_availability_note, observations_available, read_csv,
                                read_json, reference_records, search_metadata, search_records,
                                stage_usable)

TABS = (("data", "数据与口径"), ("combat", "战斗对照"),
        ("behavior", "行为情景"), ("search", "候选与稳健性"))


def _table(rows, columns, table_id, **kwargs):
    return table(rows, columns, table_id=table_id, formatter=display, **kwargs)


def _card(title: str, content: str, note: str = "") -> str:
    return (f'<article class="card"><h3>{escape(title)}</h3>'
            + (f'<p class="mini-note">{escape(note)}</p>' if note else "") + content + '</article>')


def _data_section(out: Path, data: dict | None, *, intro: str = "", after_overview: str = "") -> str:
    imported = observations_available(out)
    pieces = ['<div class="panel-heading"><h2>公开样本与真实配队</h2>'
              '<p class="subtitle">Layer 1 · 社区公开通关记录</p></div>']
    if not imported:
        pieces += [callout("核心业务发现", "原始战斗记录未导入：当前仅有公开文件清单，尚不能计算出场率和真实成绩。"),
                   _card("公开文件覆盖", _table(read_csv(out / 'observations/repo_file_coverage.csv'),
                          [('collection_period', '采集期'), ('collect_date', '采集日'), ('status', '覆盖状态')],
                          'file-coverage', search=True))]
        return ''.join(pieces)
    if data:
        top = data['top12'][:2]
        insight = '；'.join(f"{row['label'].split('（')[0]}占 {row['share']:.2%}" for row in top)
        pieces.append(callout("核心业务发现 · 主核更替与插件共现",
                              f"{insight}。{data['core_count']} 名主核／引擎与 {data['flex_count']} 名泛用插件分开标记，多核队伍全部保留。"
                              "后期帷幕、耀变与锐化体系进入样本，辅助与主核呈现高共现搭配；这些是机制情景推演的业务原型。", green=True))
        pieces.append(intro)
        fig = out / 'report/figures/archetype_evolution.svg'
        if fig.exists():
            pieces.append('<div class="evolution">' + chart(fig.read_text('utf-8'),
                          "七个机制分面均为当季合法完整三人记录覆盖率，分母一致；多核可能覆盖多个机制，不能相加。"
                          "式舆与危局合并，赛季等距排列不代表等长时间。") + '</div>')
        pieces.append(after_overview)
        pieces.append('<div class="content-grid">' + _card("高频主核 / 多核流派 · Top 12",
                      _table(data['top12'], [('label', '主核与机制'), ('records', '通关记录'), ('share', '全期占比')], 'top-archetypes'),
                      f"分母：{data['valid_records']:,} 条合法完整三人记录；每支队伍只归入一个完整主核签名。")
                      + _card("命破贯穿 · 五名主核与专项辅助",
                      _table(data['rupture'], [('name', '主核'), ('records', '含该主核'), ('lucia_records', '同队卢西娅'),
                                               ('pan_records', '同队潘引壶')], 'rupture'),
                      "混搭可进入多行；两位辅助均保持泛用插件定位。") + '</div>')
        rules = (f'<p>{data["unit_count"]} 名角色按主输出／体系引擎与泛用插件区分：'
                 f'{data["core_count"]} 名主核、{data["flex_count"]} 名插件。职业沿用游戏数据，主核定位为研究分类。'
                 '耀嘉音、凯撒、妮可、莱特、浮波柚叶等插件不决定流派；所有主核同时保留。</p>')
        rules += ('<p class="mini-note">样本为社区提交的通关记录，存在选择偏差；出场比例不等于全体玩家使用率或持有率。'
                  '技能能力标签统一投射到历史样本，不逐版本复原技能调整。观测不用于推断真实付费和退坑因果。</p>')
        rules += _table(data['pairings'], [('core', '主核'), ('support', '插件'), ('both_records', '同队记录'),
                                          ('support_given_core', '含主核队伍中插件占比'),
                                          ('core_given_support', '含插件队伍中主核占比')], 'pairings')
        pieces.append('<details><summary>分类规则与插件共现明细</summary><div>' + rules + '</div></details>')
    else:
        pieces.append(callout("核心业务发现", "公开战斗记录已导入。覆盖与角色出场明细按合格样本展示，不能外推为全部玩家行为。"))
        pieces.append(intro)
        pieces.append(after_overview)
    pieces.append(boss_html(out))
    pieces += ['<article class="card section-gap"><h3>有效高层关卡与角色赛季查询</h3>'
               '<p class="mini-note">关卡仅展示合格记录大于 0 的行；保留用时制七层、评分制终局层及危局。'
               '赛季条件作用于两张表；角色条件只作用于角色出场表。首屏 KPI 与全期流派统计保持全期范围。</p>'
               '<div class="filter-bar"><label>观测赛季<input id="season-filter" type="search" placeholder="全部赛季，例如 3.2.3 或 2.5"></label>'
               '<label>角色名称<input id="role-filter" type="search" placeholder="中文或英文，例如 仪玄 / Yixuan"></label>'
               '<button id="reset-observed" class="quiet" type="button">重置筛选</button></div>'
               '<div class="content-grid"><div><h3>有效关卡覆盖</h3>'
               + _table(enrich_boss_coverage(out, coverage_rows(out)), [('combat_season', '赛季'), ('mode', '模式'), ('stage_id', '关卡'),
                        ('boss_display', '当期对应 Boss 与核心机制锁'),
                        ('eligible_records', '合格记录'), ('unique_players', '采集期内人数')], 'stage-coverage', season=True)
               + '</div><div><h3>已确认角色出场</h3>'
               + _table(appearance_rows(out), [('combat_season', '赛季'), ('mode', '模式'), ('name_zh', '角色'),
                        ('appearance_player_share', '样本人数占比'), ('appearance_per_clear', '每队出场率'),
                        ('sample_players', '样本人数')], 'role-appearance', season=True, role=True)
               + '</div></div></article>']
    return ''.join(pieces)


def _combat_section(out: Path) -> str:
    charts = combat_charts(out)
    km = read_csv(out / 'benchmarks/km_first_failure.csv') if stage_usable(out, 'build_benchmarks') else []
    pairs = {r['investment_plan']: r for r in km
             if r['gear_policy'] == 'growth_from_debut' and r['support_policy'] == 'none'}
    base = '、'.join(f"{plan} 为 {display('median_age_days', pairs[plan]['median_age_days'])} 天"
                    for plan in ('0+0', '0+1') if plan in pairs)
    insight = (f"持续刷盘口径下，无限定支援的首次失效中位寿命：{base}。"
               "支援能延长寿命，但加入时间和强度共同决定增益，后加入的强支援不保证挽回已经发生的首次失效。"
               if base else "固定参考队伍采用同条件配对，分别观察支援与爆发对齐的边际影响。")
    return ('<div class="panel-heading"><h2>固定队伍战斗对照</h2><p class="subtitle">Layer 2 · 参数化代理模型</p></div>'
            + callout("核心业务发现 · 支援收益取决于时点", insight, green=True)
            + boss_html(out, compact=True)
            + '<div class="chart-grid">' + chart(charts.get('benchmark_pairs', ''), "同条件配对，比较战力指数变化；支援对照包含新增支援资产。")
            + chart(charts.get('km_first_failure', ''), "首次跌破 CI=1 计为失效事件，之后回升也不撤销；虚线为 50% 生存线。") + '</div>'
            + chart(charts.get('reference_applicability', ''))
            + _card("投资、养成与支援 · 首次失效寿命", _table(km,
                    [('investment_plan', '主 C 投资'), ('gear_policy', '养成方式'), ('support_policy', '支援条件'),
                     ('subjects', '参考主体'), ('events', '首次失效数'), ('median_age_days', '中位寿命（天）')],
                    'lifetime', page_size=12, search=True),
                    "CI≥1 是模型满星代理阈值。未跌破 50% 的 KM 曲线保留“观察期内未达中位数”，不填 0。"))


def _behavior_section(out: Path) -> str:
    refs = reference_records(out)
    charts = behavior_charts(out)
    cards = kpi_data(out)
    insight = (cards[2]['note'] + '三种参照中，专拐保值流同时取得最高核心留存和现金；'
               '它是指定行为假设下的参照对比，不代表全部候选的全局最优。') if refs else '参照情景尚未运行，行为数据暂不可用。'
    pieces = ['<div class="panel-heading"><h2>玩家抽卡与留存情景</h2><p class="subtitle">Layer 3 · 模拟结果 / 显式行为假设</p></div>',
              callout("核心业务发现 · 用支援保值维持生态", insight, green=True),
              _card("三种参照情景总览", _table(refs,
                    [('scenario', '操盘情景'), ('n_players', '初始人数'), ('total_revenue_fen', '累计现金（元）'),
                     ('core_retention_end', '核心预算层期末留存'), ('retention_end_all', '全体期末留存')], 'reference-summary'),
                    "固定种子、相同初始规模；核心预算层与全体玩家是两个不同分母。"),
              '<p class="mini-note">' + escape(PROXY_NOTE) + '</p>',
              '<div class="chart-grid">' + chart(charts.get('retention', '')) + chart(charts.get('cash', '')) + '</div>']
    if refs:
        pieces.append('<article class="card section-gap"><h3>情景明细 · 抽数与现金分账</h3>'
                      '<p class="mini-note">按钮同时切换四来源抽数、订阅／直购现金与固定获取队列。三种情景保持一致纵轴尺度。</p>'
                      '<div class="segmented" role="group" aria-label="切换行为情景">')
        for i, ref in enumerate(refs):
            scenario = ref['scenario']
            pieces.append(f'<button type="button" data-scenario="{escape(scenario)}" aria-pressed="{str(i == 0).lower()}">{escape(label(scenario))}</button>')
        pieces.append('</div>')
        for i, ref in enumerate(refs):
            scenario = ref['scenario']
            views = charts['scenarios'].get(scenario, {})
            pieces += [f'<div class="scenario-panel" data-scenario-panel="{escape(scenario)}" {"hidden" if i else ""}>',
                       f'<p class="scenario-caption">{escape(label(scenario))} · 种子 {ref["seed"]} · {ref["n_players"]} 名初始模拟玩家</p>',
                       '<div class="chart-grid">' + chart(views.get('sources', '')) + chart(views.get('money', '')) + '</div>',
                       '<details><summary>固定获取队列 · 活跃、存续与冻结配置反事实</summary><div>',
                       chart(views.get('cohort', ''), "选择该情景样本数最大的固定获取队列；流失记 0 与冻结退坑配置的曲线用于区分样本构成变化。"),
                       '</div></details></div>']
        pieces.append('</article>')
    return ''.join(pieces)


def _search_section(out: Path) -> str:
    return render_decision(out)


def build_dashboard(out_dir: Path | str) -> dict:
    out = Path(out_dir)
    if (out / "publication_manifest.json").is_file():
        from .publication_dashboard import build_publication_dashboard
        return build_publication_dashboard(out)
    validation = read_json(out / 'validate.json') or {}
    manifest = read_json(out / 'pipeline_manifest.json') or {}
    data = load_archetype_presentation(out)
    sections = {'data': _data_section(out, data), 'combat': _combat_section(out),
                'behavior': _behavior_section(out), 'search': _search_section(out)}
    kpis = ''.join('<article class="kpi">'
                   + ''.join(f'<div class="kpi-{key}">{escape(card[key])}</div>'
                             for key in ('title', 'value', 'detail', 'note'))
                   + f'<div class="kpi-source">来源：{escape(card["source"])}</div></article>'
                   for card in kpi_data(out))
    tabs = ''.join(f'<button type="button" id="tab-{key}" role="tab" data-t="{key}" aria-controls="p-{key}" '
                   f'aria-selected="{str(i == 0).lower()}" tabindex="{0 if i == 0 else -1}">{title}</button>'
                   for i, (key, title) in enumerate(TABS))
    panes = ''.join(f'<section role="tabpanel" id="p-{key}" aria-labelledby="tab-{key}" '
                    f'{"hidden" if i else ""}>{sections[key]}</section>' for i, (key, _) in enumerate(TABS))
    stages = ''.join(f'<span>{escape(label(key))} · {escape(label(stage.get("status", "—")))} · '
                     f'{escape(stage.get("wall_seconds", "—"))} 秒</span>'
                     for key, stage in (manifest.get('stages') or {}).items())
    db_note = database_availability_note(out)
    footer = ('<footer class="shell footer"><details><summary>数据来源、研究边界与运行信息</summary><div>'
              '<p>Layer 1 为真实社区公开通关样本；Layer 2／3 为代理模型与显式行为假设下的反事实情景模拟。'
              '候选搜索采用缩减规模，图中差异不构成真实商业预测。金额均为人民币元，抽数与现金分别记账。</p>'
              f'<p class="config">科学配置：{escape(validation.get("config_id", "未运行"))}</p>'
              + (f'<p class="warning">{escape(db_note)}</p>' if db_note else '')
              + f'<div class="stage-grid">{stages}</div></div></details>'
              '<p>数据来源：Hugging Face ShiyuData / ShiyuStats 固定修订；研究分类见角色维表与流派组合映射。'
              '可在各表导出当前筛选，报告与模拟结果保持原始口径。</p></footer>')
    document = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>机制、支援与玩家生态 · 研究看板</title><style>' + CSS + DECISION_CSS + '</style></head><body>'
                '<header class="shell page-header"><h1>机制、支援与玩家生态</h1>'
                '<div class="subtitle"><span class="badge">研究看板</span>真实公开样本 × 固定队伍对照 × 玩家行为情景</div>'
                '<div class="kpi-grid" aria-label="核心指标概览">' + kpis + '</div></header>'
                '<div class="nav-shell"><nav class="shell" role="tablist" aria-label="研究视图">' + tabs + '</nav></div>'
                '<main class="shell">' + panes + '</main>' + footer + '<script>' + SCRIPT + '</script></body></html>')
    target = out / 'dashboard/index.html'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding='utf-8')
    return {'path': str(target)}
