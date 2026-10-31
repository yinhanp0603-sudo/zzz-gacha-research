"""Source-backed planning controls, candidate map and robustness explanations."""

from __future__ import annotations

from pathlib import Path

from zzz_research.dashboard_data import display, label
from zzz_research.dashboard_decision_charts import decision_charts
from zzz_research.dashboard_decision_data import decision_data
from zzz_research.dashboard_ui import callout, chart, escape, table
from zzz_research.report import search_records


def _table(rows, columns, table_id, **kwargs):
    return table(rows, columns, table_id=table_id, formatter=display, **kwargs)


def _pct(value, digits=1):
    return '—' if value is None else f'{value:.{digits}%}'


def _money(value):
    return '—' if value is None else f'¥{value / 1_000_000:.2f}万'


def _card(title, body, note=''):
    return (f'<article class="card"><h3>{escape(title)}</h3>'
            + (f'<p class="mini-note">{escape(note)}</p>' if note else '') + body + '</article>')


def _knobs(data):
    defense = data['knobs']['shield_defense_bonus_base']
    reduction = data['knobs']['shield_damage_reduction_base']
    if defense is None or reduction is None:
        return '<p class="empty">缺少本次实验的冻结参数，暂不展示旋钮档位。</p>'
    left = ('<p>把秽盾、紫光控制技、赴火暴伤锁和侵蚀壁垒理解成对非对策老队伍的额外门槛。'
            '当前计算用两项盾惩罚统一代理：'
            f'<strong>防御加成 +{defense:.0%}×λ</strong>，'
            f'<strong>盾期额外减伤 {reduction:.0%}×λ</strong>。往下拧，同一支队伍越难打满星。</p>'
            '<div class="dial-scale">'
            '<div><b>λ＝0</b><span>关闭额外盾惩罚</span></div>'
            f'<div><b>λ＝1.0</b><span>基准盾<br>防+{defense:.0%} / 减伤{reduction:.0%}</span></div>'
            '<div><b>λ＝1.5</b><span>高压临界档</span></div>'
            f'<div class="danger"><b>λ＝2.0</b><span>高压悬崖档<br>防+{2*defense:.0%} / 减伤{2*reduction:.0%}</span></div></div>'
            '<p class="mini-note">λ＝0 仍有基础防御、血量增长和输出时间约束。贯穿跳过防御区，仍承受模型中的盾期额外减伤。</p>')
    right = ('<p>控制抽到支援后，<strong>把辅助附加的那份增益放大多少</strong>。'
             '计算为 <code>总输出倍率＝1＋a×基础增益</code>，a＝0.8 表示附加增益打八折。</p>'
             '<div class="dial-scale support-scale"><div><b>a＝0.8</b><span>增益偏保守<br>基础增益的80%</span></div>'
             '<div><b>a＝1.0</b><span>标准支援<br>基础增益的100%</span></div>'
             '<div class="generous"><b>a＝1.4～1.6</b><span>强力保值<br>基础增益的140%～160%</span></div></div>'
             '<p class="mini-note">卢西娅、千夏、照、南宫羽、维琳娜等角色的体系搭配是理解这类增益的现实原型；'
             '当前模拟使用抽象支援 S_A／S_B 的数值增益，不逐项还原真实角色技能。</p>')
    return ('<div class="desk-heading"><h3>第 1 步：认识两个调参旋钮</h3></div><div class="content-grid knob-grid">'
            + _card('旋钮 ①：广义机制锁强度 λ（0.0～2.0）', left)
            + _card('旋钮 ②：专拐增益倍数 a（0.8～1.6）', right) + '</div>')


