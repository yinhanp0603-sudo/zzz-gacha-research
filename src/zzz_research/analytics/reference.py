"""M-COMBAT 4.5 / M-STATS 11.2：预注册参考轨迹（L2 固定基准面板）。

参考主体 = 锚定主输出 × 投资方案 × 养成策略 × 支援策略，在其出道日起逐期用基准情景环境评估。
方案在评估前由配置确定（投资取 gacha.target_paths 中的主输出路径；支援取“从不”或“该支援出道即加入”），
不按结果重新分组。与玩家活跃、持有和购买独立。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from ..combat.proxy import ProxyCombatModel
from ..contracts import CombatRequest, ResolvedConfig, sha256_hex
from ..simulation import ROLE_SUPPORT, STARTER_ID, World, resolve_world

GEAR_FIXED = "fixed_G1"
GEAR_GROWTH = "growth_from_debut"
SUPPORT_NONE = "none"
REFERENCE_PROTOCOL = "reference_trajectories_v1"
_CIN = re.compile(r"^cinema_(\d+)$")


@dataclass(frozen=True)
class ReferenceSubject:
    reference_subject_id: str
    anchor_unit_id: str
    investment_plan: str
    cinema: int
    signature_weapon: bool
    gear_policy: str
    support_policy: str
    support_unit: Optional[str]
    origin_day: float
    debut_t: int


@dataclass(frozen=True)
class ReferenceObservation:
    subject: ReferenceSubject
    t_idx: int
    period_id: str
    scenario_id: str
    age_days: float
    support_active: Optional[str]
    gear: float
    ci: Optional[float]
    is_full_star: Optional[bool]
    eval_status: str
    shield_damage_share: Optional[float]
    effective_damage: Optional[float]


def dps_investment_plans(cfg: ResolvedConfig) -> dict[str, tuple[int, bool]]:
    """主输出可用的目标路径 → (影画数, 是否专武)。支援目标路径不作为主输出投资方案。"""
    sup = cfg.module("behavior")["support_target"]
    out = {}
    for pid, comps in sorted(cfg.module("gacha")["target_paths"].items()):
        if pid == sup:
            continue
        cin = max([int(m.group(1)) for c in comps if (m := _CIN.match(c))] or [0])
        out[pid] = (cin, "signature_weapon" in comps)
    return out


def build_subjects(cfg: ResolvedConfig, world: World, *, include_starter: bool = False) -> list[ReferenceSubject]:
    plans = dps_investment_plans(cfg)
    supports = [(None, SUPPORT_NONE)] + [(s, f"join_on_debut:{s}") for s in world.supports]
    out = []
    for u in sorted(world.units.values(), key=lambda x: x.unit_id):
        if u.role == ROLE_SUPPORT or u.b_ref is None:
            continue
        if u.unit_id == STARTER_ID and not include_starter:
            continue
        debut_t = u.debut_t or 1
        origin = u.debut_day if u.debut_day is not None else 0.0
        for plan, (cin, sig) in plans.items():
            for gear in (GEAR_FIXED, GEAR_GROWTH):
                for sunit, spol in supports:
                    sid = sha256_hex([REFERENCE_PROTOCOL, u.unit_id, plan, gear, spol])[:20]
                    out.append(ReferenceSubject(sid, u.unit_id, plan, cin, sig, gear, spol, sunit, origin, debut_t))
    return out


def evaluate_trajectories(cfg: ResolvedConfig, *, include_starter: bool = False
                          ) -> tuple[str, World, list[ReferenceObservation]]:
    model = ProxyCombatModel(cfg.module("combat"))
    world = resolve_world(cfg, model)
    subjects = build_subjects(cfg, world, include_starter=include_starter)
    bid = sha256_hex([REFERENCE_PROTOCOL, cfg.config_id, model.params.config_digest,
                      [s.reference_subject_id for s in subjects]])
    debut = {u.unit_id: (u.debut_t or 1) for u in world.units.values()}
    obs = []
    for s in subjects:
        unit = world.units[s.anchor_unit_id]
        for p in world.periods:
            if p.t_idx < s.debut_t:
                continue
            sup = s.support_unit if (s.support_unit and p.t_idx >= debut[s.support_unit]) else None
            g = 1.0 if s.gear_policy == GEAR_FIXED else model.params.gear(p.evaluation_day, s.origin_day)
            res = model.evaluate(CombatRequest(
                team=(s.anchor_unit_id, world.neutral_fixed, sup or world.neutral_flex),
                anchor_unit_id=s.anchor_unit_id,
                investment={"B_ref": unit.b_ref, "pierce_share": unit.pierce_share, "cinema": s.cinema,
                            "signature_weapon": s.signature_weapon},
                actual_g=g, scenario_id=p.scenario.scenario_id, model_version=model.params.model_version))
            obs.append(ReferenceObservation(
                s, p.t_idx, p.period_id, p.scenario.scenario_id, p.evaluation_day - s.origin_day, sup, g,
                res.ci, res.meets_threshold, res.eval_status.value,
                res.damage_breakdown.get("q"), res.damage_breakdown.get("D_window")))
    return bid, world, obs


def write_reference_panel(writer: Any, cfg: ResolvedConfig, bid: str, obs: list[ReferenceObservation]) -> None:
    subjects = {o.subject.reference_subject_id: o.subject for o in obs}
    writer.write_dim("dim_reference_subject", [{
        "reference_subject_id": s.reference_subject_id, "benchmark_id": bid, "anchor_unit_id": s.anchor_unit_id,
        "investment_plan": s.investment_plan, "gear_policy": s.gear_policy, "support_policy": s.support_policy,
        "origin_day": s.origin_day, "budget_description": "预注册固定投资，不含预算约束"} for s in subjects.values()])
    with writer.atomic_buffer():
        for o in obs:
            s = o.subject
            build = {"anchor": s.anchor_unit_id, "cinema": s.cinema, "signature_weapon": s.signature_weapon,
                     "gear": o.gear, "support": o.support_active or "NONE"}
            writer.insert("fact_fixed_benchmark_panel", {
                "benchmark_id": bid, "reference_subject_id": s.reference_subject_id, "period_id": o.period_id,
                "scenario_id": o.scenario_id, "engine_kind": "proxy", "anchor_unit_id": s.anchor_unit_id,
                "team_id": None, "build_hash": sha256_hex(build), "support_id": o.support_active or "NONE",
                "cinema": s.cinema, "has_weapon": int(s.signature_weapon), "disc_mult": o.gear,
                "B_ref": None, "shield_damage_share": o.shield_damage_share, "effective_damage": o.effective_damage,
                "clear_index": o.ci, "is_full_star": None if o.is_full_star is None else int(o.is_full_star),
                "eval_status": o.eval_status, "first_kill_seconds": None, "coverage_status": "proxy_only"})
    writer.flush()
