"""M-GACHA：概率曲线、单抽纯转移、目标路径解释与 DP 成本分布（gacha_v1）。

- 不扣钱包、不修改库存：只返回 PullOutcome 与 AssetDelta 意图。
- 所有曲线参数读取 gacha.json；目标顺序唯一来源是 gacha.json.target_paths。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from ..contracts import (
    AssetDelta,
    AssetKind,
    PityState,
    PoolKind,
    PullOutcome,
    RngType,
    RosterView,
    TargetPlan,
    TargetStage,
    deep_thaw,
    signature_weapon_id,
)

#: 非 UP 的 S 记录为非战斗库存类别（固定类别ID，见 gacha.md 6.2）
OFFRATE_CATEGORY: Mapping[PoolKind, str] = {
    PoolKind.CHARACTER: "OFFRATE_S_CHAR",
    PoolKind.WEAPON: "OFFRATE_S_WEAPON",
}

COMPONENT_BODY = "body"
COMPONENT_SIGNATURE = "signature_weapon"
_CINEMA_RE = re.compile(r"^cinema_([1-9][0-9]*)$")


class GachaError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 6.1 曲线
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolParams:
    pool_kind: PoolKind
    base_s_probability: float
    soft_pity_start: int
    hard_pity: int
    soft_increment: float
    up_given_s: float
    pity_group: str

    def __post_init__(self) -> None:
        for n in ("base_s_probability", "soft_increment", "up_given_s"):
            v = getattr(self, n)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
                raise GachaError(f"{self.pool_kind.value}.{n} 非法: {v!r}")
        for n in ("soft_pity_start", "hard_pity"):
            v = getattr(self, n)
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise GachaError(f"{self.pool_kind.value}.{n} 必须为正整数")
        if self.soft_pity_start > self.hard_pity:
            raise GachaError("soft_pity_start 不能大于 hard_pity")

    def s_probability(self, n: int) -> float:
        """p(n)，n = pity + 1 为本次是连续第几抽。"""
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= self.hard_pity:
            raise GachaError(f"n 必须在 1…{self.hard_pity}，得到 {n!r}")
        if n == self.hard_pity:
            return 1.0
        if n < self.soft_pity_start:
            return float(self.base_s_probability)
        return min(1.0, self.base_s_probability + self.soft_increment * (n - self.soft_pity_start + 1))

    def check_state(self, pity: PityState) -> None:
        if pity.pity_group != self.pity_group:
            raise GachaError(f"pity_group 不匹配: {pity.pity_group} ≠ {self.pity_group}")
        if not 0 <= pity.pulls_since_s < self.hard_pity:
            raise GachaError(f"非法 pity 状态 {pity.pulls_since_s}（硬保底 {self.hard_pity}）")


@dataclass(frozen=True)
class GachaModel:
    model_version: str
    curve_version: str
    pools: Mapping[PoolKind, PoolParams]
    target_paths: Mapping[str, tuple[str, ...]]
    non_s_result: str

    @classmethod
    def from_config(cls, gacha_cfg: Mapping[str, Any]) -> "GachaModel":
        c = deep_thaw(gacha_cfg)
        pools = {}
        for name, p in sorted(c["pools"].items()):
            kind = PoolKind(name)
            pools[kind] = PoolParams(
                pool_kind=kind,
                base_s_probability=p["base_s_probability"],
                soft_pity_start=p["soft_pity_start"],
                hard_pity=p["hard_pity"],
                soft_increment=p["soft_increment"],
                up_given_s=p["up_given_s"],
                pity_group=p["pity_group"],
            )
        if set(pools) != set(PoolKind):
            raise GachaError("gacha.pools 必须同时配置 character 与 weapon")
        paths = {k: tuple(v) for k, v in sorted(c["target_paths"].items())}
        for pid, comps in paths.items():
            for comp in comps:
                component_pool(comp)  # 校验
        return cls(c["model_version"], c["curve_version"], pools, paths, c["non_s_result"])

    def pool(self, kind: PoolKind | str) -> PoolParams:
        return self.pools[PoolKind(kind)]

    def fresh_pity(self, kind: PoolKind | str) -> PityState:
        return PityState(self.pool(kind).pity_group, 0, False)


# ---------------------------------------------------------------------------
# 6.2 单抽纯转移
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Transition:
    is_s: bool
    is_up: bool
    pity_after: PityState


def transition(pool: PoolParams, pity: PityState, u_s: float, u_up: float) -> Transition:
    """按 U < p 判定 S；S 时若已保证则必 UP，否则按 up_given_s 判定。"""
    pool.check_state(pity)
    for name, u in (("u_s", u_s), ("u_up", u_up)):
        if not 0.0 <= u < 1.0:
            raise GachaError(f"{name} 必须在 [0,1)")
    p = pool.s_probability(pity.pulls_since_s + 1)
    if not u_s < p:
        return Transition(False, False, PityState(pity.pity_group, pity.pulls_since_s + 1, pity.guaranteed_up))
    is_up = True if pity.guaranteed_up else (u_up < pool.up_given_s)
    return Transition(True, is_up, PityState(pity.pity_group, 0, not is_up))


def resolve_one_pull(
    model: GachaModel, pool_kind: PoolKind | str, pity: PityState, *,
    u_s: float, u_up: float, pull_id: str, banner_id: str, draw_slot: int,
    up_unit_id: str, day: float,
) -> PullOutcome:
    """单次实际抽取。UP 角色资产ID=unit_id，UP 音擎资产ID=该角色专武ID。"""
    kind = PoolKind(pool_kind)
    tr = transition(model.pool(kind), pity, u_s, u_up)
    deltas: tuple[AssetDelta, ...] = ()
    if tr.is_up:
        if kind is PoolKind.CHARACTER:
            asset_kind, asset_id = AssetKind.CHARACTER, up_unit_id
        else:
            asset_kind, asset_id = AssetKind.WEAPON, signature_weapon_id(up_unit_id)
        deltas = (AssetDelta(f"{pull_id}:asset", asset_kind, asset_id, 1, day),)
        result = asset_id
    elif tr.is_s:
        asset_id = OFFRATE_CATEGORY[kind]
        deltas = (AssetDelta(f"{pull_id}:asset", AssetKind.CATEGORY, asset_id, 1, day),)
        result = asset_id
    else:
        result = model.non_s_result
    return PullOutcome(
        pull_id=pull_id, banner_id=banner_id, pool_kind=kind, draw_slot=draw_slot,
        is_s=tr.is_s, is_up=tr.is_up, result_asset_id=result,
        pity_before=pity, pity_after=tr.pity_after, asset_deltas=deltas,
    )


def draw_with_rng(
    model: GachaModel, rng: Any, *, player_id: int, t_idx: int, banner_id: str,
    pool_kind: PoolKind | str, draw_slot: int, pity: PityState, up_unit_id: str, day: float,
    pull_id: Optional[str] = None,
) -> PullOutcome:
    """读取公共随机数的两个独立流（S / UP）。即使保证命中也不移动槽位。"""
    kind = PoolKind(pool_kind)
    pair = getattr(rng, "draw_pair", None)
    if pair is not None:  # 同一事件键，逐位相同，只是前缀哈希复用
        u_s, u_up = pair(player_id, t_idx, banner_id, kind, draw_slot)
    else:
        u_s = rng.draw_u(player_id, t_idx, banner_id, kind, draw_slot, RngType.S)
        u_up = rng.draw_u(player_id, t_idx, banner_id, kind, draw_slot, RngType.UP)
    pid = pull_id or f"p{player_id}:t{t_idx}:{banner_id}:{kind.value}:{draw_slot}"
    return resolve_one_pull(model, kind, pity, u_s=u_s, u_up=u_up, pull_id=pid,
                            banner_id=banner_id, draw_slot=draw_slot, up_unit_id=up_unit_id, day=day)


# ---------------------------------------------------------------------------
# 6.3 目标路径
# ---------------------------------------------------------------------------


def component_pool(component: str) -> PoolKind:
    if component == COMPONENT_BODY or _CINEMA_RE.match(component):
        return PoolKind.CHARACTER
    if component == COMPONENT_SIGNATURE:
        return PoolKind.WEAPON
    raise GachaError(f"未知目标部件 {component!r}")


def build_target_plan(model: GachaModel, path_id: str, unit_id: str,
                      banners: Mapping[PoolKind | str, str]) -> TargetPlan:
    if path_id not in model.target_paths:
        raise GachaError(f"未知目标路径 {path_id!r}")
    b = {PoolKind(k): v for k, v in banners.items()}
    stages = []
    for comp in model.target_paths[path_id]:
        kind = component_pool(comp)
        if kind not in b:
            raise GachaError(f"目标路径 {path_id} 需要 {kind.value} 卡池 banner")
        stages.append(TargetStage(f"{unit_id}:{comp}", comp, kind, b[kind]))
    return TargetPlan(f"{unit_id}:{path_id}", unit_id, tuple(stages))


def stage_done(stage: TargetStage, unit_id: str, roster: RosterView) -> bool:
    ch = roster.character(unit_id)
    if stage.component == COMPONENT_BODY:
        return ch is not None
    if stage.component == COMPONENT_SIGNATURE:
        return roster.weapon_count(signature_weapon_id(unit_id)) >= 1
    m = _CINEMA_RE.match(stage.component)
    if m:
        return ch is not None and ch.cinema >= int(m.group(1))
    raise GachaError(f"未知目标部件 {stage.component!r}")


def pending_stages(plan: TargetPlan, roster: RosterView) -> list[TargetStage]:
    """按固定顺序跳过已持有部件；不重复支付已完成目标。"""
    return [s for s in plan.stages if not stage_done(s, plan.unit_id, roster)]


def next_stage(plan: TargetPlan, roster: RosterView) -> Optional[TargetStage]:
    p = pending_stages(plan, roster)
    return p[0] if p else None


# ---------------------------------------------------------------------------
# DP：成本分布、均值与容量上界
# ---------------------------------------------------------------------------


def next_s_distribution(pool: PoolParams, pulls_since_s: int = 0) -> list[float]:
    """dist[j-1] = P(下一个 S 恰在第 j 抽)，j=1…H−pity。"""
    pool.check_state(PityState(pool.pity_group, pulls_since_s))
    out, survive = [], 1.0
    for n in range(pulls_since_s + 1, pool.hard_pity + 1):
        p = pool.s_probability(n)
        out.append(survive * p)
        survive *= 1.0 - p
    return out


def _convolve(a: Sequence[float], b: Sequence[float]) -> list[float]:
    out = [0.0] * (len(a) + len(b))
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b):
                out[i + j + 1] += x * y  # 索引 i 表示 i+1 抽
    return out[: len(a) + len(b)]


def up_cost_distribution(pool: PoolParams, pity: Optional[PityState] = None) -> list[float]:
    """dist[j-1] = P(获得下一个 UP 恰需 j 抽)。"""
    pity = pity or PityState(pool.pity_group)
    first = next_s_distribution(pool, pity.pulls_since_s)
    if pity.guaranteed_up:
        return first
    fresh = next_s_distribution(pool, 0)
    out = [x * pool.up_given_s for x in first] + [0.0] * len(fresh)
    for k, v in enumerate(_convolve(first, fresh)):
        out[k] += v * (1.0 - pool.up_given_s)
    while out and out[-1] == 0.0:
        out.pop()
    return out


def mean_of(dist: Sequence[float]) -> float:
    return sum((i + 1) * p for i, p in enumerate(dist))


def expected_up_pulls(pool: PoolParams, pity: Optional[PityState] = None) -> float:
    return mean_of(up_cost_distribution(pool, pity))


def worst_case_up_pulls(pool: PoolParams, pity: Optional[PityState] = None) -> int:
    pity = pity or PityState(pool.pity_group)
    pool.check_state(pity)
    first = pool.hard_pity - pity.pulls_since_s
    return first if pity.guaranteed_up else first + pool.hard_pity


def _stage_pools(model: GachaModel, stages: Sequence[TargetStage],
                 pities: Optional[Mapping[str, PityState]]) -> list[tuple[PoolParams, PityState]]:
    state = {p.pity_group: (pities or {}).get(p.pity_group, PityState(p.pity_group))
             for p in model.pools.values()}
    out = []
    for s in stages:
        pool = model.pool(s.pool_kind)
        out.append((pool, state[pool.pity_group]))
        state[pool.pity_group] = PityState(pool.pity_group)  # 获得 UP 后归零且无保证
    return out


def expected_plan_pulls(model: GachaModel, plan: TargetPlan, roster: RosterView,
                        pities: Optional[Mapping[str, PityState]] = None) -> float:
    return sum(expected_up_pulls(pool, st)
               for pool, st in _stage_pools(model, pending_stages(plan, roster), pities))


def worst_case_plan_pulls(model: GachaModel, plan: TargetPlan, roster: RosterView,
                          pities: Optional[Mapping[str, PityState]] = None) -> int:
    """容量上界：剩余目标在当前保底状态下的最坏抽数。"""
    return sum(worst_case_up_pulls(pool, st)
               for pool, st in _stage_pools(model, pending_stages(plan, roster), pities))