def _matrix(data):
    rows = data['candidates']
    if not rows:
        return '<p class="empty">尚无已完成的候选结果</p>'
    by_xy = {(r['lambda'], r['support_a']): r for r in rows}
    header = ''.join(f'<th scope="col">a＝{value:.1f}</th>' for value in data['a_levels'])
    body = []
    maximum = max(data['lambda_levels'])
    for lam in data['lambda_levels']:
        cells = []
        for gain in data['a_levels']:
            row = by_xy.get((lam, gain))
            if row is None:
                cells.append('<td><div class="matrix-cell unavailable">未运行</div></td>')
                continue
            tags = []
            if row['validation_nondominated']:
                tags.append('<span class="strategy-badge">★ 复核均值非支配</span>')
            if row['validation_high_frequency']:
                tags.append(f'<span class="frequency-badge">复核高频入选 {_pct(row["validation_inclusion_frequency"], 0)}</span>')
            style = ' frontier' if row['validation_nondominated'] else ''
            if lam == maximum and maximum == 2:
                style += ' cliff-cell'
            cells.append(f'<td><article class="matrix-cell{style}" data-candidate="{escape(row["candidate_id"])}">'
                         f'<div class="cell-coordinate">λ＝{lam:.1f}，a＝{gain:.1f}</div>'
                         f'<div class="cell-badges">{"".join(tags)}</div>'
                         f'<div class="cell-retention">留存 {_pct(row["validation_retention"])}</div>'
                         f'<div class="cell-cash">累计流水 {_money(row["validation_revenue_fen"])}</div>'
                         f'<div class="cell-training">筛选：留存 {_pct(row["train_retention"])}<br>'
                         f'流水 {_money(row["train_revenue_fen"])}</div></article></td>')
        body.append(f'<tr><th scope="row">λ＝{lam:.1f}'
                    + ('<span class="matrix-danger-label">高压悬崖区</span>' if lam == 2 else '')
                    + '</th>' + ''.join(cells) + '</tr>')
    nt = max(r['train_n_seeds'] for r in rows)
    nv = max(r['validation_n_seeds'] for r in rows)
    return ('<article class="card matrix-card"><h3>第 2 步：5×5 策略全景热力棋盘</h3>'
            f'<p class="mini-note">大字与主金额：{nv} 组独立复核种子的均值；小字：{nt} 组筛选种子的均值。'
            '留存分母为核心预算层初始人数；流水为每次运行的累计现金（订阅＋直购）。</p>'
            '<div class="matrix-axis-top">专拐越大方 →</div><div class="matrix-frame">'
            '<div class="matrix-axis-side">锁越重 ↓</div><div class="matrix-scroll">'
            '<table class="strategy-matrix" aria-label="机制与支援的候选策略矩阵">'
            f'<thead><tr><th scope="col">机制 λ / 支援 a</th>{header}</tr></thead><tbody>{"".join(body)}</tbody></table>'
            '</div></div><div class="matrix-legend"><span><i class="swatch sweet"></i>复核均值非支配：没有其他候选同时胜过它</span>'
            '<span><i class="swatch normal"></i>过渡区</span><span><i class="swatch pressure"></i>高压悬崖区</span></div>'
            '<p class="mini-note">“高频入选”指至少 2/3 复核种子进入当次模拟非支配集合；'
            '它与“复核均值非支配”分别计算。这里只比较已扫描候选，不代表所有可能参数的全局最优。</p></article>')


def _rho(data):
    rho = data['rho']
    if not rho:
        return '<p class="empty">尚无完整的筛选与复核排名比较</p>'
    parts = []
    for key, name in (('rho_rev', '收入排名相关'), ('rho_ret', '留存排名相关')):
        value = rho.get(key)
        if value is None:
            continue
        width = max(0, min(100, value * 100))
        parts.append(f'<div class="rho-row"><div><span>{name}</span><strong class="rho-badge">ρ＝{value:.3f}</strong></div>'
                     f'<div class="rho-track" role="meter" aria-label="{name}" aria-valuemin="-1" aria-valuemax="1" '
                     f'aria-valuenow="{value}"><i style="width:{width:.2f}%"></i></div></div>')
    return ('<p>相关系数 ρ 越接近 <strong>1.000</strong>，换一批随机数后，25 个方案的前后排名越稳。'
            '收入排名和留存排名分开看，不合成总分。</p>' + ''.join(parts)
            + '<p class="mini-note">对比筛选种子均值与独立复核种子均值；排名稳定不等于收入数值没有随机波动。</p>')


def _impact_cell(key, row, maximum):
    if key != 'impact':
        return None
    value = row['retention_change_mean']
    if value is None:
        return '—'
    width = abs(value) / maximum * 50 if maximum else 0
    direction = 'up' if value > 0 else 'down' if value < 0 else 'flat'
    side = 'right' if value < 0 else 'left'
    return (f'<div class="impact {direction}"><span class="impact-track"><i style="width:{width:.2f}%;{side}:50%"></i></span>'
            f'<strong>{value:+.2%}</strong></div>')


