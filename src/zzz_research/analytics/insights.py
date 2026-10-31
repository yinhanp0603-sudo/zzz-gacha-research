"""报告用“核心业务发现”：只从已导出的汇总表计算，不重新模拟、不改模型参数。

每条结论都由数据推出并带具体数字；条件不满足（例如不存在拐点、中位数未达到）时如实说明，
不以预设叙事替代结果。所有行为层结论默认带“模拟/假设情景”语境，由调用方在章节中声明。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

PERIOD_DAYS_LABEL = "天"


def _f(x: Any) -> Optional[float]:
    if x in (None, "", "None", "NULL", "null"):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _yuan(fen: float) -> str:
    return f"{fen / 100:,.0f} 元"


def _pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def _pp(x: float, digits: int = 1) -> str:
    return f"{x * 100:+.{digits}f} 个百分点"


def _mean(v: Sequence[float]) -> Optional[float]:
    return sum(v) / len(v) if v else None


@dataclass(frozen=True)
class Finding:
    text: str
    basis: str  # 依据的表或口径，便于读者回查


# =========================================================================== Layer 2：寿命


def lifetime_findings(km_rows: Sequence[Mapping[str, Any]], *, gear_policy: str = "growth_from_debut",
                      plans: Sequence[str] = ("0+0", "0+1", "2+1")) -> list[Finding]:
    """各投资方案下：无支援的首次失效中位寿命，以及加入各支援后的寿命与倍数。"""
    out: list[Finding] = []
    by = {(r.get("investment_plan"), r.get("support_policy")): r for r in km_rows
          if r.get("gear_policy") == gear_policy}
    supports = sorted({sp for (_, sp) in by if sp and sp != "none"})
    for plan in plans:
        base = by.get((plan, "none"))
        if not base:
            continue
        m0 = base.get("median_age_days")
        m0f = _f(m0)
        parts = []
        for sp in supports:
            r = by.get((plan, sp))
            if not r:
                continue
            m1 = r.get("median_age_days")
            m1f = _f(m1)
            name = sp.split(":")[-1]
            if m1f is None:
                parts.append(f"{name} 后中位寿命超过观察窗口（S(t) 始终 >0.5）")
            elif m0f is None:
                parts.append(f"{name} 后 {m1f:.0f} 天")
            elif m0f == 0:
                parts.append(f"{name} 后 {m1f:.0f} 天（无支援出道即失效，倍数无定义）")
            else:
                parts.append(f"{name} 后 {m1f:.0f} 天（×{m1f / m0f:.1f}）")
        if m0f is None:
            head = f"{plan}：无支援时中位寿命超过观察窗口"
        else:
            head = f"{plan}：无支援首次失效中位寿命 {m0f:.0f} 天（约 {m0f / 42:.0f} 个区间）"
        out.append(Finding(head + ("；" + "，".join(parts) if parts else "") + "。",
                           f"km_first_failure.csv（{gear_policy}）"))
    if out and supports:
        out.append(Finding("支援策略为“该支援出道即加入”：较晚出道的支援只能覆盖其出道后的主输出，"
                           "因此支援之间的寿命倍数同时反映增益大小与出道时点，不能直接当作增益强弱排序。",
                           "dim_reference_subject.support_policy"))
    return out


def applicability_findings(traj_rows: Sequence[Mapping[str, Any]], *, plan: str = "0+1",
                           gear_policy: str = "growth_from_debut") -> list[Finding]:
    """无支援参考轨迹在出道后各年龄的达标占比。"""
    acc: dict[float, list[int]] = {}
    for r in traj_rows:
        if r.get("plan") != plan or r.get("gear_policy") != gear_policy or r.get("support_policy") != "none":
            continue
        age, ci = _f(r.get("age_days")), _f(r.get("ci"))
        if age is None or ci is None:
            continue
        acc.setdefault(age, []).append(int(ci >= 1.0))
    if not acc:
        return []
    cells = [f"{age:.0f} 天 {sum(v)}/{len(v)}" for age, v in sorted(acc.items())[:4]]
    return [Finding(f"{plan} 无支援参考主体达标数随出道年龄变化：" + "；".join(cells) + "。",
                    "reference_trajectories.csv")]


def pairing_findings(pair_rows: Sequence[Mapping[str, Any]]) -> list[Finding]:
    out = []
    groups: dict[str, list[float]] = {}
    rels: dict[str, list[float]] = {}
    for r in pair_rows:
        key = f"{r.get('changed_factor')}:{str(r.get('alt_label', '')).split('|')[-1]}"
        d, rel = _f(r.get("abs_diff")), _f(r.get("rel_diff"))
        if d is not None:
            groups.setdefault(key, []).append(d)
        if rel is not None:
            rels.setdefault(key, []).append(rel)
    sup = {k: v for k, v in groups.items() if k.startswith("support:")}
    if sup:
        txt = "，".join(f"{k.split(':')[1]} 平均 ΔCI {_mean(v):+.3f}（相对 {_pct(_mean(rels.get(k, [0])))}）"
                       for k, v in sorted(sup.items()))
        out.append(Finding(f"只加入支援、其余不变时：{txt}；这是增加支援资产的对照，不是等成本比较。",
                           "benchmark_pairs.csv"))
    kap = {k: v for k, v in groups.items() if k.startswith("kappa:")}
    if kap:
        txt = "，".join(f"{k.split(':')[1]} 平均 ΔCI {_mean(v):+.3f}" for k, v in sorted(kap.items()))
        out.append(Finding(f"盾内/盾外输出对齐只改变时序分配：{txt}（相对 κ=1）。", "benchmark_pairs.csv"))
    return out


# =========================================================================== Layer 3：参照情景


def reference_findings(refs: Sequence[Mapping[str, Any]], retention_tables: Mapping[str, Sequence[Mapping]],
                       base_scenario: str) -> list[Finding]:
    out = []
    by = {r["scenario"]: r for r in refs}
    base = by.get(base_scenario)
    if base is None:
        return out
    b_ret, b_rev = _f(base.get("retention_end_all")), _f(base.get("total_revenue_fen"))
    out.append(Finding(f"基准情景 {base_scenario}：期末全体留存 {_pct(b_ret)}、核心预算层 "
                       f"{_pct(_f(base.get('core_retention_end')) or 0)}，观察期累计现金 {_yuan(b_rev)}"
                       f"（{base.get('n_players')} 人）。", "reference_runs.json"))
    for name, r in sorted(by.items()):
        if name == base_scenario:
            continue
        ret, rev = _f(r.get("retention_end_all")), _f(r.get("total_revenue_fen"))
        if None in (ret, rev, b_ret, b_rev):
            continue
        out.append(Finding(f"{name} 相对基准：期末留存 {_pp(ret - b_ret)}，累计现金 {_yuan(rev - b_rev)}"
                           f"（{(rev - b_rev) / b_rev * 100:+.1f}%）；同一种子、不同科学配置的情景对比。",
                           "reference_runs.json"))
    tab = retention_tables.get(base_scenario) or []
    ends = [(int(r["t_idx"]), _f(r.get("retention_end")), _f(r.get("retention_start"))) for r in tab]
    if ends:
        churn = [(t, (s or 0) - (e or 0)) for t, e, s in ends]
        total = sum(c for _, c in churn)
        if total > 0:
            mid = sum(c for t, c in churn if 4 <= t <= 8)
            peak_t, peak_c = max(churn, key=lambda x: (x[1], -x[0]))
            out.append(Finding(f"基准情景流失时序：P04–P08 占全部流失的 {_pct(mid / total, 0)}，单期峰值在 P{peak_t:02d}"
                               f"（{_pct(peak_c)} 初始人群）；中期流失会截断其后的订阅与直购现金。",
                               "retention_cash.csv"))
    return out


def revenue_mix_findings(rev_rows: Sequence[Mapping[str, Any]]) -> list[Finding]:
    sub = sum(_f(r.get("sub_revenue_fen")) or 0 for r in rev_rows)
    top = sum(_f(r.get("topup_revenue_fen")) or 0 for r in rev_rows)
    pulls = sum(_f(r.get("total_pulls")) or 0 for r in rev_rows)
    if not pulls or (sub + top) == 0:
        return []
    shares = {k: sum(_f(r.get(k)) or 0 for r in rev_rows) / pulls
              for k in ("curr_free_used", "hoarded_free_used", "sub_pulls_used", "topup_pulls_used")}
    return [Finding(f"基准情景现金中订阅占 {_pct(sub / (sub + top), 0)}、直购占 {_pct(top / (sub + top), 0)}；"
                    f"抽数来源：本期免费 {_pct(shares['curr_free_used'], 0)}、历史免费 {_pct(shares['hoarded_free_used'], 0)}、"
                    f"订阅 {_pct(shares['sub_pulls_used'], 0)}、直购 {_pct(shares['topup_pulls_used'], 0)}。",
                    "revenue_structure.csv")]


def cohort_findings(rows: Sequence[Mapping[str, Any]]) -> list[Finding]:
    sizes: dict[tuple[str, str], int] = {}
    for r in rows:
        sizes[(r["unit_id"], r["acquisition_period"])] = int(r["n"])
    if not sizes:
        return []
    u, g = max(sizes, key=lambda k: (sizes[k], k))
    seq = sorted((r for r in rows if r["unit_id"] == u and r["acquisition_period"] == g), key=lambda r: int(r["t_idx"]))
    first, last = seq[0], seq[-1]
    a0, a1 = _f(first.get("avail_rate_active")), _f(last.get("avail_rate_active"))
    c1, f1 = _f(last.get("cohort_active_clear_share")), _f(last.get("avail_rate_counterfactual_frozen"))
    ch = _f(last.get("churned_holder_share"))
    if None in (a0, a1):
        return []
    txt = (f"最大获取队列 {u}（P{int(g):02d} 获取，N={sizes[(u, g)]}）：活跃持有者达标率由 {_pct(a0, 0)} 降至 "
           f"P{int(last['t_idx']):02d} 的 {_pct(a1, 0)}")
    if ch is not None:
        txt += f"，退出持有者占 {_pct(ch, 0)}"
    if c1 is not None and f1 is not None:
        txt += f"；按冻结配置反事实计入退出者后为 {_pct(f1, 0)}（全队列活跃达标占比 {_pct(c1, 0)}）"
    return [Finding(txt + "。", "cohort_availability.csv")]


# =========================================================================== 候选搜索：λ / a 与 CRN 配对差


def _level(cid: str, axis: int) -> Optional[float]:
    """候选 ID 约定为 L<λ>_A<a>；不符合约定的 ID 不参与轴汇总（返回 None）。"""
    parts = str(cid).split("_")
    if len(parts) != 2 or axis >= 2:
        return None
    try:
        return float(parts[axis][1:])
    except ValueError:
        return None


def axis_paired_effects(search_rows: Sequence[Mapping[str, Any]], *, axis: int, split: str = "train",
                        perturbation: str = "P0") -> list[dict]:
    """按 λ（axis=0）或 a（axis=1）水平汇总同 seed 相对基准点的配对差（对另一轴与种子取平均）。"""
    acc: dict[float, dict[str, list[float]]] = {}
    for r in search_rows:
        if r.get("split") != split or r.get("perturbation_id") != perturbation:
            continue
        lv = _level(r["candidate_id"], axis)
        if lv is None:
            continue
        d = acc.setdefault(lv, {"rev": [], "ret": [], "rev_abs": [], "ret_abs": []})
        for src, dst in (("delta_revenue_vs_base", "rev"), ("delta_retention_vs_base", "ret"),
                         ("total_revenue_fen", "rev_abs"), ("core_retention_end", "ret_abs")):
            v = _f(r.get(src))
            if v is not None:
                d[dst].append(v)
    out = []
    for lv, d in sorted(acc.items()):
        out.append({"level": lv, "n": len(d["rev"]), "mean_delta_rev_fen": _mean(d["rev"]),
                    "sd_delta_rev_fen": statistics.stdev(d["rev"]) if len(d["rev"]) > 1 else None,
                    "share_delta_rev_negative": (sum(x < 0 for x in d["rev"]) / len(d["rev"])) if d["rev"] else None,
                    "mean_delta_ret": _mean(d["ret"]), "mean_rev_fen": _mean(d["rev_abs"]),
                    "mean_ret": _mean(d["ret_abs"])})
    return out


def sign_change(levels: Sequence[dict], key: str) -> Optional[tuple[float, float]]:
    """均值由正转负的相邻水平 (最后一个为正, 第一个为负)；不存在返回 None。"""
    vals = [(r["level"], r[key]) for r in levels if r[key] is not None]
    for (l0, v0), (l1, v1) in zip(vals, vals[1:]):
        if v0 > 0 and v1 < 0:
            return l0, l1
    return None


def profile_shape(levels: Sequence[dict], key: str) -> tuple[str, Optional[float]]:
    """均值随水平的形状：'increasing' / 'decreasing' / 'interior_max'（返回峰值水平）/ 'interior_min' / 'flat' / 'mixed'。"""
    vals = [(r["level"], r[key]) for r in levels if r[key] is not None]
    if len(vals) < 2:
        return "flat", None
    diffs = [v1 - v0 for (_, v0), (_, v1) in zip(vals, vals[1:])]
    if all(d > 0 for d in diffs):
        return "increasing", None
    if all(d < 0 for d in diffs):
        return "decreasing", None
    if all(d == 0 for d in diffs):
        return "flat", None
    i = max(range(len(vals)), key=lambda k: vals[k][1])
    if 0 < i < len(vals) - 1 and all(d >= 0 for d in diffs[:i]) and all(d <= 0 for d in diffs[i:]):
        return "interior_max", vals[i][0]
    j = min(range(len(vals)), key=lambda k: vals[k][1])
    if 0 < j < len(vals) - 1 and all(d <= 0 for d in diffs[:j]) and all(d >= 0 for d in diffs[j:]):
        return "interior_min", vals[j][0]
    return "mixed", None


def largest_step(levels: Sequence[dict], key: str) -> Optional[tuple[float, float, float, float]]:
    """相邻水平间绝对变化最大的一步：(起点, 终点, 变化, 其余步变化绝对值的均值)。"""
    vals = [(r["level"], r[key]) for r in levels if r[key] is not None]
    steps = [(l0, l1, v1 - v0) for (l0, v0), (l1, v1) in zip(vals, vals[1:])]
    if len(steps) < 2:
        return None
    k = max(range(len(steps)), key=lambda i: abs(steps[i][2]))
    rest = [abs(x[2]) for i, x in enumerate(steps) if i != k]
    return steps[k][0], steps[k][1], steps[k][2], sum(rest) / len(rest)


_SHAPE_CN = {"increasing": "随水平单调上升", "decreasing": "随水平单调下降", "flat": "不随水平变化",
             "mixed": "随水平不单调"}


def _shape_text(shape: tuple[str, Optional[float]], what: str) -> str:
    kind, at = shape
    if kind == "interior_max":
        return f"{what}在网格内部 {at:g} 处取最大值（先升后降）"
    if kind == "interior_min":
        return f"{what}在网格内部 {at:g} 处取最小值（先降后升）"
    return f"{what}{_SHAPE_CN[kind]}"


def search_findings(search_rows: Sequence[Mapping[str, Any]], robust_rows: Sequence[Mapping[str, Any]],
                    rho_rows: Sequence[Mapping[str, Any]], sens_rows: Sequence[Mapping[str, Any]],
                    base_lambda: float, base_a: float) -> list[Finding]:
    out: list[Finding] = []
    lam = axis_paired_effects(search_rows, axis=0)
    lam_v = axis_paired_effects(search_rows, axis=0, split="validation")
    if lam:
        lo, hi = lam[0], lam[-1]
        out.append(Finding(
            f"机制强度 λ 从 {lo['level']:g} 到 {hi['level']:g}（筛选种子，对 a 取平均）：核心预算层期末留存均值 "
            f"{_pct(lo['mean_ret'])} → {_pct(hi['mean_ret'])}，每 run 累计现金均值 {_yuan(lo['mean_rev_fen'])} → "
            f"{_yuan(hi['mean_rev_fen'])}；{_shape_text(profile_shape(lam, 'mean_rev_fen'), '收入')}，"
            f"{_shape_text(profile_shape(lam, 'mean_ret'), '留存')}。", "search_results.csv"))
        step = largest_step(lam, "mean_ret")
        if step and abs(step[2]) > 2 * step[3]:
            consistent = ""
            if lam_v:
                sv = largest_step(lam_v, "mean_ret")
                if sv and (sv[0], sv[1]) == (step[0], step[1]):
                    consistent = "；复核种子上最大一步出现在同一区间"
            out.append(Finding(
                f"留存损失并非线性：λ {step[0]:g}→{step[1]:g} 一步使核心层留存变化 {_pp(step[2])}，"
                f"约为其余相邻步平均幅度（{step[3] * 100:.1f} 个百分点）的 {abs(step[2]) / step[3]:.1f} 倍{consistent}。"
                f"在此假设情景中，这一区间是机制强度的边际代价陡增点。", "search_results.csv"))
        sc = sign_change(lam, "mean_delta_rev_fen")
        if sc:
            out.append(Finding(
                f"相对基准点（λ={base_lambda:g}, a={base_a:g}）的同 seed 配对收入差均值在 λ={sc[0]:g}→{sc[1]:g} 之间过零"
                f"（{_yuan(next(r['mean_delta_rev_fen'] for r in lam if r['level'] == sc[0]))} → "
                f"{_yuan(next(r['mean_delta_rev_fen'] for r in lam if r['level'] == sc[1]))}）。"
                f"该过零点取决于基准点与对 a 的平均方式，不是收入峰值；收入形状以上一条为准。",
                "search_results.csv：delta_revenue_vs_base"))
        worst = max(lam, key=lambda r: abs(r["mean_delta_rev_fen"] or 0))
        if worst["sd_delta_rev_fen"] is not None:
            out.append(Finding(f"配对差的离散度：λ={worst['level']:g} 处配对收入差均值 {_yuan(worst['mean_delta_rev_fen'])}、"
                               f"SD {_yuan(worst['sd_delta_rev_fen'])}、负值占比 {_pct(worst['share_delta_rev_negative'] or 0, 0)}"
                               f"（n={worst['n']} 个候选×种子，同 seed 共享 CRN，非独立重复）。", "search_results.csv"))
    a = axis_paired_effects(search_rows, axis=1)
    if a:
        out.append(Finding(f"支援增益缩放 a 从 {a[0]['level']:g} 到 {a[-1]['level']:g}（对 λ 取平均）：核心层留存均值 "
                           f"{_pct(a[0]['mean_ret'])} → {_pct(a[-1]['mean_ret'])}，每 run 累计现金 {_yuan(a[0]['mean_rev_fen'])} → "
                           f"{_yuan(a[-1]['mean_rev_fen'])}；{_shape_text(profile_shape(a, 'mean_rev_fen'), '收入')}，"
                           f"{_shape_text(profile_shape(a, 'mean_ret'), '留存')}。", "search_results.csv"))
    val = [r for r in robust_rows if r.get("split") == "validation" and r.get("perturbation_id") == "P0"]
    if val:
        top = sorted(val, key=lambda r: (-(_f(r.get("inclusion_frequency")) or 0), r["candidate_id"]))
        best = [r["candidate_id"] for r in top if (_f(r.get("inclusion_frequency")) or 0) == (_f(top[0].get("inclusion_frequency")) or 0)]
        out.append(Finding(f"复核种子下非支配入选频率最高的候选：{'、'.join(best[:5])}"
                           f"（{_pct(_f(top[0].get('inclusion_frequency')) or 0, 0)}）；只描述这几次复核结果。",
                           "seed_robustness.csv"))
    rho = next((r for r in rho_rows if r.get("comparison") == "train_mean_vs_validation_mean"), None)
    if rho:
        rr, rt = _f(rho.get("rho_rev")), _f(rho.get("rho_ret"))
        if rr is not None and rt is not None:
            out.append(Finding(f"筛选→复核排序一致性：收入 ρ={rr:.2f}、留存 ρ={rt:.2f}。" +
                               ("留存排序更稳定，收入排序受人群随机构成影响更大。" if rt - rr > 0.2 else ""),
                               "rank_stability.csv"))
    if sens_rows:
        acc: dict[str, list[tuple[float, float]]] = {}
        for r in sens_rows:
            acc.setdefault(r["perturbation_id"], []).append((_f(r.get("rel_change_revenue_vs_P0")) or 0,
                                                            _f(r.get("rel_change_retention_vs_P0")) or 0))
        parts, zero = [], []
        for pid, v in sorted(acc.items()):
            mr, mt = _mean([x for x, _ in v]), _mean([y for _, y in v])
            if abs(mr) < 1e-12 and abs(mt) < 1e-12:
                zero.append(pid)
            else:
                parts.append(f"{pid} 收入 {mr * 100:+.2f}%、留存 {mt * 100:+.2f}%")
        if parts:
            out.append(Finding("痛感参数单因素扰动（对 25 候选取平均的相对变化）：" + "；".join(parts) + "。",
                               "parameter_sensitivity.csv"))
        if zero:
            out.append(Finding(f"{'、'.join(zero)} 在本实验全部候选上收入与留存相对变化均为 0：这些扰动没有改变任何玩家的去留"
                               f"（机制见下条生命周期日志交叉核对）。", "parameter_sensitivity.csv"))
    return out


def budget_fatigue_findings(cross_rows: Sequence[Mapping[str, Any]], budget_weight: Optional[float],
                            success_recovery: Optional[float], perturb_factors: Sequence[float] = (),
                            label: str = "") -> list[Finding]:
    """直购痛感（预算使用率进入疲劳）与满星恢复的交叉：只用生命周期日志计数推出结论。"""
    used = [r for r in cross_rows if str(r.get("budget_used")) == "1"]
    n_used = sum(int(_f(r.get("player_periods")) or 0) for r in used)
    if n_used == 0:
        return [Finding(f"{label}没有任何活跃玩家-期使用直购额度，预算痛感项在本情景下无从生效。",
                        "budget_fatigue_cross.csv")]
    used_fs = sum(int(_f(r.get("player_periods")) or 0) for r in used if str(r.get("full_star_post")) == "1")
    used_f0 = sum(int(_f(r.get("fatigue_after_zero")) or 0) for r in used)
    used_churn = sum(int(_f(r.get("churned")) or 0) for r in used)
    notfs = [r for r in cross_rows if str(r.get("full_star_post")) == "0"]
    n_notfs = sum(int(_f(r.get("player_periods")) or 0) for r in notfs)
    n_notfs_used = sum(int(_f(r.get("player_periods")) or 0) for r in notfs if str(r.get("budget_used")) == "1")
    churn_all = sum(int(_f(r.get("churned")) or 0) for r in cross_rows)
    churn_notfs = sum(int(_f(r.get("churned")) or 0) for r in notfs)
    txt = (f"{label}直购痛感与满星恢复的交叉：使用直购额度的 {n_used} 个活跃玩家-期中 {used_fs} 个"
           f"（{_pct(used_fs / n_used, 0)}）期末满星，{used_f0} 个期末疲劳被截断在下界 0，其中流失 {used_churn} 人次；"
           f"未满星的 {n_notfs} 个玩家-期中仅 {n_notfs_used} 个使用直购，"
           f"而全部 {churn_all} 次流失里有 {churn_notfs} 次发生在未满星期。")
    out = [Finding(txt, "budget_fatigue_cross.csv")]
    if used_f0 == n_used and n_notfs_used == 0:
        msg = ("业务含义：在本情景中“掏钱的人”和“快流失的人”几乎不重叠——直购集中在当期满星的玩家身上，"
               "满星带来的疲劳恢复把直购压力完全抵消；濒临流失的未满星玩家很少直购，直购压力因此无法传导到流失。"
               "对付费设计的提示是：付费痛点若只作用于已获得目标的玩家，调节它不会改变留存，留存杠杆在未满星玩家的获取与战力缺口上。")
        if budget_weight is not None and success_recovery is not None and perturb_factors:
            hi = max(perturb_factors) * budget_weight
            if hi < success_recovery:
                msg += (f"另外，按配置每期预算项最大为 w_b×b≤{hi:g}（扰动上限），小于满星恢复 r_F={success_recovery:g}，"
                        "所以满星期内无论扰动多大都会被截断，零效应是结构性的。")
        out.append(Finding(msg + "这是模型设定下的推演，不是对真实玩家心理的估计。", "budget_fatigue_cross.csv"))
    return out


def render(findings: Iterable[Finding]) -> str:
    items = list(findings)
    if not items:
        return ""
    lines = ["> **核心业务发现（Key Takeaways）**", ">"]
    for f in items:
        lines.append(f"> - {f.text} <sub>依据：{f.basis}</sub>")
    return "\n".join(lines) + "\n\n"
