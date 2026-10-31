"""Decision-oriented SVG views of completed candidate experiments."""

from __future__ import annotations

import html
import math
from pathlib import Path

from zzz_research.dashboard_decision_data import decision_data


FONT = "'Microsoft YaHei','Noto Sans CJK SC',system-ui,sans-serif"
INK, MUTED, GRID = '#18283e', '#65758b', '#e2e9f2'
BLUE, RED, GREEN = '#2459d3', '#c45656', '#16856d'


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _text(x, y, text, *, size=12, color=MUTED, anchor='start', bold=False):
    return (f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" fill="{color}" '
            f'text-anchor="{anchor}" font-weight="{600 if bold else 400}">{_esc(text)}</text>')


def _frame(title: str, lines: list[str], subtitles: list[str], *, height=500):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 {height}" '
           f'role="img" aria-label="{_esc(title)}" font-family="{FONT}">',
           f'<title>{_esc(title)}</title>']
    for i, line in enumerate(lines):
        out.append(_text(22, 29 + i * 25, line, size=17, color=INK, bold=True))
    base = 29 + 25 * len(lines)
    for i, line in enumerate(subtitles):
        out.append(_text(22, base + i * 19, line, size=11))
    return out


def _ticks(lo, hi, count=4):
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), raw)
    first = math.ceil(lo / step) * step
    return [first + i * step for i in range(count + 2) if first + i * step <= hi + step / 1000]


def _bounds(values, padding=.12, *, low=None, high=None):
    lo, hi = min(values), max(values)
    pad = (hi - lo) * padding or abs(lo) * .05 or .01
    return max(low, lo - pad) if low is not None else lo - pad, min(high, hi + pad) if high is not None else hi + pad


def _tradeoff(data):
    candidates = [row for row in data.get('candidates', [])
                  if row.get('validation_retention') is not None and row.get('validation_revenue_fen') is not None]
    if not candidates:
        return ''
    title = '权衡图：怎么同时保住流水与留存？（越靠右上角越优）'
    ns = sorted({row['validation_n_seeds'] for row in candidates})
    seeds = '/'.join(str(n) for n in ns)
    o = _frame(title, ['权衡图：怎么同时保住流水与留存？', '越靠右上角越优'],
               ['横轴＝核心层留存率；纵轴＝累计流水（万元）。',
                f'模拟 · P0 独立复核 · 每点为 {seeds} 组种子的均值；仅比较本轮候选。',
                '实心大圆点＝未被其他方案全面碾压的“性价比天花板（帕累托前沿）”。'])
    left, right, top, bottom = 78, 680, 187, 397
    x0, x1 = _bounds([row['validation_retention'] for row in candidates], low=0, high=1)
    y0, y1 = _bounds([row['validation_revenue_fen'] / 1e6 for row in candidates])
    X = lambda value: left + (value - x0) / (x1 - x0) * (right - left)
    Y = lambda value: bottom - (value - y0) / (y1 - y0) * (bottom - top)
    for value in _ticks(x0, x1):
        o.append(f'<line x1="{X(value):.2f}" x2="{X(value):.2f}" y1="{top}" y2="{bottom}" stroke="{GRID}"/>')
        o.append(_text(X(value), bottom + 23, f'{value:.0%}', anchor='middle', size=11))
    for value in _ticks(y0, y1):
        o.append(f'<line x1="{left}" x2="{right}" y1="{Y(value):.2f}" y2="{Y(value):.2f}" stroke="{GRID}"/>')
        o.append(_text(left - 10, Y(value) + 4, f'¥{value:.1f}万', anchor='end', size=11))
    o += [f'<line x1="{left}" x2="{right}" y1="{bottom}" y2="{bottom}" stroke="#9aa9ba"/>',
          _text((left + right) / 2, bottom + 47, '核心预算层期末留存率 →', anchor='middle', size=12),
          _text(left, top - 10, '累计流水（万元） ↑', size=11)]
    frontier = sorted((r for r in candidates if r['validation_nondominated']), key=lambda r: r['validation_retention'])
    if frontier:
        path = ' '.join(f'{"M" if i == 0 else "L"}{X(r["validation_retention"]):.2f},{Y(r["validation_revenue_fen"] / 1e6):.2f}'
                        for i, r in enumerate(frontier))
        o.append(f'<path d="{path}" fill="none" stroke="{GREEN}" stroke-width="2.3"/>')
    for row in sorted(candidates, key=lambda r: bool(r['validation_nondominated'])):
        front = row['validation_nondominated']
        x, y = X(row['validation_retention']), Y(row['validation_revenue_fen'] / 1e6)
        summary = (f'机制 λ={row["lambda"]:g} · 专拐 a={row["support_a"]:g} · '
                   f'复核留存 {row["validation_retention"]:.2%} · 流水 ¥{row["validation_revenue_fen"] / 100:,.0f} · '
                   f'复核入选频率 {row["validation_inclusion_frequency"]:.0%}')
        o.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{8 if front else 4.3}" '
                 f'fill="{GREEN if front else "#8ca1bc"}" fill-opacity="{1 if front else .55}" '
                 f'stroke="white" stroke-width="1.3"><title>{_esc(summary)}</title></circle>')
    if frontier:
        max_lam = max(r['lambda'] for r in frontier)
        condition = '轻机制 + 强力专拐' if max_lam <= .5 else '本轮均值非支配候选'
        lambdas = sorted({r['lambda'] for r in frontier})
        best_a = sorted({r['support_a'] for r in frontier})
        detail = ' / '.join(f'λ={v:g}' for v in lambdas) + '；' + ' / '.join(f'a={v:g}' for v in best_a)
        o += [f'<rect x="350" y="133" width="330" height="46" rx="7" fill="#e7f5ef" fill-opacity=".9" stroke="{GREEN}" stroke-dasharray="5 4"/>',
              _text(364, 153, '★ 黄金甜点区（' + condition + '）', size=13, color=GREEN, bold=True),
              _text(364, 170, detail, size=11, color=GREEN)]
    o += [f'<circle cx="100" cy="467" r="7" fill="{GREEN}"/>',
          _text(114, 471, '均值非支配方案', size=11),
          '<circle cx="305" cy="467" r="4.3" fill="#8ca1bc"/>',
          _text(318, 471, '其他候选', size=11),
          _text(22, 493, '坐标聚焦候选范围；均值前沿与逐种子入选频率是两种口径。', size=10), '</svg>']
    return ''.join(o)


