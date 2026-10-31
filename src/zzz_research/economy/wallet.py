"""M-WALLET：来源批次、发放、订阅、逐抽直购与守恒（wallet_v1）。

全部数值读取 economy.json 与 population.json（Tier 额度）；金额整数分、credit 整数。
prepare_payment 只产生支付草案，不提交；gacha 不调用本模块。
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..contracts import (
    CashEvent,
    CreditBatch,
    CreditSource,
    ResourceDelta,
    WalletState,
    deep_thaw,
)


class WalletError(ValueError):
    pass


#: economy.json spend_priority 名称 → 来源
PRIORITY_SOURCES: Mapping[str, Optional[CreditSource]] = {
    "historical_free_fifo": CreditSource.HISTORICAL_FREE,
    "current_free": CreditSource.CURRENT_FREE,
    "subscription_fifo": CreditSource.SUBSCRIPTION,
    "instant_topup": None,  # 逐抽直购，非库存来源
}

PRODUCT_SUBSCRIPTION = "subscription"
PRODUCT_TOPUP = "instant_topup"


def _exact(x: Any, name: str) -> Fraction:
    if isinstance(x, bool) or not isinstance(x, (int, float, Fraction)):
        raise WalletError(f"{name} 必须是数值")
    if isinstance(x, float) and not math.isfinite(x):
        raise WalletError(f"{name} 必须有限")
    return Fraction(x)


def _floor_accrual(total_per_span: int, span_days: int, start_day: Any, end_day: Any) -> int:
    """floor(total·end/span) − floor(total·start/span)，避免分数在多区间重复取整。"""
    s, e = _exact(start_day, "start_day"), _exact(end_day, "end_day")
    if e < s or s < 0:
        raise WalletError("需要 0 ≤ start_day ≤ end_day")
    return math.floor(Fraction(total_per_span) * e / span_days) - math.floor(
        Fraction(total_per_span) * s / span_days)


@dataclass(frozen=True)
class WalletPolicy:
    model_version: str
    credit_per_pull: int
    standard_span_days: int
    free_pulls_per_span: int
    sub_renewal_days: int
    sub_price_fen: int
    sub_grant_pulls: int
    sub_rollover: bool
    topup_price_fen_per_pull: int
    topup_whole_pull_only: bool
    topup_balance_rollover: bool
    spend_order: tuple[CreditSource, ...]
    topup_enabled: bool
    allow_mixed_payment: bool
    paid_budget_rollover: bool

    @classmethod
    def from_config(cls, economy_cfg: Mapping[str, Any]) -> "WalletPolicy":
        c = deep_thaw(economy_cfg)
        prio = c["spend_priority"]
        unknown = [p for p in prio if p not in PRIORITY_SOURCES]
        if unknown or len(set(prio)) != len(prio):
            raise WalletError(f"spend_priority 非法: {prio}")
        if "instant_topup" in prio and prio[-1] != "instant_topup":
            raise WalletError("instant_topup 必须位于消耗顺序末尾（库存不足一整抽后才直购）")
        order = tuple(PRIORITY_SOURCES[p] for p in prio if PRIORITY_SOURCES[p] is not None)
        sub, top = c["subscription"], c["topup"]
        ints = {
            "credit_minor_per_pull": c["credit_minor_per_pull"],
            "standard_span_days": c["standard_span_days"],
            "free_pulls_per_standard_span": c["free_pulls_per_standard_span"],
            "subscription.renewal_days": sub["renewal_days"],
            "subscription.price_fen": sub["price_fen"],
            "subscription.grant_pulls_immediately": sub["grant_pulls_immediately"],
            "topup.price_fen_per_pull": top["price_fen_per_pull"],
        }
        for k, v in ints.items():
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise WalletError(f"{k} 必须是非负整数")
        if c["credit_minor_per_pull"] == 0 or c["standard_span_days"] == 0 or sub["renewal_days"] == 0:
            raise WalletError("单抽 credit、标准天数与续订周期必须为正")
        if not top["instant_whole_pull_only"] or top["balance_rollover"]:
            raise WalletError("wallet_v1 只支持整抽即时直购且无待用余额；礼包预充值需新增余额模型")
        return cls(
            model_version=c["model_version"],
            credit_per_pull=c["credit_minor_per_pull"],
            standard_span_days=c["standard_span_days"],
            free_pulls_per_span=c["free_pulls_per_standard_span"],
            sub_renewal_days=sub["renewal_days"],
            sub_price_fen=sub["price_fen"],
            sub_grant_pulls=sub["grant_pulls_immediately"],
            sub_rollover=bool(sub["unused_credit_rollover"]),
            topup_price_fen_per_pull=top["price_fen_per_pull"],
            topup_whole_pull_only=True,
            topup_balance_rollover=False,
            spend_order=order,
            topup_enabled="instant_topup" in prio,
            allow_mixed_payment=bool(c["allow_mixed_existing_provenance_payment"]),
            paid_budget_rollover=bool(c["paid_budget_rollover"]),
        )

    # -- 7.2 发放与预算 ---------------------------------------------------

    def free_credit_for(self, start_day: Any, end_day: Any) -> int:
        return _floor_accrual(self.free_pulls_per_span * self.credit_per_pull,
                              self.standard_span_days, start_day, end_day)

    def paid_cap_for(self, budget_pulls_per_span: int, start_day: Any, end_day: Any) -> int:
        if isinstance(budget_pulls_per_span, bool) or not isinstance(budget_pulls_per_span, int) \
                or budget_pulls_per_span < 0:
            raise WalletError("Tier 直购额度必须是非负整数")
        return _floor_accrual(budget_pulls_per_span, self.standard_span_days, start_day, end_day)

    def renewal_days_in(self, start_day: Any, end_day: Any, first_day: Any = 0) -> list[Fraction]:
        """订阅续购日 first_day + k·renewal 落在 [start_day, end_day) 内的日期。"""
        s, e, f = _exact(start_day, "start"), _exact(end_day, "end"), _exact(first_day, "first")
        k = max(0, math.ceil((s - f) / self.sub_renewal_days))
        out = []
        while f + k * self.sub_renewal_days < e:
            out.append(f + k * self.sub_renewal_days)
            k += 1
        return out


# ---------------------------------------------------------------------------
# 状态变更（纯函数，返回新状态与事件）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WalletChange:
    state: WalletState
    resource_deltas: tuple[ResourceDelta, ...] = ()
    cash_events: tuple[CashEvent, ...] = ()


def _with_batches(state: WalletState, batches: Iterable[CreditBatch]) -> WalletState:
    return replace(state, batches=tuple(b for b in batches))


def roll_period(state: WalletState) -> WalletState:
    """新一期开始：上期“本期免费”批次转为历史免费（同一资产，不复制）。"""
    return _with_batches(state, (
        replace(b, source=CreditSource.HISTORICAL_FREE) if b.source is CreditSource.CURRENT_FREE else b
        for b in state.batches))


def grant_free(policy: WalletPolicy, state: WalletState, *, t_idx: int, start_day: Any,
               end_day: Any, grant_day: Any) -> WalletChange:
    if any(b.batch_id == f"free:t{t_idx}" for b in state.batches):
        raise WalletError(f"第 {t_idx} 期免费资源已发放")
    if _exact(grant_day, "grant_day") > _exact(end_day, "end_day"):
        raise WalletError("发放事件不得晚于本期评估")
    amount = policy.free_credit_for(start_day, end_day)
    bid = f"free:t{t_idx}"
    batch = CreditBatch(bid, CreditSource.CURRENT_FREE, amount, float(grant_day))
    delta = ResourceDelta(f"grant:{bid}", bid, CreditSource.CURRENT_FREE, amount, float(grant_day))
    return WalletChange(_with_batches(state, (*state.batches, batch)), (delta,))


def purchase_subscription(policy: WalletPolicy, state: WalletState, *, day: Any,
                          event_id: str) -> WalletChange:
    credit = policy.sub_grant_pulls * policy.credit_per_pull
    bid = f"sub:{event_id}"
    if any(b.batch_id == bid for b in state.batches):
        raise WalletError(f"订阅事件 {event_id} 已存在")
    batches = state.batches
    if not policy.sub_rollover:  # 不滚存时旧订阅余额作废（显式记为负变动）
        expired = [b for b in batches if b.source is CreditSource.SUBSCRIPTION and b.credit_minor > 0]
    else:
        expired = []
    exp_deltas = tuple(ResourceDelta(f"expire:{b.batch_id}:{event_id}", b.batch_id, b.source,
                                     -b.credit_minor, float(day)) for b in expired)
    batches = tuple(replace(b, credit_minor=0) if b in expired else b for b in batches)
    cash = CashEvent(event_id, float(day), policy.sub_price_fen, PRODUCT_SUBSCRIPTION, credit)
    batch = CreditBatch(bid, CreditSource.SUBSCRIPTION, credit, float(day))
    grant = ResourceDelta(f"grant:{bid}", bid, CreditSource.SUBSCRIPTION, credit, float(day))
    new = replace(state, batches=(*batches, batch), cash_events=(*state.cash_events, cash))
    return WalletChange(new, (*exp_deltas, grant), (cash,))


def set_period_paid_cap(policy: WalletPolicy, state: WalletState, cap_pulls: int) -> WalletState:
    """本期直购额度；同一期所有 banner 共用，默认不滚存。"""
    if isinstance(cap_pulls, bool) or not isinstance(cap_pulls, int) or cap_pulls < 0:
        raise WalletError("直购额度必须是非负整数")
    carry = state.direct_purchase_remaining_pulls if policy.paid_budget_rollover else 0
    return replace(state, direct_purchase_remaining_pulls=carry + cap_pulls)


def stock_credit(policy: WalletPolicy, state: WalletState) -> int:
    srcs = policy.spend_order  # 至多 3 个来源：元组成员检查比每次建集合更快
    total = 0
    for b in state.batches:
        if b.source in srcs:
            total += b.credit_minor
    return total


def stock_pulls(policy: WalletPolicy, state: WalletState) -> int:
    """可由已有库存支付的整抽数（零碎余额不计）。"""
    if policy.allow_mixed_payment:
        return stock_credit(policy, state) // policy.credit_per_pull
    return sum(b.credit_minor // policy.credit_per_pull for b in state.batches
               if b.source in policy.spend_order)


def can_pay(policy: WalletPolicy, state: WalletState, allow_topup: bool = True) -> bool:
    return stock_pulls(policy, state) >= 1 or (
        allow_topup and policy.topup_enabled and state.direct_purchase_remaining_pulls >= 1)


def _ordered_batches(policy: WalletPolicy, state: WalletState) -> list[CreditBatch]:
    rank = {s: i for i, s in enumerate(policy.spend_order)}
    return sorted((b for b in state.batches if b.source in rank and b.credit_minor > 0),
                  key=lambda b: (rank[b.source], b.acquired_day, b.batch_id))


def prepare_payment(policy: WalletPolicy, state: WalletState, *, pull_id: str, day: Any,
                    allow_topup: bool = True) -> Optional[WalletChange]:
    """一抽支付草案（不提交）。无法支付返回 None。"""
    need = policy.credit_per_pull
    d = float(day)
    if stock_pulls(policy, state) >= 1:
        ordered = _ordered_batches(policy, state)
        if not policy.allow_mixed_payment:
            ordered = [next(b for b in ordered if b.credit_minor >= need)]
        take: dict[str, int] = {}
        remaining = need
        for b in ordered:
            if remaining == 0:
                break
            x = min(b.credit_minor, remaining)
            take[b.batch_id] = x
            remaining -= x
        assert remaining == 0
        batches = tuple(replace(b, credit_minor=b.credit_minor - take[b.batch_id]) if b.batch_id in take else b
                        for b in state.batches)  # 只重建被扣款的批次
        deltas = tuple(
            ResourceDelta(f"spend:{pull_id}:{b.batch_id}", b.batch_id, b.source, -take[b.batch_id], d, pull_id)
            for b in ordered if b.batch_id in take)
        return WalletChange(_with_batches(state, batches), deltas)

    if not (allow_topup and policy.topup_enabled and state.direct_purchase_remaining_pulls >= 1):
        return None
    # 整抽即时直购：买 160 credit 并立刻用掉，不动零碎余额
    bid = f"topup:{pull_id}"
    cash = CashEvent(f"cash:{pull_id}", d, policy.topup_price_fen_per_pull, PRODUCT_TOPUP, need, pull_id)
    deltas = (
        ResourceDelta(f"grant:{bid}", bid, CreditSource.INSTANT_TOPUP, need, d, pull_id),
        ResourceDelta(f"spend:{pull_id}:{bid}", bid, CreditSource.INSTANT_TOPUP, -need, d, pull_id),
    )
    new = replace(state, cash_events=(*state.cash_events, cash),
                  direct_purchase_remaining_pulls=state.direct_purchase_remaining_pulls - 1)
    return WalletChange(new, deltas, (cash,))


# ---------------------------------------------------------------------------
# 7.1 / 7.3 用量、收入与守恒
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PullUsage:
    curr_free_used: Fraction
    hoarded_free_used: Fraction
    sub_pulls_used: Fraction
    topup_pulls_used: Fraction

    @property
    def total(self) -> Fraction:
        return self.curr_free_used + self.hoarded_free_used + self.sub_pulls_used + self.topup_pulls_used


def summarize_usage(policy: WalletPolicy, deltas: Iterable[ResourceDelta]) -> PullUsage:
    """四来源等价抽数（可为小数，合计须为整数）。只统计关联抽取的消耗。"""
    acc: dict[CreditSource, int] = defaultdict(int)
    for x in deltas:
        if x.delta_credit_minor < 0 and x.related_pull_id is not None:
            acc[x.source] += -x.delta_credit_minor
    f = lambda s: Fraction(acc[s], policy.credit_per_pull)  # noqa: E731
    return PullUsage(f(CreditSource.CURRENT_FREE), f(CreditSource.HISTORICAL_FREE),
                     f(CreditSource.SUBSCRIPTION), f(CreditSource.INSTANT_TOPUP))


@dataclass(frozen=True)
class Revenue:
    sub_revenue_fen: int
    topup_revenue_fen: int

    @property
    def total_fen(self) -> int:
        return self.sub_revenue_fen + self.topup_revenue_fen


def revenue(cash_events: Iterable[CashEvent]) -> Revenue:
    sub = top = 0
    for e in cash_events:
        if e.product == PRODUCT_SUBSCRIPTION:
            sub += e.amount_fen
        elif e.product == PRODUCT_TOPUP:
            top += e.amount_fen
        else:
            raise WalletError(f"未知商品 {e.product!r}")
    return Revenue(sub, top)


def check_conservation(before: WalletState, after: WalletState,
                       deltas: Sequence[ResourceDelta]) -> None:
    """单期逐批次金额、来源及付费到账对账；before须已完成期初roll_period。

    不接收跨多个时期且混合本期/历史免费标签的累计事件。
    """
    ids = [d.event_id for d in deltas]
    if len(set(ids)) != len(ids):
        raise WalletError("资源变动事件ID重复")
    batches0 = {b.batch_id: b for b in before.batches}
    batches1 = {b.batch_id: b for b in after.batches}
    if len(batches0) != len(before.batches) or len(batches1) != len(after.batches):
        raise WalletError("钱包批次ID重复")
    if set(batches0) - set(batches1):
        raise WalletError("期内不得删除已有批次，零余额批次也应保留")
    for bid in set(batches0) & set(batches1):
        a, b = batches0[bid], batches1[bid]
        if a.source is not b.source or a.acquired_day != b.acquired_day:
            raise WalletError(f"期内批次 {bid} 来源或获取时间改变；先执行期初roll_period")
    if after.cash_events[: len(before.cash_events)] != before.cash_events:
        raise WalletError("现金事件只能追加")
    if len({e.event_id for e in after.cash_events}) != len(after.cash_events):
        raise WalletError("现金事件ID重复")
    new_cash = after.cash_events[len(before.cash_events):]
    paid_batches: dict[str, tuple[CreditSource, CashEvent]] = {}
    for e in new_cash:
        if e.product == PRODUCT_SUBSCRIPTION:
            bid, src = f"sub:{e.event_id}", CreditSource.SUBSCRIPTION
        elif e.product == PRODUCT_TOPUP and e.related_pull_id:
            bid, src = f"topup:{e.related_pull_id}", CreditSource.INSTANT_TOPUP
        else:
            raise WalletError("现金商品未知或直购缺失关联抽取ID")
        if bid in paid_batches:
            raise WalletError(f"付费批次 {bid} 对应多个现金事件")
        paid_batches[bid] = (src, e)
    b0 = {bid: b.credit_minor for bid, b in batches0.items()}
    b1 = {bid: b.credit_minor for bid, b in batches1.items()}
    flow: dict[str, int] = defaultdict(int)
    paid_grants: dict[str, int] = defaultdict(int)
    for d in deltas:
        batch = batches1.get(d.batch_id, batches0.get(d.batch_id))
        payment = paid_batches.get(d.batch_id)
        if batch is not None:
            expected_source = batch.source
        elif payment is not None and payment[0] is CreditSource.INSTANT_TOPUP:
            expected_source = CreditSource.INSTANT_TOPUP
        else:
            raise WalletError(f"资源事件引用未登记批次 {d.batch_id}")
        if d.source is not expected_source:
            raise WalletError(f"批次 {d.batch_id} 来源标签不一致")
        if d.source is CreditSource.INSTANT_TOPUP:
            if payment is None or d.related_pull_id != payment[1].related_pull_id:
                raise WalletError("直购资源与现金未关联同一次抽取")
        if d.delta_credit_minor > 0 and d.source in (CreditSource.SUBSCRIPTION, CreditSource.INSTANT_TOPUP):
            if payment is None or payment[0] is not d.source:
                raise WalletError("付费到账缺少同来源现金事件")
            paid_grants[d.batch_id] += d.delta_credit_minor
        flow[d.batch_id] += d.delta_credit_minor
    for bid, (_, cash) in paid_batches.items():
        if paid_grants[bid] != cash.credit_minor_granted:
            raise WalletError(f"付费批次 {bid} 到账与现金权益不一致")
    for bid in sorted(set(b0) | set(b1) | set(flow)):
        lhs = b0.get(bid, 0) + flow.get(bid, 0)
        if lhs != b1.get(bid, 0):
            raise WalletError(f"批次 {bid} 不守恒: {b0.get(bid, 0)} + {flow.get(bid, 0)} ≠ {b1.get(bid, 0)}")
        if b1.get(bid, 0) < 0:
            raise WalletError(f"批次 {bid} 余额为负")
    granted = sum(d.delta_credit_minor for d in deltas
                  if d.delta_credit_minor > 0 and d.source in (CreditSource.SUBSCRIPTION,
                                                               CreditSource.INSTANT_TOPUP))
    cash_credit = sum(e.credit_minor_granted for e in new_cash)
    if granted != cash_credit:
        raise WalletError(f"付费到账 credit {granted} 与现金事件 {cash_credit} 不一致")
