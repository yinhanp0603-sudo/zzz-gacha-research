"""Self-contained layout and controls for the local research dashboard."""

from __future__ import annotations

import html


def escape(value) -> str:
    return html.escape(str(value), quote=True)


def callout(title: str, text: str, *, green: bool = False) -> str:
    return (f'<aside class="takeaway {"positive" if green else ""}">'
            f'<h3>{escape(title)}</h3><p>{escape(text)}</p></aside>')


def chart(svg: str, note: str = "") -> str:
    if not svg:
        return '<div class="empty">此阶段尚无可用图表</div>'
    start = svg.find("<svg")
    return ('<figure class="chart">' + (svg[start:] if start >= 0 else svg)
            + (f'<figcaption>{escape(note)}</figcaption>' if note else "") + '</figure>')


def table(rows, columns, *, table_id: str, page_size: int = 12,
          search: bool = False, choices=None, choice_key: str = "", initial: str = "",
          season: bool = False, role: bool = False, formatter=None, cell_renderer=None) -> str:
    """All reviewed rows stay available to filters, pagination and CSV download."""
    if not rows:
        return '<p class="empty">此范围暂无可用记录</p>'
    controls = []
    if search:
        controls.append('<label class="search">关键词<input type="search" data-table-search '
                        'placeholder="搜索当前表格" aria-label="搜索当前表格"></label>')
    if choices:
        options = ''.join(f'<option value="{escape(key)}" {"selected" if key == initial else ""}>'
                          f'{escape(text)}</option>' for key, text in choices)
        controls.append(f'<label>扰动类型<select data-table-choice>{options}</select></label>')
    controls.append('<button class="quiet" data-download type="button">导出当前筛选 CSV</button>')
    body = []
    for i, row in enumerate(rows):
        values = [(formatter(key, row.get(key)) if formatter else str(row.get(key, "—")))
                  for key, _ in columns]
        fields = [str(v) for v in row.values()]
        attrs = f' data-search="{escape(" ".join(fields).lower())}"'
        if choice_key:
            attrs += f' data-kind="{escape(row.get(choice_key, ""))}"'
        if season:
            attrs += f' data-season="{escape(row.get("combat_season", ""))}"'
        if role:
            attrs += f' data-role="{escape(str(row.get("name_zh", "")) + " " + str(row.get("unit_id", "")))}"'
        cells = []
        for (key, _), value in zip(columns, values):
            rendered = cell_renderer(key, row) if cell_renderer else None
            cells.append(f'<td>{escape(value) if rendered is None else rendered}</td>')
        body.append(f'<tr{attrs}{" hidden" if i >= page_size else ""}>' + ''.join(cells) + '</tr>')
    headers = ''.join(f'<th scope="col">{escape(title)}</th>' for _, title in columns)
    return (f'<div class="data-table" id="{escape(table_id)}" data-page-size="{page_size}" '
            f'data-season-filter="{str(season).lower()}" data-role-filter="{str(role).lower()}">'
            f'<div class="table-tools">{"".join(controls)}</div><div class="table-scroll">'
            f'<table><thead><tr>{headers}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
            '<div class="table-pager"><span class="table-count" aria-live="polite"></span>'
            '<div><button class="quiet" data-prev type="button" aria-label="上一页">上一页</button>'
            '<button class="quiet" data-next type="button" aria-label="下一页">下一页</button></div></div></div>')


