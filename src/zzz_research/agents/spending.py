"""Subscription and direct-purchase willingness from information available before paying."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional

from ..contracts import LifecycleState, deep_thaw


class SpendingError(ValueError):
    pass


def _finite(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise SpendingError(f"{name} 必须为有限数值")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SpendingError(f"{name} 必须为有限数值") from exc
    if not math.isfinite(result):
        raise SpendingError(f"{name} 必须为有限数值")
    return result


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    numerator = math.exp(value)
    return numerator / (1.0 + numerator)


def _draw(utility: float, u: Optional[float]) -> tuple[float, bool]:
    if u is None or not 0.0 <= u < 1.0:
        raise SpendingError("随机消费决策需要 [0,1) 内的随机值")
    probability = _sigmoid(utility)
    return probability, u < probability


@dataclass(frozen=True)
class ChannelParams:
    intercept: float
    fatigue_weight: float
    failure_weight: float
    hoarding_weight: float
    paid_failure_weight: float
    persistence_bonus: float

    @classmethod
    def from_config(cls, value: Mapping[str, Any]) -> "ChannelParams":
        result = cls(**{key: _finite(key, value[key]) for key in cls.__dataclass_fields__})
        if any(getattr(result, key) < 0 for key in cls.__dataclass_fields__ if key != "intercept"):
            raise SpendingError("消费阻力与状态持续系数必须非负")
        return result

    def utility(self, previous_allowed: bool, previous_failure: bool,
                lifecycle: LifecycleState) -> float:
        fatigue = _finite("fatigue", lifecycle.fatigue)
        failure_span = _finite("failure_span", lifecycle.failure_span)
        hoarding = _finite("hoarding", lifecycle.hoarding)
        return (self.intercept + self.persistence_bonus * previous_allowed
                - self.fatigue_weight * fatigue
                - self.failure_weight * failure_span
                - self.hoarding_weight * hoarding
                - self.paid_failure_weight * previous_failure)


@dataclass(frozen=True)
class SpendingParams:
    subscription: ChannelParams
    topup: ChannelParams
    gain_weight: float
    cost_weight: float

    @classmethod
    def from_config(cls, behavior_cfg: Mapping[str, Any]) -> Optional["SpendingParams"]:
        """Legacy behavior has no spending-state model; the v2 block defines both channels."""
        config = deep_thaw(behavior_cfg)
        if not str(config.get("model_version", "")).startswith("behavior_v2"):
            return None
        research = config.get("research_v2") or {}
        if "spending" not in research:
            return None
        spending = research["spending"]
        topup = spending["topup"]
        gain = _finite("gain_weight", topup["gain_weight"])
        cost = _finite("cost_weight", topup["cost_weight"])
        if gain < 0 or cost < 0:
            raise SpendingError("预期收益与现金成本权重必须非负")
        return cls(ChannelParams.from_config(spending["subscription"]),
                   ChannelParams.from_config(topup), gain, cost)


@dataclass(frozen=True)
class SpendingState:
    """Willingness is separate from initial budget capacity, wallet credits and activity."""

    subscription_active: bool
    topup_allowed: bool
    last_paid_failure: bool = False


@dataclass(frozen=True)
class SpendingDecision:
    state: SpendingState
    probability: float
    utility: Optional[float]
    allowed: bool
    reason: str


def subscription_decision(params: SpendingParams, prev: SpendingState, lifecycle: LifecycleState, *,
                          tier_subscribed: bool, u: Optional[float]) -> SpendingDecision:
    """Decide the next renewal using prior lifecycle state; existing entitlements remain valid."""
    if not lifecycle.active_at_end or not tier_subscribed:
        reason = "inactive" if not lifecycle.active_at_end else "no_subscription_capacity"
        return SpendingDecision(replace(prev, subscription_active=False), 0.0, None, False, reason)
    utility = params.subscription.utility(prev.subscription_active, prev.last_paid_failure, lifecycle)
    probability, allowed = _draw(utility, u)
    return SpendingDecision(replace(prev, subscription_active=allowed), probability, utility, allowed,
                            "renew" if allowed else "pause_renewal")


def paid_decision(params: SpendingParams, prev: SpendingState, lifecycle: LifecycleState, *,
                  paid_cap: int, expected_gain: float, expected_topup_pulls: float,
                  u: Optional[float]) -> SpendingDecision:
    """Decide direct-purchase permission before drawing; free-pull policy is not changed here."""
    if isinstance(paid_cap, bool) or not isinstance(paid_cap, int) or paid_cap < 0:
        raise SpendingError("paid_cap 必须为非负整数")
    if not lifecycle.active_at_end or paid_cap == 0:
        reason = "inactive" if not lifecycle.active_at_end else "no_paid_capacity"
        return SpendingDecision(replace(prev, topup_allowed=False), 0.0, None, False, reason)
    gain = _finite("expected_gain", expected_gain)
    cost = _finite("expected_topup_pulls", expected_topup_pulls)
    if cost < 0:
        raise SpendingError("预期直购抽数必须非负")
    utility = (params.topup.utility(prev.topup_allowed, prev.last_paid_failure, lifecycle)
               + params.gain_weight * gain - params.cost_weight * cost / paid_cap)
    probability, allowed = _draw(utility, u)
    return SpendingDecision(replace(prev, topup_allowed=allowed), probability, utility, allowed,
                            "allow_topup" if allowed else "decline_topup")
