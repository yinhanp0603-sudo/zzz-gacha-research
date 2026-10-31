"""M-BEHAVIOR 8.4：囤卡记忆、疲劳、失败持续量与随机流失（behavior_v1）。

每期只更新一次；系数全部读取 behavior.json，达标阈值与标准天数来自公共契约。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ..contracts import LifecycleState, contract_value, deep_thaw

EXIT_CHURN = "churn"


class LifecycleError(ValueError):
    pass


def _clip(x: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, x))


@dataclass(frozen=True)
class LifecycleParams:
    full_star_threshold: float
    h_decay_base: float
    h_gap_weight: float
    h_success_recovery: float
    h_min: float
    h_max: float
    f_decay_rate: float
    f_gap_weight: float
    f_failure_weight: float
    f_failure_span_cap: float
    f_budget_weight: float
    f_success_recovery: float
    f_min: float
    f_max: float
    churn_base_hazard: float
    churn_fatigue_sensitivity: float
    initial_fatigue: float
    initial_failure_span: float

    @classmethod
    def from_config(cls, behavior_cfg: Mapping[str, Any]) -> "LifecycleParams":
        c = deep_thaw(behavior_cfg)
        h, f, ch, ini = c["hoarding"], c["fatigue"], c["churn"], c["initial"]
        p = cls(
            full_star_threshold=float(c["full_star_threshold"]),
            h_decay_base=float(h["decay_base"]), h_gap_weight=float(h["gap_weight"]),
            h_success_recovery=float(h["success_recovery"]), h_min=float(h["min"]), h_max=float(h["max"]),
            f_decay_rate=float(f["decay_rate"]), f_gap_weight=float(f["gap_weight"]),
            f_failure_weight=float(f["failure_weight"]), f_failure_span_cap=float(f["failure_span_cap"]),
            f_budget_weight=float(f["budget_weight"]), f_success_recovery=float(f["success_recovery"]),
            f_min=float(f["min"]), f_max=float(f["max"]),
            churn_base_hazard=float(ch["base_hazard"]),
            churn_fatigue_sensitivity=float(ch["fatigue_sensitivity"]),
            initial_fatigue=float(ini["fatigue"]), initial_failure_span=float(ini["failure_span"]),
        )
        if p.full_star_threshold != contract_value("behavior.full_star_threshold"):
            raise LifecycleError("达标阈值必须与公共契约一致")
        if p.h_min > p.h_max or p.f_min > p.f_max:
            raise LifecycleError("min 不能大于 max")
        return p

    def initial_state(self, initial_hoarding: float) -> LifecycleState:
        return LifecycleState(True, True, _clip(initial_hoarding, self.h_min, self.h_max),
                              _clip(self.initial_fatigue, self.f_min, self.f_max), self.initial_failure_span)


@dataclass(frozen=True)
class LifecycleStep:
    state: LifecycleState
    p_churn: float
    churned: bool
    gap: float
    budget_utilization: float
    h_before: float
    f_before: float


def step(params: LifecycleParams, prev: LifecycleState, *, ci_post: Optional[float],
         elapsed_days: float, topup_pulls_used: int, paid_cap: int, u_churn: float,
         exit_day: float) -> LifecycleStep:
    """本期期末更新。prev 必须本期期初活跃。ci_post 为 None（不可评估）时报错，不当作 0。"""
    if not prev.active_at_end:
        raise LifecycleError("已流失玩家不再更新生命周期")
    if ci_post is None or not math.isfinite(ci_post):
        raise LifecycleError("CI_post 不可评估，停止依赖它的生命周期更新")
    if not 0.0 <= u_churn < 1.0:
        raise LifecycleError("流失随机值必须在 [0,1)")
    d = elapsed_days / float(contract_value("economy.standard_span_days"))
    success = ci_post >= params.full_star_threshold
    g = max(0.0, params.full_star_threshold - ci_post)
    s_new = 0.0 if success else prev.failure_span + d
    b = (topup_pulls_used / paid_cap) if paid_cap > 0 else 0.0
    ind = 1.0 if success else 0.0
    h_new = _clip(params.h_decay_base ** d * prev.hoarding + params.h_gap_weight * d * g
                  - params.h_success_recovery * d * ind, params.h_min, params.h_max)
    f_new = _clip(math.exp(-params.f_decay_rate * d) * prev.fatigue
                  + d * (params.f_gap_weight * g
                         + params.f_failure_weight * min(s_new, params.f_failure_span_cap)
                         + params.f_budget_weight * b
                         - params.f_success_recovery * ind),
                  params.f_min, params.f_max)
    p = 1.0 - math.exp(-params.churn_base_hazard * d * math.exp(params.churn_fatigue_sensitivity * f_new))
    churned = u_churn < p
    new = LifecycleState(
        active_at_start=True, active_at_end=not churned, hoarding=h_new, fatigue=f_new,
        failure_span=s_new, exit_day=exit_day if churned else None,
        exit_reason=EXIT_CHURN if churned else None,
    )
    return LifecycleStep(new, p, churned, g, b, prev.hoarding, prev.fatigue)


def inactive_state(prev: LifecycleState) -> LifecycleState:
    """前期已流失者：本期期初、期末均不活跃，状态保留。"""
    return LifecycleState(False, False, prev.hoarding, prev.fatigue, prev.failure_span,
                          prev.exit_day, prev.exit_reason)