def _cliff(data):
    levels = [r for r in data.get('axis_levels', {}).get('train', [])
              if r.get('mean_ret') is not None and r.get('mean_rev_fen') is not None]
    if not levels:
        return ''
    cliff = data.get('cliff', {}).get('train') or {}
    drop = cliff.get('change')
    drop_pp = f'{drop * 100:+.1f}' if drop is not None else '—'
    start, end = cliff.get('start'), cliff.get('end')
    title = '悬崖效应：机制锁拉太狠会发生什么？'
    detail = f'λ 从 {start:g} 到 {end:g}，留存变化 {drop_pp} 个百分点' if start is not None else '机制强度与留存、流水的变化'
    o = _frame(title + '（' + detail + '）', [title, detail],
               ['对专拐各档取平均，观察机制盾越来越厚时，玩家留存与流水的变化。',
                '模拟 · P0 筛选种子均值 · 红线看留存，蓝线看流水；左右轴独立刻度。'])
    left, right, top, bottom = 78, 638, 168, 397
    xs = [r['level'] for r in levels]
    x0, x1 = min(xs), max(xs)
    r0, r1 = _bounds([r['mean_ret'] for r in levels], padding=.25, low=0, high=1)
    c0, c1 = _bounds([r['mean_rev_fen'] / 1e6 for r in levels], padding=.25)
    X = lambda value: left + (value - x0) / (x1 - x0 or 1) * (right - left)
    R = lambda value: bottom - (value - r0) / (r1 - r0) * (bottom - top)
    C = lambda value: bottom - (value - c0) / (c1 - c0) * (bottom - top)
    if start is not None and end is not None:
        o.append(f'<rect x="{X(start):.2f}" y="{top}" width="{X(end)-X(start):.2f}" height="{bottom-top}" fill="#f9dcdc" fill-opacity=".65"/>')
    for value in _ticks(r0, r1):
        o += [f'<line x1="{left}" x2="{right}" y1="{R(value):.2f}" y2="{R(value):.2f}" stroke="{GRID}"/>',
              _text(left - 10, R(value) + 4, f'{value:.0%}', anchor='end', size=11, color=RED)]
    for value in _ticks(c0, c1):
        o.append(_text(right + 10, C(value) + 4, f'¥{value:.1f}万', size=11, color=BLUE))
    for value in xs:
        o.append(_text(X(value), bottom + 22, f'{value:g}', anchor='middle', size=11))
    o += [f'<line x1="{left}" x2="{right}" y1="{bottom}" y2="{bottom}" stroke="#9aa9ba"/>',
          _text(left, top - 12, '核心层留存率', size=11, color=RED),
          _text(right + 50, top - 12, '累计流水', anchor='end', size=11, color=BLUE),
          _text((left + right) / 2, bottom + 46, '广义机制锁强度 λ →', anchor='middle')]
    for key, color, yfun, fmt in (('mean_ret', RED, R, lambda v: f'{v:.2%}'),
                                 ('mean_rev_fen', BLUE, lambda v: C(v / 1e6), lambda v: f'¥{v / 100:,.0f}')):
        path = ' '.join(f'{"M" if i == 0 else "L"}{X(r["level"]):.2f},{yfun(r[key]):.2f}' for i, r in enumerate(levels))
        o.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.8"/>')
        for row in levels:
            o.append(f'<circle cx="{X(row["level"]):.2f}" cy="{yfun(row[key]):.2f}" r="4.7" fill="{color}">'
                     f'<title>λ={row["level"]:g}：{_esc(fmt(row[key]))}</title></circle>')
    if start is not None:
        o += [f'<rect x="342" y="113" width="348" height="29" rx="6" fill="#fff2f2" stroke="#e2a4a4"/>',
              _text(354, 133, f'⚠ 心理崩盘悬崖（{drop_pp}pp）', color=RED, bold=True, size=13)]
        first = next(r for r in levels if r['level'] == start)
        last = next(r for r in levels if r['level'] == end)
        o.append(_text(X(start) - 8, R(first['mean_ret']) - 12, f'{first["mean_ret"]:.1%}', anchor='end', color=RED, size=11, bold=True))
        o.append(_text(X(end) - 8, R(last['mean_ret']) + 21, f'{last["mean_ret"]:.1%}', anchor='end', color=RED, size=11, bold=True))
    o += [f'<line x1="130" x2="155" y1="467" y2="467" stroke="{RED}" stroke-width="3"/>',
          _text(163, 471, '留存率（左轴）', size=11),
          f'<line x1="365" x2="390" y1="467" y2="467" stroke="{BLUE}" stroke-width="3"/>',
          _text(398, 471, '累计流水（右轴，万元）', size=11),
          _text(22, 493, '双纵轴用于观察各自走势，不比较红蓝线的绝对高度。', size=10), '</svg>']
    return ''.join(o)


def decision_charts(out: Path | str) -> dict[str, str]:
    """Return trade-off and mechanism-cliff views without rerunning a model."""
    data = decision_data(out)
    return {'tradeoff': _tradeoff(data), 'cliff': _cliff(data)}
