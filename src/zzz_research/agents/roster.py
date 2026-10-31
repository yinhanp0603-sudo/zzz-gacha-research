"""M-ROSTER：资产库存状态转移（roster 权威）。

不拥有概率、价格或战斗公式；只应用 AssetDelta、保存 combat 算好的装备值、
枚举可行队伍与冻结。初始资产读取 scenarios.json.initial_assets。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..contracts import (
    MAX_CINEMA,
    AssetDelta,
    AssetKind,
    CategoryCount,
    CharacterHolding,
    RosterState,
    RosterView,
    WeaponHolding,
    deep_thaw,
    signature_weapon_id,
)

#: 影画溢出（超过 MAX_CINEMA 的重复角色）计入的非战斗类别
CINEMA_OVERFLOW_CATEGORY = "CHAR_DUPLICATE_OVERFLOW"


class RosterError(ValueError):
    pass


class DuplicateAssetEvent(RosterError):
    """同一 asset_event_id 被重复应用。"""


class FrozenRosterError(RosterError):
    pass


def _refresh_signature_flags(chars: Iterable[CharacterHolding],
                             weapons: Mapping[str, WeaponHolding]) -> list[CharacterHolding]:
    out = []
    for c in chars:
        has = weapons.get(signature_weapon_id(c.unit_id))
        out.append(replace(c, equipped_signature=has is not None and has.count >= 1))
    return out


def apply_asset_delta(state: RosterState, delta: AssetDelta) -> RosterState:
    """应用初始化或已实际获得的资产，返回新状态；同一事件只能应用一次。"""
    if state.frozen:
        raise FrozenRosterError("已冻结的库存不再获取资产")
    if delta.event_id in state.applied_event_ids:
        raise DuplicateAssetEvent(delta.event_id)
    chars = {c.unit_id: c for c in state.characters}
    weapons = {w.weapon_id: w for w in state.weapons}
    cats = {k.category_id: k.count for k in state.categories}
    q = delta.quantity

    if delta.asset_kind is AssetKind.CHARACTER:
        cur = chars.get(delta.asset_id)
        if cur is None and q > 0:
            cur = CharacterHolding(delta.asset_id, 0, float(delta.day))
            q -= 1
        if cur is not None:
            room = MAX_CINEMA - cur.cinema
            add = min(room, q)
            # 获取影画不重置首次获取日
            chars[delta.asset_id] = replace(cur, cinema=cur.cinema + add)
            if q > add:
                cats[CINEMA_OVERFLOW_CATEGORY] = cats.get(CINEMA_OVERFLOW_CATEGORY, 0) + (q - add)
    elif delta.asset_kind is AssetKind.WEAPON:
        cur_w = weapons.get(delta.asset_id)
        if cur_w is None:
            if q > 0:
                weapons[delta.asset_id] = WeaponHolding(delta.asset_id, q, float(delta.day))
        else:
            weapons[delta.asset_id] = replace(cur_w, count=cur_w.count + q)
    elif delta.asset_kind is AssetKind.CATEGORY:
        cats[delta.asset_id] = cats.get(delta.asset_id, 0) + q
    else:  # pragma: no cover
        raise RosterError(f"未知资产类型 {delta.asset_kind}")

    return replace(
        state,
        characters=tuple(_refresh_signature_flags(chars.values(), weapons)),
        weapons=tuple(weapons.values()),
        categories=tuple(CategoryCount(k, v) for k, v in cats.items()),
        applied_event_ids=state.applied_event_ids | {delta.event_id},
    )


def apply_asset_deltas(state: RosterState, deltas: Iterable[AssetDelta]) -> RosterState:
    for d in deltas:
        state = apply_asset_delta(state, d)
    return state


def snapshot(state: RosterState) -> RosterView:
    """只读视图；状态本身不可变，直接返回。"""
    return state


def update_disc_values(state: RosterState, values: Mapping[str, float]) -> RosterState:
    """保存 combat 按当前日期计算的装备倍率；不改变首次获取日。冻结后不再养成。"""
    if state.frozen:
        raise FrozenRosterError("已冻结的库存不再养成")
    held = {c.unit_id for c in state.characters}
    unknown = sorted(set(values) - held)
    if unknown:
        raise RosterError(f"未持有的角色不能更新装备值: {unknown}")
    return replace(state, characters=tuple(
        replace(c, gear_value=float(values[c.unit_id])) if c.unit_id in values else c
        for c in state.characters))


def freeze(state: RosterState) -> RosterState:
    """退出期最后 PostEval 后冻结实际库存。"""
    return replace(state, frozen=True,
                   characters=tuple(replace(c, frozen=True) for c in state.characters))


# ---------------------------------------------------------------------------
# 初始库存
# ---------------------------------------------------------------------------


def initial_asset_deltas(scenarios_cfg: Mapping[str, Any], *, player_id: int,
                         starter_unit_id: str = "starter_dps") -> list[AssetDelta]:
    """starter 主输出与中性角色作为显式库存资产初始化（scenarios.initial_assets）。"""
    ia = deep_thaw(scenarios_cfg["initial_assets"])
    starter = ia[starter_unit_id]
    day = starter["acquired_day"]
    cinema = starter["cinema"]
    out = [AssetDelta(f"init:p{player_id}:{starter_unit_id}", AssetKind.CHARACTER, starter_unit_id,
                      1 + cinema, day, is_initial=True)]
    if starter["signature_weapon"]:
        out.append(AssetDelta(f"init:p{player_id}:{starter_unit_id}:sig", AssetKind.WEAPON,
                              signature_weapon_id(starter_unit_id), 1, day, is_initial=True))
    for u in sorted(ia["neutral_units"]):
        out.append(AssetDelta(f"init:p{player_id}:{u}", AssetKind.CHARACTER, u, 1, day, is_initial=True))
    return out


def initial_roster(scenarios_cfg: Mapping[str, Any], *, player_id: int) -> RosterState:
    return apply_asset_deltas(RosterState(), initial_asset_deltas(scenarios_cfg, player_id=player_id))


# ---------------------------------------------------------------------------
# 队伍枚举
# ---------------------------------------------------------------------------


def enumerate_feasible_teams(
    state: RosterView, *, anchors: Sequence[str], neutral_fixed: str,
    neutral_flex: str, supports: Sequence[str], anchor: Optional[str] = None,
) -> list[tuple[str, str, str]]:
    """锚定主输出 + 固定中性 + 可替换位（中性或一个支援）。只列库存可用者，不计算伤害。

    anchors 为可作锚定的主输出 ID（由调用方从配置角色类型给出）；
    主输出不能被另一个主输出“带过关”，因此可替换位不放主输出。
    supports 可含混合型（主C兼体系辅助）：它同时出现在 anchors 中，作锚定时不进入自己的可替换位。
    """
    held = {c.unit_id for c in state.characters}
    if neutral_fixed not in held:
        return []
    flex_options = ([neutral_flex] if neutral_flex in held else []) + \
        sorted(s for s in supports if s in held)
    cand = [a for a in sorted(set(anchors)) if a in held]
    if anchor is not None:
        cand = [a for a in cand if a == anchor]
    teams = []
    for a in cand:
        for f in flex_options:
            if len({a, neutral_fixed, f}) == 3:
                teams.append((a, neutral_fixed, f))
    return sorted(teams)