def _pain(data):
    explanations = {
        'failure_weight': ('最敏感：连续卡关挫败感', '连续几期打不满星的累积挫败，对留存影响最大。'),
        'gap_weight': ('次敏感：差一点满星的焦虑', '抽卡后仍存在强度缺口，会持续增加痛感。'),
        'budget_weight': ('当前零效应：直购预算痛感', '本次 ±25% 扰动下，25 个候选的留存和现金均未变化。'),
    }
    cards = []
    for row in data['sensitivity_families']:
        title, meaning = explanations[row['family']]
        low, high = row['mean_min'], row['mean_max']
        value = '—' if low is None or high is None else ('0.00%' if low == high == 0 else f'{low:+.2%} ～ {high:+.2%}')
        cards.append(f'<div class="pain-card {row["family"]}"><h4>{title}</h4><div class="pain-number">{value}</div>'
                     f'<p>{meaning}</p></div>')
    rows = data['sensitivity']['summary']
    maximum = max((abs(r['retention_change_mean']) for r in rows if r['retention_change_mean'] is not None), default=0)
    summary = _table(rows, [('perturbation_id', '痛感参数 / 扰动档位'), ('n_candidates', '候选数'),
                    ('retention_change_mean', '平均留存相对变化'), ('impact', '影响幅度可视化条（←负 0 正→）'),
                    ('retention_change_min', '单候选最小变化'), ('retention_change_max', '单候选最大变化'),
                    ('revenue_change_mean', '平均流水相对变化')], 'sensitivity-summary', page_size=6,
                    cell_renderer=lambda key, row: _impact_cell(key, row, maximum))
    return ('<p>一次只调整一种心理疲劳权重，其余不变。下列范围是 <strong>25 个候选平均留存相对变化</strong>，'
            '分别比较权重降低 25% 与提高 25%；不是百分点，也不是所有候选的极值。</p>'
            '<div class="pain-grid">' + ''.join(cards) + '</div>' + summary)


def _budget(data):
    budget = data['budget']
    total = budget.get('topup_player_periods', 0)
    if not total:
        return '<p class="empty">暂无直购与疲劳交叉分组，无法解释预算痛感。</p>'
    clear = budget['full_star_rate']
    zero = budget['fatigue_after_zero_periods']
    all_zero = zero == total
    return (f'<div class="budget-outcome"><strong>{total:,.0f}</strong><span>个直购玩家期</span>'
            f'<strong>{clear:.0%}</strong><span>抽后满星</span></div>'
            f'<p>在广义机制换锁参照中，{zero:,.0f} 个直购玩家期的期末疲劳为零。'
            + ('当前预算痛感贡献被满星恢复抵消，没有出现“花钱后仍打不过”的组合，因此预算权重扰动呈结构性零效应。'
               if clear == 1 and all_zero else '可对照直购后通关结果与疲劳分组，判断预算压力是否被满星恢复抵消。')
            + f'</p><p class="mini-note">这里的 {total:,.0f} 个样本按玩家期计数，同一人可能出现多次；'
            '参照日志用于解释机制，候选网格的零效应另由六类扰动表验证。结论限定于当前配置与 ±25% 压力测试。</p>')


def _details(data, out):
    candidates = []
    for row in data['candidates']:
        candidates.append({**row, 'train_retention_rate': row['train_retention'],
                           'validation_retention_rate': row['validation_retention'],
                           'train_frontier_label': '★ 非支配' if row['train_nondominated'] else '—',
                           'validation_frontier_label': '★ 非支配' if row['validation_nondominated'] else '—'})
    grades = _table(candidates, [('candidate_id', '候选配置'), ('train_retention_rate', '筛选核心留存'),
                    ('validation_retention_rate', '复核核心留存'), ('train_revenue_fen', '筛选流水（元）'),
                    ('validation_revenue_fen', '复核流水（元）'), ('train_frontier_label', '筛选均值前沿'),
                    ('validation_frontier_label', '复核均值前沿'),
                    ('validation_inclusion_frequency', '复核入选频率')], 'candidate-grades', page_size=25, search=True)
    sensitivity = data['sensitivity']
    detail = [row for key in sensitivity['types'] for row in sensitivity['details'][key]]
    raw = _table(detail, [('perturbation_id', '扰动类型'), ('candidate_id', '候选配置'),
                 ('rel_change_revenue_vs_P0', '流水相对变化'), ('rel_change_retention_vs_P0', '留存相对变化'),
                 ('nondominated_freq', '非支配入选频率')], 'sensitivity-detail', page_size=10,
                 choices=[('', '全部扰动')] + [(key, label(key)) for key in sensitivity['types']],
                 choice_key='perturbation_id', initial=sensitivity['types'][0] if sensitivity['types'] else '')
    ranks = _table(search_records(out, 'rank_stability.csv'), [('perturbation_id', '参数条件'),
                   ('comparison', '比较口径'), ('rho_rev', '收入排名相关'), ('rho_ret', '留存排名相关')], 'rank-stability', page_size=8)
    return (f'<details class="collapsible-card"><summary>{len(candidates)} 候选方案完整成绩单 · 点击展开与导出</summary><div>{grades}</div></details>'
            f'<details class="collapsible-card"><summary>点击展开全部 {len(detail)} 组底层实验数据（含筛选与 CSV 导出）</summary><div>{raw}</div></details>'
            f'<details class="collapsible-card"><summary>排名稳定性完整对照与统计口径</summary><div>{ranks}'
            '<p class="mini-note">矩阵与权衡图采用 P0 独立复核均值；悬崖图采用 P0 筛选种子并对五档支援取平均。'
            '两张图的种子口径分别标注，不将复核均值与筛选均值混合排名。</p>'
            '<p class="mini-note">来源：<a href="../search/seed_robustness.csv">候选均值与入选频率</a> · '
            '<a href="../search/parameter_sensitivity.csv">痛感参数扰动</a> · '
            '<a href="../search/rank_stability.csv">排名稳定性</a>。实际现金与抽数分别记账。</p></div></details>')


