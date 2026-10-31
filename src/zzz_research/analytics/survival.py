"""M-STATS 11.2 / 11.3：L2 参考轨迹的首次观测失效（Kaplan–Meier 描述）与动态可用率。

- 观测单位为 reference_subject；事件=首次评估 CI<达标阈值（含 age=0 的首次观测即失败）。
- 事件后退出风险集；未失败者在最后评估日右删失。中位时间取首个 S(t)≤0.5 的观测时点，否则 MedianNotReached。
- 不插值、不外推；时变 Cox 默认关闭（仅探索性，需另行启用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from ..contracts import contract_value
from .reference import ReferenceObservation

MEDIAN_NOT_REACHED = "MedianNotReached"


@dataclass(frozen=True)
class SubjectOutcome:
    subject_id: str
    group: tuple[str, ...]
    time: float          # 事件或删失的 age_days
    event: bool
    entry_age: float
    n_obs: int
    reason: str = "observation_end"


def first_failure(observations: Sequence[ReferenceObservation], group_by: Sequence[str], *,
                  diagnostics: Optional[list[dict]] = None
                  ) -> list[SubjectOutcome]:
    """只使用首次事件或首次未知之前的可评估前缀。

    首次观测未知的主体排除；成功前缀后遇未知，在最后成功日保守右删失，
    不跨未知间隔认定首次失效。已观测首次事件之后的未知不撤销事件。
    diagnostics 可记录排除/删失原因；该规则不保证删失具有独立性。
    """
    thr = contract_value("behavior.full_star_threshold")
    by_subject: dict[str, list[ReferenceObservation]] = {}
    for o in observations:
        by_subject.setdefault(o.subject.reference_subject_id, []).append(o)
    out = []
    for sid, obs in sorted(by_subject.items()):
        obs = sorted(obs, key=lambda o: o.age_days)
        s = obs[0].subject
        group = tuple(str(getattr(s, g)) for g in group_by)
        valid = []
        event = False
        reason = "observation_end"
        for o in obs:
            if o.ci is None or o.eval_status not in ("ok", "no_feasible_team"):
                reason = ("censored_at_last_valid_before_unknown" if valid else
                          "excluded_first_observation_unknown")
                break
            valid.append(o)
            if o.ci < thr:
                event, reason = True, "first_observed_failure"
                break
        if diagnostics is not None:
            diagnostics.append({"subject_id": sid, "group": group, "included": bool(valid),
                                "reason": reason, "n_obs_total": len(obs), "n_obs_used": len(valid),
                                "time": valid[-1].age_days if valid else None, "event": event})
        if valid:
            out.append(SubjectOutcome(sid, group, valid[-1].age_days, event,
                                      valid[0].age_days, len(valid), reason))
    return out


@dataclass(frozen=True)
class KMStep:
    time: float
    at_risk: int
    events: int
    censored: int
    survival: float


@dataclass(frozen=True)
class KMResult:
    group: tuple[str, ...]
    n_subjects: int
    n_events: int
    steps: tuple[KMStep, ...]
    median: float | str

    def survival_at(self, t: float) -> float:
        s = 1.0
        for st in self.steps:
            if st.time <= t:
                s = st.survival
        return s


def kaplan_meier(outcomes: Iterable[SubjectOutcome]) -> KMResult:
    outs = list(outcomes)
    if not outs:
        raise ValueError("空组无法估计 KM")
    group = outs[0].group
    times = sorted({o.time for o in outs})
    s, steps = 1.0, []
    for t in times:
        at_risk = sum(1 for o in outs if o.time >= t and o.entry_age <= t)
        d = sum(1 for o in outs if o.time == t and o.event)
        c = sum(1 for o in outs if o.time == t and not o.event)
        if at_risk and d:
            s *= 1.0 - d / at_risk
        steps.append(KMStep(t, at_risk, d, c, s))
    median: float | str = next((st.time for st in steps if st.survival <= 0.5), MEDIAN_NOT_REACHED)
    return KMResult(group, len(outs), sum(o.event for o in outs), tuple(steps), median)


def km_by_group(observations: Sequence[ReferenceObservation], group_by: Sequence[str], *,
                diagnostics: Optional[list[dict]] = None) -> list[KMResult]:
    outs = first_failure(observations, group_by, diagnostics=diagnostics)
    groups: dict[tuple[str, ...], list[SubjectOutcome]] = {}
    for o in outs:
        groups.setdefault(o.group, []).append(o)
    return [kaplan_meier(v) for _, v in sorted(groups.items())]


def dynamic_availability(observations: Sequence[ReferenceObservation], group_by: Sequence[str]
                         ) -> list[dict]:
    """各 age 上达标主体占比（恢复仍计入，不撤销已发生的首次失效）。"""
    acc: dict[tuple, list[int]] = {}
    for o in observations:
        if o.is_full_star is None:
            continue
        key = (tuple(str(getattr(o.subject, g)) for g in group_by), o.age_days)
        a = acc.setdefault(key, [0, 0])
        a[0] += int(o.is_full_star)
        a[1] += 1
    return [{"group": " / ".join(g), "age_days": age, "full_star": k, "subjects": n, "share": k / n}
            for (g, age), (k, n) in sorted(acc.items())]


def cox_status(enabled: bool = False) -> dict:
    """时变 Cox 默认关闭：仅在显式启用且依赖可用、事件足够时才作为探索性关联运行。"""
    if not enabled:
        return {"status": "skipped", "reason": "disabled_by_default (statistics.md 11.3 探索性、默认关闭)"}
    return {"status": "skipped", "reason": "lifelines 未安装；本版不内置 Cox 估计"}


def trajectory_matrix(observations: Sequence[ReferenceObservation], subject_filter=None) -> list[dict]:
    """逐轨迹 CI 矩阵：轨迹少时直接展示，不强制 KM。"""
    rows: dict[str, dict] = {}
    for o in observations:
        s = o.subject
        if subject_filter and not subject_filter(s):
            continue
        r = rows.setdefault(s.reference_subject_id, {
            "anchor": s.anchor_unit_id, "plan": s.investment_plan, "gear": s.gear_policy,
            "support": s.support_policy, "ci_by_t": {}})
        r["ci_by_t"][o.t_idx] = o.ci
    return sorted(rows.values(), key=lambda r: (r["anchor"], r["plan"], r["gear"], r["support"]))


def km_summary_row(km: KMResult, labels: Sequence[str]) -> dict:
    return {**dict(zip(labels, km.group)), "subjects": km.n_subjects, "events": km.n_events,
            "median_age_days": km.median,
            "S_at_last": km.steps[-1].survival if km.steps else None}


def median_value(km: KMResult) -> Optional[float]:
    return None if km.median == MEDIAN_NOT_REACHED else float(km.median)