CSS = r"""
:root{--bg:#f3f6fb;--card:#fff;--ink:#18283e;--muted:#65758b;--line:#e2e9f2;--blue:#2459d3;--green:#087c69;--soft:#edf3ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 'Microsoft YaHei','Noto Sans CJK SC',system-ui,sans-serif}
button,input,select{font:inherit}button{cursor:pointer}a{color:var(--blue)}[hidden]{display:none!important}
.shell{max-width:1280px;margin:auto;padding:0 28px}.page-header{padding-top:30px;padding-bottom:20px}
h1{font-size:26px;letter-spacing:.02em;margin:0 0 5px;font-weight:750}h2{font-size:21px;margin:0 0 4px}h3{font-size:15px;line-height:1.6;margin:0 0 10px}p{margin:0 0 12px}.subtitle,.muted{color:var(--muted)}.subtitle{font-size:13px}.badge{display:inline-block;padding:3px 8px;background:var(--soft);color:var(--blue);border-radius:5px;font-size:11px;margin-right:8px;vertical-align:middle}
.kpi-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:20px 0 24px}.kpi{background:var(--card);border:1px solid var(--line);border-top:3px solid var(--blue);border-radius:12px;padding:18px 18px 16px;box-shadow:0 4px 16px #142c5210;min-width:0}.kpi:nth-child(2){border-top-color:#ad711c}.kpi:nth-child(3){border-top-color:var(--green)}.kpi:nth-child(4){border-top-color:#8b5da6}
.kpi-title{font-size:12px;font-weight:600;color:var(--muted);margin-bottom:9px}.kpi-value{font-size:27px;line-height:1.25;font-weight:760;letter-spacing:-.035em;margin-bottom:11px;font-variant-numeric:tabular-nums}.kpi:nth-child(3) .kpi-value{font-size:23px;color:var(--green)}.kpi-detail{font-size:12px;line-height:1.7}.kpi-note{font-size:11px;line-height:1.6;color:var(--muted);margin-top:7px}.kpi-source{font-size:10px;color:var(--muted);margin-top:10px}
.nav-shell{position:sticky;top:0;background:#fffffff5;backdrop-filter:blur(10px);border-top:1px solid var(--line);border-bottom:1px solid var(--line);z-index:10}nav{display:flex;gap:7px;padding-top:10px!important;padding-bottom:10px!important;overflow-x:auto}nav button{background:none;border:0;color:var(--muted);padding:10px 18px;border-radius:7px;white-space:nowrap;font-weight:600}nav button[aria-selected=true]{background:var(--soft);color:var(--blue)}nav button:focus-visible,button:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
main{padding-top:25px!important;padding-bottom:26px!important}.panel-heading{margin-bottom:20px}.takeaway{background:#ebf2ff;border-left:4px solid var(--blue);border-radius:0 9px 9px 0;padding:17px 21px;margin:0 0 23px}.takeaway.positive{background:#eaf6f2;border-left-color:var(--green)}.takeaway h3{font-size:13px;color:var(--blue);margin-bottom:5px}.takeaway.positive h3{color:var(--green)}.takeaway p{margin:0;line-height:1.85}
.chart-grid,.content-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:20px;align-items:start;margin:18px 0}.card{background:var(--card);padding:21px;border:1px solid var(--line);border-radius:11px;min-width:0}.card h3{margin-bottom:12px}.chart{background:var(--card);border:1px solid var(--line);border-radius:11px;overflow:hidden;margin:0;min-width:0;padding:12px}.chart svg{width:100%;height:auto;display:block}.chart figcaption{font-size:11px;color:var(--muted);line-height:1.7;padding:5px 7px 3px}.evolution{margin:20px 0}.evolution .chart{padding:10px}.section-gap{margin-top:24px}.mini-note{font-size:12px;line-height:1.8;color:var(--muted)}
.data-table{min-width:0}.table-tools{display:flex;align-items:end;justify-content:flex-end;gap:12px;margin-bottom:10px;flex-wrap:wrap}.table-tools label{margin-right:auto}label{display:flex;flex-direction:column;gap:5px;color:var(--muted);font-size:12px}.table-tools .search{flex:1}input,select{border:1px solid #cdd8e7;border-radius:6px;background:#fff;color:var(--ink);padding:8px 10px;min-width:0;max-width:100%}.table-scroll{overflow:auto;border:1px solid var(--line);border-radius:7px;max-height:560px}table{border-collapse:collapse;width:100%;font-size:12px;font-variant-numeric:tabular-nums}th,td{text-align:left;border-bottom:1px solid var(--line);padding:10px 12px;vertical-align:top}th{color:#51647e;background:#f2f6fb;position:sticky;top:0;z-index:1;font-size:11px;white-space:nowrap}td{white-space:normal;min-width:60px}td:first-child{min-width:100px}tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:#f7faff}.table-pager{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:10px;color:var(--muted);font-size:11px}.table-pager>div{display:flex;gap:6px}.quiet{background:#fff;border:1px solid var(--line);color:#526681;padding:6px 10px;border-radius:6px;font-size:11px;white-space:nowrap}.quiet:disabled{opacity:.4;cursor:default}.empty{padding:20px;color:var(--muted);text-align:center;background:#f8fafc;border-radius:7px}.filter-bar{display:flex;flex-wrap:wrap;gap:14px;align-items:end;margin:0 0 16px}.filter-bar label{min-width:200px;flex:1}.filter-bar button{margin-bottom:2px}
.segmented{display:flex;gap:7px;flex-wrap:wrap;margin:18px 0}.segmented button{border:1px solid var(--line);background:#fff;color:var(--muted);padding:9px 14px;border-radius:7px;font-size:12px}.segmented button[aria-pressed=true]{background:var(--blue);border-color:var(--blue);color:#fff}.scenario-caption{padding:9px 0;color:var(--muted);font-size:12px}.insight-stat{font-size:29px;font-weight:700;color:var(--green);margin:10px 0}.compact-list{padding-left:19px;line-height:1.9;margin:8px 0}.warning{color:#9b5d14}.metadata{font-size:12px;color:var(--muted)}details{background:#fff;border:1px solid var(--line);border-radius:9px;margin-top:16px;padding:13px 17px}summary{cursor:pointer;font-weight:600;color:#526681}details>div{padding-top:12px}.footer{border-top:1px solid var(--line);padding-top:12px;padding-bottom:30px}.footer p{font-size:11px;color:var(--muted);margin-top:13px}.config{overflow-wrap:anywhere;font:11px/1.8 monospace}.stage-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:7px}.stage-grid span{padding:6px;background:#f6f8fb}.archetype-table{overflow:auto}.archetype-table td{white-space:normal}
@media(max-width:1140px){.chart-grid,.content-grid{grid-template-columns:1fr}.kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:620px){.shell{padding-left:15px;padding-right:15px}.page-header{padding-top:20px}h1{font-size:21px}.kpi-grid{gap:10px}.kpi{padding:13px 12px}.kpi-value{font-size:23px}.kpi:nth-child(3) .kpi-value{font-size:20px}.kpi-source{display:none}.card{padding:14px}.chart-grid,.content-grid{gap:14px}.takeaway{padding:14px}nav button{padding:8px 11px}.table-pager{align-items:start}.filter-bar label{min-width:100%}}
@media print{.nav-shell,.table-tools,.table-pager,.filter-bar,.segmented,.footer{display:none}section[role=tabpanel][hidden],.scenario-panel[hidden]{display:block!important}.shell{max-width:100%;padding:0}.chart-grid,.content-grid{break-inside:avoid}.kpi{box-shadow:none}}
"""