def render_decision(out_dir: Path | str) -> str:
    out = Path(out_dir)
    data = decision_data(out)
    intro = '<div class="panel-heading"><h2>三步策划调参决策台</h2><p class="subtitle">候选策略与稳健性 · 机制压力 × 支援保值</p></div>'
    if not data['candidates']:
        return intro + callout('核心业务发现', '候选网格尚未运行，暂无可用于调参比较的结果。')
    cliff = data['cliff']['train']
    text = ('先看 25 个候选在独立复核中的留存与流水，再用筛选种子识别机制压力的陡降区间。'
            '当前复核甜点区为低机制压力与强支援。')
    if cliff:
        text += f' λ 从 {cliff["start"]:g} 到 {cliff["end"]:g}，核心留存平均变化 {cliff["change"]*100:+.1f} 个百分点。'
    charts = decision_charts(out)
    return ('<div class="searchdesk">' + intro + callout('核心业务发现 · 先保住留存，再比较流水', text)
            + _knobs(data) + _matrix(data)
            + '<div class="chart-grid decision-charts">' + chart(charts.get('tradeoff', '')) + chart(charts.get('cliff', '')) + '</div>'
            + '<div class="desk-heading"><h3>第 3 步：用两类压力测试检验结论</h3></div>'
            + _card('压力测试 A：结论是靠抽卡运气蒙出来的吗？', _rho(data))
            + _card('压力测试 B：玩家最受不了哪种痛？', _pain(data)
                    + '<h4>预算痛感为何为零？参照日志中的机制解释</h4>' + _budget(data))
            + _details(data, out) + '</div>')


