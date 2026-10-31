"""M-BEHAVIOR 8.2：平滑收益、购买效用与下池决策（behavior_v1）。

只消费 PlanForecast 与显式随机值；不执行抽取、不查询数据库。全部系数读取 behavior.json。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional

from ..contracts import ActionIntent, deep_thaw
from ..economy.planner import PlanForecast

SKIP_NO_BUDGET = "no_budget"
SKIP_TARGET_OWNED = "target_owned"
SKIP_CI_UNAVAILABLE = "ci_unavailable"
SKIP_DECLINED = "declined"


class DecisionError(ValueError):
    pass


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


@dataclass(frozen=True)
class TierPolicy:
    tier_id: str
    alpha: float
    initial_hoarding: float
    dps_target: str


@dataclass(frozen=True)
class DecisionParams:
    model_version: str
    comfort_threshold: float
    temperature: float
    beta_gain: float
    beta_comfort: float
    beta_hoarding: float
    beta_resource_cost: float
    beta_paid_cost: float
    tiers: Mapping[str, TierPolicy]
    support_target: str
    beta_novelty: float = 0.0

    @classmethod
    def from_config(cls, behavior_cfg: Mapping[str, Any]) -> "DecisionParams":
        c = deep_thaw(behavior_cfg)
        if not (isinstance(c["temperature"], (int, float)) and c["temperature"] > 0):
            raise DecisionError("temperature τ 必须 > 0")
        u = c["utility"]
        tiers = {tid: TierPolicy(tid, float(p["alpha"]), float(p["initial_hoarding"]), p["dps_target"])
                 for tid, p in sorted(c["tier_policy"].items())}
        novelty = float(((c.get("research_v2") or {}).get("novelty") or {}).get("beta_novelty", 0.0))
        if not (math.isfinite(novelty) and novelty >= 0):
            raise DecisionError("beta_novelty 必须为非负有限数值")
        return cls(c["model_version"], float(c["comfort_threshold"]), float(c["temperature"]),
                   float(u["beta_gain"]), float(u["beta_comfort"]), float(u["beta_hoarding"]),
                   float(u["beta_resource_cost"]), float(u["beta_paid_cost"]),
                   MappingProxyType(tiers), c["support_target"], novelty)

    def smooth_gain(self, ci: float) -> float:
        """G(c) = β_g·c + β_s·σ((c − c_comfort)/τ)。"""
        return self.beta_gain * ci + self.beta_comfort * _sigmoid((ci - self.comfort_threshold) / self.temperature)


@dataclass(frozen=True)
class DecisionRecord:
    """fact_decision_log 一行的业务内容（由 runtime 写出）。"""

    target_path: str
    ci_pre: Optional[float]
    available_pulls: int
    remaining_paid_cap: int
    expected_gain: Optional[float]
    expected_pulls: Optional[float]
    expected_topup_pulls: Optional[float]
    utility: Optional[float]
    p_enter: Optional[float]
    did_enter: bool
    skip_reason: Optional[str]
    planner_version: str


def utility(params: DecisionParams, tier_id: str, hoarding: float, fc: PlanForecast,
            novelty: bool = False) -> tuple[float, float]:
    """返回 (U, E[G(CI_K)] − G(CI_0))；novelty 为新主C池时 U 另加不随 CI_0 归零的 β_novelty。"""
    if fc.budget_pulls <= 0:
        raise DecisionError("B=0 时不计算购买概率")
    if any(c is None for c in fc.ci_k):
        raise DecisionError("CI 不可评估时不计算效用")
    tier = params.tiers[tier_id]
    eg = sum(p * params.smooth_gain(c) for p, c in zip(fc.k_probability, fc.ci_k))  # type: ignore[arg-type]
    gain = eg - params.smooth_gain(fc.ci_k[0])  # type: ignore[arg-type]
    u = (tier.alpha - params.beta_hoarding * hoarding + gain
         + (params.beta_novelty if novelty else 0.0)
         - params.beta_resource_cost * fc.expected_spent / fc.budget_pulls
         - params.beta_paid_cost * fc.expected_topup / max(fc.paid_pulls, 1))
    return u, gain


def decide(params: DecisionParams, *, tier_id: str, hoarding: float, path_id: str,
           forecast: PlanForecast, u_decision: Optional[float],
           novelty: bool = False) -> tuple[ActionIntent, DecisionRecord]:
    """一次下池决策。u_decision 为本事件独立的下池随机值；跳过时不需要。"""
    base = dict(target_path=path_id, ci_pre=forecast.ci_k[0], available_pulls=forecast.free_pulls,
                remaining_paid_cap=forecast.paid_pulls, planner_version=forecast.planner_version)

    def skip(reason: str) -> tuple[ActionIntent, DecisionRecord]:
        rec = DecisionRecord(**base, expected_gain=None, expected_pulls=None, expected_topup_pulls=None,
                             utility=None, p_enter=None, did_enter=False, skip_reason=reason)
        return ActionIntent(False, None, 0, {"skip_reason": reason}), rec

    if forecast.target_owned:
        return skip(SKIP_TARGET_OWNED)
    if forecast.budget_pulls == 0:
        return skip(SKIP_NO_BUDGET)
    if not forecast.eval_status.ci_defined or any(c is None for c in forecast.ci_k):
        return skip(SKIP_CI_UNAVAILABLE)
    if u_decision is None or not 0.0 <= u_decision < 1.0:
        raise DecisionError("需要 [0,1) 内的下池随机值")
    u, gain = utility(params, tier_id, hoarding, forecast, novelty)
    p = _sigmoid(u)
    enter = u_decision < p
    rec = DecisionRecord(**base, expected_gain=gain, expected_pulls=forecast.expected_spent,
                         expected_topup_pulls=forecast.expected_topup, utility=u, p_enter=p,
                         did_enter=enter, skip_reason=None if enter else SKIP_DECLINED)
    intent = ActionIntent(enter, forecast.plan if enter else None,
                          forecast.budget_pulls if enter else 0,
                          {"utility": u, "p_enter": p, "u": u_decision})
    return intent, rec
