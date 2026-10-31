"""M-PLANNER：确定性 DP 规划（planner_dp_v1）。

- 只调用 gacha 的概率内核与调用方给出的纯 CI 评估；不读取实际 CRN、不扣钱包、不修改库存。
- 每池首次访问未完成阶段使用当前真实保底状态；获得 UP 后同池回到 pity=0/无保证。
- 前缀成本 C_j 在预算 B 处截断卷积，概率为精确值（无近似）。
- 单阶段“下一 UP 所需抽数”分布按概率曲线在首次使用时一次性预计算为 NumPy 查找表
  (pity, guaranteed) → pdf，进程内按曲线参数共享；玩家循环内只做查表与截断卷积。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from typing import Callable, Mapping, Optional, Sequence

from ..agents.roster import apply_asset_delta
from ..contracts import (
    AssetDelta,
    AssetKind,
    BudgetView,
    EvalStatus,
    Forecast,
    PityState,
    PoolKind,
    RosterView,
    StageForecast,
    TargetPlan,
    TargetStage,
    signature_weapon_id,
)
from .gacha import (
    COMPONENT_SIGNATURE,
    GachaModel,
    PoolParams,
    pending_stages,
    worst_case_up_pulls,
)
from ..versions import PLANNER_VERSION

#: CI 评估回调：库存 → (状态, CI 或 None)
CIEvaluator = Callable[[RosterView], tuple[EvalStatus, Optional[float]]]


class PlannerError(ValueError):
    pass


@dataclass(frozen=True)
class PlanForecast:
    """一次目标规划的全部确定性输出。"""

    plan: TargetPlan
    stages: tuple[TargetStage, ...]          # 待完成阶段（已持有部件已跳过）
    reach_probability: tuple[float, ...]     # P(C_j ≤ B)，j = 1…L
    k_probability: tuple[float, ...]         # P(K = j)，j = 0…L
    ci_k: tuple[Optional[float], ...]        # CI_K，K = 0…L
    eval_status: EvalStatus
    free_pulls: int                          # F
    paid_pulls: int                          # P
    worst_case_pulls: int                    # H
    budget_pulls: int                        # B = min(F+P, H)
    expected_spent: float                    # E[N_spent]
    expected_topup: float                    # E[N_topup]
    planner_version: str = PLANNER_VERSION

    @property
    def target_owned(self) -> bool:
        return not self.stages

    def to_forecast(self) -> Forecast:
        rows = []
        for j, st in enumerate(self.stages, start=1):
            rows.append(StageForecast(
                stage_id=st.stage_id,
                reach_probability=min(1.0, max(0.0, self.reach_probability[j - 1])),
                ci=self.ci_k[j],
                expected_pulls=self.expected_spent if j == len(self.stages) else None,
                expected_direct_pulls=self.expected_topup if j == len(self.stages) else None,
            ))
        return Forecast(self.plan, tuple(rows), self.planner_version)


def stage_asset_delta(stage: TargetStage, unit_id: str, day: float) -> AssetDelta:
    """阶段 → 潜在资产（与实际入库共用资产解释规则）。"""
    if stage.pool_kind is PoolKind.WEAPON:
        if stage.component != COMPONENT_SIGNATURE:
            raise PlannerError(f"未知音擎阶段 {stage.component}")
        return AssetDelta(f"plan:{stage.stage_id}", AssetKind.WEAPON, signature_weapon_id(unit_id), 1, day)
    return AssetDelta(f"plan:{stage.stage_id}", AssetKind.CHARACTER, unit_id, 1, day)


#: 进程级查找表：曲线参数 → {(pity, guaranteed): pdf}，pdf[i] = P(下一个 UP 恰需 i+1 抽)
_UP_TABLES: dict[tuple, dict[tuple[int, bool], np.ndarray]] = {}


def _pool_key(pool: PoolParams) -> tuple:
    return (pool.base_s_probability, pool.soft_pity_start, pool.hard_pity, pool.soft_increment, pool.up_given_s)


def up_cost_table(pool: PoolParams) -> dict[tuple[int, bool], np.ndarray]:
    """预计算该池全部 (pity, guaranteed) 状态的下一 UP 抽数分布。

    next_s 分布用顺序 cumprod，与逐抽连乘次序一致；非保证状态 = u·首个S + (1−u)·(首个S ⊛ 新保底S)。
    """
    key = _pool_key(pool)
    table = _UP_TABLES.get(key)
    if table is not None:
        return table
    H = pool.hard_pity
    p = np.array([pool.s_probability(n) for n in range(1, H + 1)], dtype=float)
    first = {}
    for k in range(H):
        pk = p[k:]
        surv = np.concatenate(([1.0], np.cumprod(1.0 - pk)[:-1]))
        first[k] = pk * surv
    fresh = first[0]
    u = pool.up_given_s
    table = {}
    for k in range(H):
        f = first[k]
        table[(k, True)] = f
        conv = np.convolve(f, fresh)                       # 下标 m ↔ 抽数 m+2
        out = np.zeros(len(f) + len(fresh))
        out[: len(f)] += u * f
        out[1: 1 + len(conv)] += (1.0 - u) * conv
        table[(k, False)] = np.trim_zeros(out, "b")
    for arr in table.values():
        arr.setflags(write=False)
    _UP_TABLES[key] = table
    return table


def _truncated_convolve(prefix: np.ndarray, stage: np.ndarray, cap: int) -> np.ndarray:
    """prefix[c] = P(C=c)；stage[i] = P(阶段成本=i+1)。结果截断到成本 ≤ cap。"""
    full = np.convolve(prefix, stage[:cap])               # 下标 m ↔ 成本 m+1
    out = np.zeros(cap + 1)
    n = min(cap, len(full))
    out[1: n + 1] = full[:n]
    return out


class DPPlanner:
    """forecast_target 的实现；缓存按 (池状态, 目标阶段池序列, 预算) 的概率部分。"""

    planner_version = PLANNER_VERSION

    def __init__(self, gacha: GachaModel):
        self.gacha = gacha
        self._tables = {kind: up_cost_table(pool) for kind, pool in gacha.pools.items()}  # 初始化时预计算

    # -- 概率部分 -----------------------------------------------------------

    def _up_dist(self, kind: PoolKind, pity: PityState) -> np.ndarray:
        return self._tables[kind][(pity.pulls_since_s, pity.guaranteed_up)]

    def stage_states(self, stages: Sequence[TargetStage],
                     pities: Mapping[str, PityState]) -> list[tuple[PoolKind, PityState]]:
        state = {}
        for kind, pool in self.gacha.pools.items():
            state[kind] = pities.get(pool.pity_group, PityState(pool.pity_group))
            if state[kind].pity_group != pool.pity_group:
                raise PlannerError("pity_group 与卡池不匹配")
        out = []
        for st in stages:
            out.append((st.pool_kind, state[st.pool_kind]))
            state[st.pool_kind] = PityState(self.gacha.pool(st.pool_kind).pity_group)  # 获得UP后重置
        return out

    def prefix_distributions(self, stages: Sequence[TargetStage], pities: Mapping[str, PityState],
                             cap: int) -> list[np.ndarray]:
        """返回 [pmf(C_0), pmf(C_1), …, pmf(C_L)]，各截断到成本 ≤ cap。"""
        if cap < 0:
            raise PlannerError("预算必须非负")
        start = np.zeros(cap + 1)
        start[0] = 1.0
        pmfs = [start]
        for kind, pity in self.stage_states(stages, pities):
            pmfs.append(_truncated_convolve(pmfs[-1], self._up_dist(kind, pity), cap))
        return pmfs

    def worst_case(self, stages: Sequence[TargetStage], pities: Mapping[str, PityState]) -> int:
        return sum(worst_case_up_pulls(self.gacha.pool(k), p) for k, p in self.stage_states(stages, pities))

    # -- 主接口 -------------------------------------------------------------

    def forecast_target(self, plan: TargetPlan, roster: RosterView, budget: BudgetView,
                        pity: Sequence[PityState] | Mapping[str, PityState], *,
                        ci_evaluator: Optional[CIEvaluator] = None, day: float = 0.0) -> PlanForecast:
        pities = dict(pity) if isinstance(pity, Mapping) else {p.pity_group: p for p in pity}
        stages = tuple(pending_stages(plan, roster))
        H = self.worst_case(stages, pities)
        F, P = budget.free_pulls, budget.paid_pulls
        B = min(F + P, H)
        L = len(stages)

        # CI_K：当前日、实际既有库存 + 前 K 个阶段潜在资产（新角色 G 由评估方按获取日=今日处理）
        status, ci_k = EvalStatus.OK, []
        if ci_evaluator is not None:
            r = roster
            for k in range(L + 1):
                if k > 0:
                    r = apply_asset_delta(r, stage_asset_delta(stages[k - 1], plan.unit_id, day))
                st, ci = ci_evaluator(r)
                if not st.ci_defined:
                    status = EvalStatus.UNSUPPORTED if st is not EvalStatus.ERROR else EvalStatus.ERROR
                    ci = None
                ci_k.append(ci)
        else:
            ci_k = [None] * (L + 1)
            status = EvalStatus.MISSING_MODEL

        if L == 0 or B == 0:
            reach = tuple(0.0 for _ in stages)
            k_prob = tuple([1.0] + [0.0] * L)
            return PlanForecast(plan, stages, reach, k_prob, tuple(ci_k), status, F, P, H, B, 0.0, 0.0)

        pmfs = self.prefix_distributions(stages, pities, B)
        reach = tuple(float(pmfs[j].sum()) for j in range(1, L + 1))       # P(C_j ≤ B)
        cdf_prev = [1.0] + list(reach)
        k_prob = tuple(cdf_prev[j] - (cdf_prev[j + 1] if j < L else 0.0) for j in range(L + 1))
        # E[N_spent] = Σ_{n=0}^{B-1} P(C_L > n)；E[N_topup] = Σ_{n=F}^{B-1} P(C_L > n)
        survive = np.maximum(0.0, 1.0 - np.cumsum(pmfs[L][:B]))
        e_spent = float(survive.sum())
        e_topup = float(survive[F:].sum()) if P > 0 else 0.0
        return PlanForecast(plan, stages, reach, k_prob, tuple(ci_k), status, F, P, H, B, e_spent, e_topup)