DECISION_CSS = r"""
.searchdesk .desk-heading{margin:24px 0 12px}.searchdesk>.card{margin:20px 0}.searchdesk .knob-grid{margin-top:0}.searchdesk .knob-grid>.card{height:100%}.searchdesk code{font-family:inherit;background:#f1f5fa;border-radius:4px;padding:1px 4px;font-size:12px}
.dial-scale{display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin:18px 0}.dial-scale>div{background:#f0f4fa;border-top:3px solid #88a6d9;border-radius:5px;padding:10px 7px;min-width:0}.dial-scale b{display:block;font-size:14px;margin-bottom:5px}.dial-scale span{display:block;font-size:11px;line-height:1.6;color:var(--muted)}.dial-scale>.danger{background:#fff0ed;border-color:#c46764}.support-scale{grid-template-columns:1fr 1fr 1.6fr}.support-scale>.generous{background:#e8f6ef;border-color:#4a9d7d}
.matrix-axis-top{font-weight:650;color:var(--green);text-align:center;margin:12px 0 8px}.matrix-frame{position:relative;padding-left:25px}.matrix-axis-side{position:absolute;left:0;top:45%;writing-mode:vertical-rl;color:var(--muted);font-size:12px;font-weight:650;letter-spacing:2px}.matrix-scroll{overflow-x:auto}.strategy-matrix{table-layout:fixed;border-collapse:separate;border-spacing:6px;min-width:950px;font-size:12px}.strategy-matrix th,.strategy-matrix td{border:0;padding:0;background:transparent;vertical-align:middle}.strategy-matrix thead th{text-align:center;position:static;padding:8px 0;color:#5a708c}.strategy-matrix th:first-child{width:100px;white-space:normal}.strategy-matrix tbody th{font-size:14px;color:#58708d}.strategy-matrix tr:hover{background:none}.matrix-cell{padding:13px 11px;border-radius:8px;background:#eff4fb;border:1px solid #dbe6f2;min-height:190px;display:flex;flex-direction:column;transition:box-shadow .15s}.matrix-cell:hover{box-shadow:0 3px 12px #193a5e18}.matrix-cell.frontier{background:#e7f5ed;border-color:#6ba88b;box-shadow:inset 0 3px #5b9f7c}.matrix-cell.cliff-cell{background:#fff0ee;border:1px solid #edc8c4;border-left:3px solid #c86662;border-bottom:3px solid #c86662}.cell-coordinate{font-size:11px;font-weight:650;color:#5a7089}.cell-badges{min-height:41px;display:flex;flex-direction:column;justify-content:center;align-items:start;gap:2px;margin:3px 0}.strategy-badge,.frequency-badge{font-size:9px;color:#116946;line-height:1.5;background:#d8eee1;padding:1px 4px;border-radius:3px}.frequency-badge{color:#52715e;background:#edf7f0}.cell-retention{font-size:21px;font-weight:750;letter-spacing:-.04em;white-space:nowrap}.cell-cash{font-size:12px;font-weight:600;margin:2px 0 8px;color:#425c74}.cell-training{font-size:10px;color:#687c91;border-top:1px solid #cfdde9;padding-top:6px;margin-top:auto}.frontier .cell-retention{color:#167046}.cliff-cell .cell-retention{color:#a64c49}.matrix-danger-label{display:block;font-size:10px;color:#ad5c57;margin-top:4px}.matrix-legend{display:flex;gap:16px;flex-wrap:wrap;font-size:11px;color:var(--muted);margin:14px 0 8px}.matrix-legend span{display:flex;align-items:center;gap:5px}.swatch{display:inline-block;width:12px;height:12px;border-radius:3px;border:1px solid #d5e0ec}.swatch.sweet{background:#cfe8da;border-color:#87b599}.swatch.normal{background:#e5eef9}.swatch.pressure{background:#f5dcd8;border-color:#d3928d}
.rho-row{max-width:850px;margin:18px 0}.rho-row>div:first-child{display:flex;justify-content:space-between;gap:14px;margin-bottom:7px}.rho-badge{color:#087552;background:#e4f4eb;border:1px solid #b8ddc9;border-radius:5px;padding:2px 10px;font-size:16px}.rho-track{height:12px;background:#edf2f7;border-radius:6px;overflow:hidden}.rho-track i{display:block;height:100%;background:#439e7e;border-radius:6px}.pain-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:18px 0}.pain-card{border:1px solid #dde7f1;border-left:3px solid #6b91bd;border-radius:7px;padding:16px;background:#f6f9fd}.pain-card.failure_weight{background:#fff5f0;border-left-color:#bc7a52}.pain-card.budget_weight{background:#edf7f2;border-left-color:#5a9d7d}.pain-card h4{font-size:13px;margin:0 0 10px}.pain-number{font-size:22px;font-weight:700;letter-spacing:-.04em;white-space:nowrap;margin:6px 0 9px}.pain-card p{font-size:12px;color:var(--muted);margin:0}.impact{display:flex;align-items:center;gap:7px;min-width:160px}.impact-track{display:block;width:90px;height:9px;background:#e8eef5;border-radius:5px;overflow:hidden}.impact i{display:block;height:100%;background:#468aab;border-radius:5px}.impact.down i{background:#b87463}.impact strong{font-size:11px;white-space:nowrap}.budget-outcome{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:15px 0}.budget-outcome strong{font-size:29px;color:#167957}.budget-outcome span{color:var(--muted);margin-right:18px}.collapsible-card{margin-top:17px}.searchdesk .collapsible-card[open]>div{padding-top:18px}.searchdesk .decision-charts{margin:22px 0}.searchdesk .mini-note{font-size:11px}
.impact-track{position:relative}.impact-track:before{content:"";position:absolute;left:50%;top:0;height:100%;width:1px;background:#7f90a5;z-index:1}.impact i{position:absolute;top:0}.searchdesk .card>h4{font-size:13px;margin-top:22px;color:#4f657d}
@media(max-width:900px){.pain-grid{grid-template-columns:1fr}.pain-number{font-size:23px}.matrix-card{padding:14px}.matrix-frame{padding-left:20px}.dial-scale{grid-template-columns:repeat(2,1fr)}.support-scale{grid-template-columns:1fr 1fr 1.6fr}}
"""
