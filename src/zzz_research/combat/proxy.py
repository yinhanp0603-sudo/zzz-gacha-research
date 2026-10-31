"""M-COMBAT：参考 ZSim 防御公式的战斗代理与机制匹配情景。

全部数值读取 combat.json 的只读切片；本文件只实现公式与有效域检查。
有效域外或非有限输入直接抛 CombatInputError，不在公式内部静默修复。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from ..contracts import (
    CombatRequest,
    CombatResult,
    EvalStatus,
    canonical_json,
    deep_thaw,
    sha256_hex,
)

ENGINE_KIND = "proxy"


class CombatInputError(ValueError):
    """参数越出有效域或引用缺失。"""


# ---------------------------------------------------------------------------
# 有效域检查
# ---------------------------------------------------------------------------


def _finite(name: str, x: Any) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise CombatInputError(f"{name} 必须是有限数值，得到 {x!r}")
    return float(x)


def _positive(name: str, x: Any) -> float:
    v = _finite(name, x)
    if v <= 0:
        raise CombatInputError(f"{name} 必须 > 0，得到 {x!r}")
    return v


def _nonneg(name: str, x: Any) -> float:
    v = _finite(name, x)
    if v < 0:
        raise CombatInputError(f"{name} 必须 ≥ 0，得到 {x!r}")
    return v


def _unit(name: str, x: Any) -> float:
    v = _finite(name, x)
    if not 0.0 <= v <= 1.0:
        raise CombatInputError(f"{name} 必须在 [0,1]，得到 {x!r}")
    return v


# ---------------------------------------------------------------------------
# 4.1 防御与相对输出
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DefenseModifiers:
    """r=百分比减防, f=固定减防, p=穿透率, z=固定穿透。"""

    ratio_reduction: float = 0.0
    flat_reduction: float = 0.0
    pen_ratio: float = 0.0
    pen_flat: float = 0.0

    def __post_init__(self) -> None:
        _unit("defense_reduction.ratio", self.ratio_reduction)
        _nonneg("defense_reduction.flat", self.flat_reduction)
        _unit("penetration.ratio", self.pen_ratio)
        _nonneg("penetration.flat", self.pen_flat)

    @classmethod
    def from_config(cls, combat_cfg: Mapping[str, Any]) -> "DefenseModifiers":
        pen = combat_cfg["default_penetration"]
        red = combat_cfg["default_defense_reduction"]
        return cls(red["ratio"], red["flat"], pen["ratio"], pen["flat"])


def effective_defense(base_defense: float, shield_bonus: float, shielded: int,
                      mods: DefenseModifiers) -> float:
    """D_eff_s = max{0, [D(1+b·s)(1−r) − f](1−p) − z}。"""
    d = _nonneg("base_defense", base_defense)
    b = _nonneg("shield.defense_bonus", shield_bonus)
    if shielded not in (0, 1):
        raise CombatInputError("shielded 必须为 0 或 1")
    inner = d * (1.0 + b * shielded) * (1.0 - mods.ratio_reduction) - mods.flat_reduction
    return max(0.0, inner * (1.0 - mods.pen_ratio) - mods.pen_flat)


def defense_multiplier(level_coefficient: float, defense_eff: float) -> float:
    """m_s = K / (K + D_eff_s)。"""
    k = _positive("attacker_level_coefficient", level_coefficient)
    return k / (k + _nonneg("defense_eff", defense_eff))


def relative_output(m_s: float, m_ref: float, pierce_share: float) -> float:
    """Q_s = (1−v)·m_s/m_ref + v。"""
    v = _unit("pierce_share", pierce_share)
    return (1.0 - v) * _nonneg("m_s", m_s) / _positive("m_ref", m_ref) + v


# ---------------------------------------------------------------------------
# 4.2 秽盾对齐
# ---------------------------------------------------------------------------


def shield_damage_share(theta: float, kappa: float) -> float:
    """q = κθ / (1 − θ + κθ)；θ=0→0，θ=1→1。"""
    t = _unit("theta", theta)
    k = _positive("kappa", kappa)
    denom = 1.0 - t + k * t
    return 0.0 if t == 0.0 else (k * t) / denom


# ---------------------------------------------------------------------------
# 4.3 养成、影画、专武、支援
# ---------------------------------------------------------------------------


def gear_multiplier(day: float, acquired_day: float, max_bonus: float,
                    time_constant_days: float) -> float:
    """G_u(d) = 1 + g_max·{1 − exp[−max(0, d − d_acq)/τ_G]}。"""
    g = _nonneg("gear_growth.max_bonus", max_bonus)
    tau = _positive("gear_growth.time_constant_days", time_constant_days)
    age = max(0.0, _finite("day", day) - _finite("acquired_day", acquired_day))
    return 1.0 + g * (1.0 - math.exp(-age / tau))


def cinema_multiplier(cinema: int, multipliers: Sequence[float]) -> float:
    if isinstance(cinema, bool) or not isinstance(cinema, int) or cinema < 0:
        raise CombatInputError(f"cinema 必须是非负整数，得到 {cinema!r}")
    if cinema >= len(multipliers):
        raise CombatInputError(f"cinema={cinema} 超出配置支持的影画档位 0…{len(multipliers) - 1}")
    return _positive(f"cinema_multipliers[{cinema}]", multipliers[cinema])


def support_factor(gain: float, gain_scale: float) -> float:
    """support_factor = 1 + a·g（a 作用于增益本身）。"""
    return 1.0 + _nonneg("support_gain_scale", gain_scale) * _nonneg("support_gain", gain)


# ---------------------------------------------------------------------------
# 模型参数与场景
# ---------------------------------------------------------------------------


def _research_params(value: Optional[Mapping[str, Any]], supports: Mapping[str, Any]
                     ) -> Optional[Mapping[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CombatInputError("research_v2 必须为对象")
    required = {"mechanism_strength", "mechanisms", "unit_profiles", "support_pairs"}
    if set(value) - {"co_output"} != required:
        raise CombatInputError(f"research_v2 字段必须为 {sorted(required)}，可选 co_output")
    _nonneg("research_v2.mechanism_strength", value["mechanism_strength"])
    mechanisms = value["mechanisms"]
    if not mechanisms or set(mechanisms) - {"defense", "window", "barrier"}:
        raise CombatInputError("research_v2.mechanisms 必须登记已支持的机制")
    for kind, profile in mechanisms.items():
        if set(profile) != {"penalty"}:
            raise CombatInputError(f"机制 {kind} 仅接受 penalty")
        _nonneg(f"mechanisms.{kind}.penalty", profile["penalty"])
    units = value["unit_profiles"]
    for unit, profile in units.items():
        if set(profile) != {"affinities"} or set(profile["affinities"]) != set(mechanisms):
            raise CombatInputError(f"主核 {unit} 需要为每个登记机制指定 affinities")
        for kind, affinity in profile["affinities"].items():
            _unit(f"unit_profiles.{unit}.{kind}", affinity)
    for anchor, pairs in value["support_pairs"].items():
        if anchor not in units:
            raise CombatInputError(f"专拐配对的主核 {anchor} 未登记")
        for support, profile in pairs.items():
            if support not in supports:
                raise CombatInputError(f"专拐 {support} 未登记为支援资产")
            if set(profile) != {"net_gain", "match_lift"}:
                raise CombatInputError(f"专拐 {anchor}/{support} 需要 net_gain 与 match_lift")
            _nonneg(f"support_pairs.{anchor}.{support}.net_gain", profile["net_gain"])
            if set(profile["match_lift"]) - set(mechanisms):
                raise CombatInputError(f"专拐 {anchor}/{support} 引用了未登记机制")
            for kind, lift in profile["match_lift"].items():
                _unit(f"support_pairs.{anchor}.{support}.{kind}", lift)
    for unit, share in value.get("co_output", {}).items():
        if unit not in units or unit not in supports:
            raise CombatInputError(f"副输出 {unit} 必须同时登记为主核与支援资产")
        _unit(f"co_output.{unit}", share)
    return value


@dataclass(frozen=True)
class ProxyParams:
    model_version: str
    level_coefficient: float
    reference_defense: float
    hp_index: float
    shield_defense_bonus: float
    shield_damage_reduction: float
    alignment_values: tuple[float, ...]
    default_alignment: float
    gear_max_bonus: float
    gear_time_constant_days: float
    cinema_multipliers: tuple[float, ...]
    signature_weapon_multiplier: float
    support_gains: Mapping[str, float]
    default_gain_scale: float
    modifiers: DefenseModifiers
    config_digest: str
    research_v2: Optional[Mapping[str, Any]] = None

    @classmethod
    def from_config(cls, combat_cfg: Mapping[str, Any]) -> "ProxyParams":
        c = deep_thaw(combat_cfg)
        ref, sh, inv, sup, gg = c["reference"], c["shield"], c["investment"], c["support"], c["gear_growth"]
        aligns = tuple(_positive("shield.alignment_values", k) for k in sh["alignment_values"])
        if not aligns:
            raise CombatInputError("shield.alignment_values 不能为空")
        p = cls(
            model_version=c["model_version"],
            level_coefficient=_positive("reference.attacker_level_coefficient",
                                        ref["attacker_level_coefficient"]),
            reference_defense=_nonneg("reference.enemy_defense", ref["enemy_defense"]),
            hp_index=_positive("reference.hp_index", ref["hp_index"]),
            shield_defense_bonus=_nonneg("shield.defense_bonus", sh["defense_bonus"]),
            shield_damage_reduction=_unit("shield.damage_reduction", sh["damage_reduction"]),
            alignment_values=aligns,
            default_alignment=_positive("shield.default_alignment", sh["default_alignment"]),
            gear_max_bonus=_nonneg("gear_growth.max_bonus", gg["max_bonus"]),
            gear_time_constant_days=_positive("gear_growth.time_constant_days",
                                              gg["time_constant_days"]),
            cinema_multipliers=tuple(_positive("cinema_multipliers", m)
                                     for m in inv["cinema_multipliers"]),
            signature_weapon_multiplier=_positive("signature_weapon_multiplier",
                                                  inv["signature_weapon_multiplier"]),
            support_gains={k: _nonneg(f"support.gains.{k}", v) for k, v in sorted(sup["gains"].items())},
            default_gain_scale=_nonneg("support.default_gain_scale", sup["default_gain_scale"]),
            modifiers=DefenseModifiers.from_config(c),
            config_digest=sha256_hex(c),
            research_v2=_research_params(c.get("research_v2"), sup["gains"]),
        )
        if p.default_alignment not in p.alignment_values:
            raise CombatInputError("shield.default_alignment 必须是 alignment_values 之一")
        return p

    @property
    def reference_multiplier(self) -> float:
        """m_ref：参考无盾敌人、默认减防/穿透下的防御倍率。"""
        d0 = effective_defense(self.reference_defense, self.shield_defense_bonus, 0, self.modifiers)
        return defense_multiplier(self.level_coefficient, d0)

    def gear(self, day: float, acquired_day: float) -> float:
        return gear_multiplier(day, acquired_day, self.gear_max_bonus, self.gear_time_constant_days)


def normal_shield_retention(params: ProxyParams, pierce_share: float = 0.0) -> float:
    """盾内相对无盾的合并保留率 = Q_1(1−d_shield)/Q_0（派生校验量，不作为输入再乘一次）。"""
    d0 = effective_defense(params.reference_defense, params.shield_defense_bonus, 0, params.modifiers)
    d1 = effective_defense(params.reference_defense, params.shield_defense_bonus, 1, params.modifiers)
    m_ref = params.reference_multiplier
    q0 = relative_output(defense_multiplier(params.level_coefficient, d0), m_ref, pierce_share)
    q1 = relative_output(defense_multiplier(params.level_coefficient, d1), m_ref, pierce_share)
    return q1 * (1.0 - params.shield_damage_reduction) / q0


@dataclass(frozen=True)
class CombatScenario:
    """一次评估的环境：HP、盾覆盖 θ、可攻击窗口 uptime、对齐 κ、支援缩放 a。"""

    scenario_id: str
    hp: float
    theta: float
    uptime: float
    kappa: float
    support_gain_scale: float
    base_defense: Optional[float] = None  # None = 参考敌人防御
    modifiers: Optional[DefenseModifiers] = None
    mechanism_type: Optional[str] = None
    mechanism_strength: Optional[float] = None

    def __post_init__(self) -> None:
        _positive("hp", self.hp)
        _unit("theta", self.theta)
        _unit("uptime", self.uptime)
        _positive("kappa", self.kappa)
        _nonneg("support_gain_scale", self.support_gain_scale)
        if self.base_defense is not None:
            _nonneg("base_defense", self.base_defense)
        if self.mechanism_type is not None and self.mechanism_type not in ("defense", "window", "barrier"):
            raise CombatInputError(f"未知机制类型 {self.mechanism_type!r}")
        if self.mechanism_strength is not None:
            _nonneg("mechanism_strength", self.mechanism_strength)


def reference_scenario(params: ProxyParams, scenario_id: str, *, theta: float, uptime: float,
                       hp: Optional[float] = None, kappa: Optional[float] = None,
                       support_gain_scale: Optional[float] = None,
                       mechanism_type: Optional[str] = None,
                       mechanism_strength: Optional[float] = None) -> CombatScenario:
    """以 combat.json 参考值（HP 指数、默认对齐、默认支援缩放）填充未给出的字段。"""
    return CombatScenario(
        scenario_id=scenario_id,
        hp=params.hp_index if hp is None else hp,
        theta=theta,
        uptime=uptime,
        kappa=params.default_alignment if kappa is None else kappa,
        support_gain_scale=params.default_gain_scale if support_gain_scale is None else support_gain_scale,
        mechanism_type=mechanism_type,
        mechanism_strength=mechanism_strength,
    )


# ---------------------------------------------------------------------------
# 4.4 有效伤害与 ClearIndex
# ---------------------------------------------------------------------------

#: CombatRequest.investment 的字段
INVESTMENT_KEYS = frozenset({"B_ref", "pierce_share", "cinema", "signature_weapon"})


@dataclass(frozen=True)
class WindowDamage:
    d_window: float
    clear_index: float
    breakdown: Mapping[str, float] = field(default_factory=dict)


def _mechanism_window_damage(params: ProxyParams, scenario: CombatScenario, *, b_ref: float,
                             pierce_share: float, gear: float, c_mult: float, w_mult: float,
                             anchor_unit: Optional[str], support_unit: Optional[str]) -> WindowDamage:
    """按主核—支援配对计算净增益，将机制适配与防御贯穿分开。

    窗口锁缩短整体可攻击窗口；壁垒锁只影响覆盖份额 q；防御锁保留减防公式。
    投资档位只改变伤害乘区，机制覆盖份额由环境和爆发对齐共同决定。
    """
    config = params.research_v2
    if config is None:
        raise CombatInputError("proxy_v2 需要 research_v2 参数")
    if anchor_unit not in config["unit_profiles"]:
        raise CombatInputError(f"proxy_v2 缺少主核 {anchor_unit!r} 的机制适配参数")
    kind = scenario.mechanism_type or "defense"
    if kind not in config["mechanisms"]:
        raise CombatInputError(f"场景机制 {kind!r} 未登记")
    strength = (config["mechanism_strength"] if scenario.mechanism_strength is None
                else scenario.mechanism_strength)
    penalty = config["mechanisms"][kind]["penalty"]
    base_match = config["unit_profiles"][anchor_unit]["affinities"][kind]
    gain, lift = 0.0, 0.0
    if support_unit is not None:
        if support_unit not in params.support_gains:
            raise CombatInputError(f"未登记的支援 {support_unit!r}")
        pair = config["support_pairs"].get(anchor_unit, {}).get(support_unit)
        if pair:
            gain = pair["net_gain"]
            lift = pair["match_lift"].get(kind, 0.0)
    match = min(1.0, base_match + lift)
    effective_strength = strength * penalty * (1.0 - match)
    s_fac = support_factor(gain, scenario.support_gain_scale)
    mods = scenario.modifiers or params.modifiers
    base_def = params.reference_defense if scenario.base_defense is None else scenario.base_defense
    m_ref = params.reference_multiplier
    m0 = defense_multiplier(params.level_coefficient,
                            effective_defense(base_def, params.shield_defense_bonus, 0, mods))
    q0 = relative_output(m0, m_ref, pierce_share)
    q = shield_damage_share(scenario.theta, scenario.kappa)
    window_retention, m1, q1 = 1.0, m0, q0
    if kind == "defense":
        extra_reduction = _unit("effective shield reduction", params.shield_damage_reduction * effective_strength)
        m1 = defense_multiplier(params.level_coefficient,
                                effective_defense(base_def, params.shield_defense_bonus * effective_strength, 1, mods))
        q1 = relative_output(m1, m_ref, pierce_share)
        shield_term = (1.0 - q) * q0 + q * (1.0 - extra_reduction) * q1
    elif kind == "window":
        window_retention = math.exp(-effective_strength)
        shield_term = q0
    else:
        shield_term = q0 * ((1.0 - q) + q * math.exp(-effective_strength))
    lock_retention = shield_term / q0 * window_retention
    effective_uptime = scenario.uptime * window_retention
    d_window = b_ref * gear * c_mult * w_mult * s_fac * effective_uptime * shield_term
    ci = d_window / scenario.hp
    return WindowDamage(d_window, ci, {
        "B_ref": b_ref, "G": gear, "C": c_mult, "W": w_mult,
        "support_factor": s_fac, "support_net_gain": gain,
        "mechanism_base_match": base_match, "mechanism_support_match_lift": lift,
        "mechanism_match": match, "mechanism_strength": strength,
        "mechanism_penalty": penalty, "mechanism_effective_strength": effective_strength,
        "mechanism_retained_output": lock_retention,
        "mechanism_window_retention": window_retention,
        "base_uptime": scenario.uptime, "uptime": effective_uptime,
        "m0": m0, "m1": m1, "m_ref": m_ref, "Q0": q0, "Q1": q1,
        "q": q, "shield_term": shield_term, "D_window": d_window,
        "HP": scenario.hp, "gap": max(0.0, 1.0 - ci),
    })


def window_damage(params: ProxyParams, scenario: CombatScenario, *, b_ref: float,
                  pierce_share: float, gear: float, cinema: int, signature_weapon: bool,
                  support_unit: Optional[str], anchor_unit: Optional[str] = None) -> WindowDamage:
    """D_window = B_ref·G·C(c)·W·support_factor·uptime·{(1−q)Q_0 + q(1−d_shield)Q_1}。"""
    b_ref = _nonneg("B_ref", b_ref)
    g = _finite("G", gear)
    if g < 1.0:
        raise CombatInputError(f"G 必须 ≥ 1，得到 {gear!r}")
    c_mult = cinema_multiplier(cinema, params.cinema_multipliers)
    w_mult = params.signature_weapon_multiplier if signature_weapon else 1.0
    if params.model_version.startswith("proxy_v2"):
        return _mechanism_window_damage(
            params, scenario, b_ref=b_ref, pierce_share=pierce_share, gear=g,
            c_mult=c_mult, w_mult=w_mult, anchor_unit=anchor_unit, support_unit=support_unit)
    if support_unit is None:
        s_fac = 1.0
    else:
        if support_unit not in params.support_gains:
            raise CombatInputError(f"未登记的支援 {support_unit!r}")
        s_fac = support_factor(params.support_gains[support_unit], scenario.support_gain_scale)

    mods = scenario.modifiers or params.modifiers
    base_def = params.reference_defense if scenario.base_defense is None else scenario.base_defense
    m0 = defense_multiplier(params.level_coefficient,
                            effective_defense(base_def, params.shield_defense_bonus, 0, mods))
    m1 = defense_multiplier(params.level_coefficient,
                            effective_defense(base_def, params.shield_defense_bonus, 1, mods))
    m_ref = params.reference_multiplier
    q0 = relative_output(m0, m_ref, pierce_share)
    q1 = relative_output(m1, m_ref, pierce_share)
    q = shield_damage_share(scenario.theta, scenario.kappa)
    shield_term = (1.0 - q) * q0 + q * (1.0 - params.shield_damage_reduction) * q1
    d_window = b_ref * g * c_mult * w_mult * s_fac * scenario.uptime * shield_term
    ci = d_window / scenario.hp
    return WindowDamage(
        d_window=d_window,
        clear_index=ci,
        breakdown={
            "B_ref": b_ref, "G": g, "C": c_mult, "W": w_mult, "support_factor": s_fac,
            "uptime": scenario.uptime, "m0": m0, "m1": m1, "m_ref": m_ref,
            "Q0": q0, "Q1": q1, "q": q, "shield_term": shield_term,
            "D_window": d_window, "HP": scenario.hp, "gap": max(0.0, 1.0 - ci),
        },
    )


class ProxyCombatModel:
    """CombatEngine 实现。队伍 = (锚定主输出, 固定中性, 可替换位)；可替换位至多一个支援。"""

    engine_kind = ENGINE_KIND

    def __init__(self, combat_cfg: Mapping[str, Any], scenarios: Sequence[CombatScenario] = ()):
        self.params = ProxyParams.from_config(combat_cfg)
        self._scenarios: dict[str, CombatScenario] = {}
        self._digest_cache: dict[Optional[CombatScenario], str] = {}
        for s in scenarios:
            self.add_scenario(s)

    def add_scenario(self, scenario: CombatScenario) -> None:
        if scenario.scenario_id in self._scenarios and self._scenarios[scenario.scenario_id] != scenario:
            raise CombatInputError(f"场景 {scenario.scenario_id} 已登记且内容不同")
        self._scenarios[scenario.scenario_id] = scenario

    def scenario(self, scenario_id: str) -> Optional[CombatScenario]:
        return self._scenarios.get(scenario_id)

    def _result(self, status: EvalStatus, scenario: Optional[CombatScenario],
                wd: Optional[WindowDamage] = None, reason: Optional[str] = None) -> CombatResult:
        digest = self._digest_cache.get(scenario)
        if digest is None:  # 每个场景只计算一次（纯函数，结果不变）
            digest = sha256_hex([self.params.config_digest,
                                 None if scenario is None else _scenario_json(scenario)])
            self._digest_cache[scenario] = digest
        if status is EvalStatus.OK:
            assert wd is not None
            return CombatResult(status, wd.clear_index, CombatResult.threshold_met(wd.clear_index),
                                dict(wd.breakdown), digest, self.engine_kind)
        if status is EvalStatus.NO_FEASIBLE_TEAM:
            return CombatResult(status, 0.0, False, {"reason_no_feasible_team": 1.0}, digest,
                                self.engine_kind)
        return CombatResult(status, None, None, {}, digest, self.engine_kind)

    def evaluate(self, request: CombatRequest) -> CombatResult:
        scenario = self._scenarios.get(request.scenario_id)
        if request.model_version != self.params.model_version:
            return self._result(EvalStatus.UNSUPPORTED, scenario)
        if scenario is None or request.missing_reason is not None or request.actual_g is None:
            return self._result(EvalStatus.MISSING_MODEL, scenario)
        if not request.team:
            return self._result(EvalStatus.NO_FEASIBLE_TEAM, scenario)
        if request.team[0] != request.anchor_unit_id or len(request.team) > 3:
            return self._result(EvalStatus.UNSUPPORTED, scenario)
        inv = request.investment
        missing = INVESTMENT_KEYS - set(inv)
        unknown = set(inv) - INVESTMENT_KEYS
        if missing or unknown:
            raise CombatInputError(f"investment 字段不符: 缺失 {sorted(missing)} 未知 {sorted(unknown)}")
        supports = [u for u in request.team[1:] if u in self.params.support_gains]
        if len(supports) > 1:
            return self._result(EvalStatus.UNSUPPORTED, scenario)  # 不叠加两个支援
        wd = window_damage(
            self.params, scenario,
            b_ref=inv["B_ref"], pierce_share=inv["pierce_share"], gear=request.actual_g,
            cinema=inv["cinema"], signature_weapon=bool(inv["signature_weapon"]),
            support_unit=supports[0] if supports else None,
            anchor_unit=request.anchor_unit_id,
        )
        share = (self.params.research_v2 or {}).get("co_output", {}).get(supports[0] if supports else None)
        if share:
            wd = self._with_co_output(scenario, wd, supports[0], share, request)
        return self._result(EvalStatus.OK, scenario, wd)

    def _with_co_output(self, scenario: CombatScenario, wd: WindowDamage, unit: str, share: float,
                        request: CombatRequest) -> WindowDamage:
        """混合型在可替换位：D = D_主核 + ρ·D_混合型单独（按其自身投资与机制适配，不带支援）。"""
        inv = request.flex_investment
        if inv is None or request.flex_actual_g is None or set(inv) != INVESTMENT_KEYS:
            raise CombatInputError(f"副输出 {unit} 需要完整的 flex_investment 与 flex_actual_g")
        solo = window_damage(
            self.params, scenario, b_ref=inv["B_ref"], pierce_share=inv["pierce_share"],
            gear=request.flex_actual_g, cinema=inv["cinema"], signature_weapon=bool(inv["signature_weapon"]),
            support_unit=None, anchor_unit=unit)
        d_window = wd.d_window + share * solo.d_window
        ci = d_window / scenario.hp
        return WindowDamage(d_window, ci, {
            **wd.breakdown, "D_anchor": wd.d_window, "co_output_share": share,
            "D_co_output_solo": solo.d_window, "D_window": d_window, "gap": max(0.0, 1.0 - ci)})


def _scenario_json(s: CombatScenario) -> str:
    d = {
        "scenario_id": s.scenario_id, "hp": s.hp, "theta": s.theta, "uptime": s.uptime,
        "kappa": s.kappa, "support_gain_scale": s.support_gain_scale, "base_defense": s.base_defense,
        "modifiers": None if s.modifiers is None else [
            s.modifiers.ratio_reduction, s.modifiers.flat_reduction,
            s.modifiers.pen_ratio, s.modifiers.pen_flat],
    }
    if s.mechanism_type is not None:
        d["mechanism_type"] = s.mechanism_type
    if s.mechanism_strength is not None:
        d["mechanism_strength"] = s.mechanism_strength
    return canonical_json(d)