SCRIPT = r"""
const tabs=[...document.querySelectorAll('nav [role="tab"]')];
function activateTab(button){
 tabs.forEach(b=>{b.setAttribute('aria-selected',String(b===button));b.tabIndex=b===button?0:-1});
 document.querySelectorAll('section[role=tabpanel]').forEach(p=>p.hidden=p.id!=='p-'+button.dataset.t);
 try{localStorage.setItem('zzz-dashboard-tab',button.dataset.t)}catch(e){}
}
tabs.forEach((button,index)=>{
 button.addEventListener('click',()=>activateTab(button));
 button.addEventListener('keydown',event=>{let next=index;if(event.key==='ArrowRight')next=(index+1)%tabs.length;else if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;event.preventDefault();tabs[next].focus();activateTab(tabs[next])});
});
try{const saved=localStorage.getItem('zzz-dashboard-tab');const b=tabs.find(x=>x.dataset.t===saved);if(b)activateTab(b)}catch(e){}
const states=[];
const seasonInput=document.querySelector('#season-filter'),roleInput=document.querySelector('#role-filter');
function selectedRows(state){
 const keyword=(state.element.querySelector('[data-table-search]')?.value||'').trim().toLowerCase();
 const kind=state.element.querySelector('[data-table-choice]')?.value||'';
 const season=state.element.dataset.seasonFilter==='true'?(seasonInput?.value||'').trim().toLowerCase():'';
 const role=state.element.dataset.roleFilter==='true'?(roleInput?.value||'').trim().toLowerCase():'';
 return state.rows.filter(r=>(!keyword||r.dataset.search.includes(keyword))&&(!kind||r.dataset.kind===kind)&&(!season||(r.dataset.season||'').toLowerCase().includes(season))&&(!role||(r.dataset.role||'').toLowerCase().includes(role)));
}
function renderTable(state){
 const rows=selectedRows(state),pages=Math.max(1,Math.ceil(rows.length/state.size));state.page=Math.min(state.page,pages-1);
 state.rows.forEach(r=>r.hidden=true);rows.slice(state.page*state.size,(state.page+1)*state.size).forEach(r=>r.hidden=false);
 const begin=rows.length?state.page*state.size+1:0,end=Math.min(rows.length,(state.page+1)*state.size);
 state.element.querySelector('.table-count').textContent=rows.length?`${begin}–${end} / ${rows.length.toLocaleString('zh-CN')} 行 · 第 ${state.page+1}/${pages} 页`:'当前筛选无匹配记录';
 state.element.querySelector('[data-prev]').disabled=state.page===0;state.element.querySelector('[data-next]').disabled=state.page>=pages-1;
}
document.querySelectorAll('.data-table').forEach(element=>{
 const state={element,rows:[...element.querySelectorAll('tbody tr')],page:0,size:Number(element.dataset.pageSize)};states.push(state);
 element.querySelector('[data-prev]').addEventListener('click',()=>{state.page--;renderTable(state)});
 element.querySelector('[data-next]').addEventListener('click',()=>{state.page++;renderTable(state)});
 element.querySelectorAll('[data-table-search],[data-table-choice]').forEach(control=>control.addEventListener(control.tagName==='SELECT'?'change':'input',()=>{state.page=0;renderTable(state)}));
 element.querySelector('[data-download]').addEventListener('click',()=>{
  const quote=value=>'"'+value.replace(/"/g,'""')+'"';
  const lines=[[...element.querySelectorAll('thead th')].map(c=>quote(c.textContent.trim())).join(','),...selectedRows(state).map(row=>[...row.cells].map(c=>quote(c.textContent.trim())).join(','))];
  const url=URL.createObjectURL(new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=element.id+'.csv';link.click();URL.revokeObjectURL(url);
 });renderTable(state);
});
[seasonInput,roleInput].filter(Boolean).forEach(control=>control.addEventListener('input',()=>states.filter(s=>s.element.dataset.seasonFilter==='true'||s.element.dataset.roleFilter==='true').forEach(s=>{s.page=0;renderTable(s)})));
document.querySelector('#reset-observed')?.addEventListener('click',()=>{seasonInput.value='';roleInput.value='';states.forEach(s=>{s.page=0;renderTable(s)})});
document.querySelectorAll('[data-scenario]').forEach(button=>button.addEventListener('click',()=>{
 document.querySelectorAll('[data-scenario]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
 document.querySelectorAll('.scenario-panel').forEach(panel=>panel.hidden=panel.dataset.scenarioPanel!==button.dataset.scenario);
}));
"""
