"""M-COMBAT 4.5：固定基准面板（fact_fixed_benchmark_panel 的计算侧）。

投资档位由 combat.json 的影画档位数 × 专武有无生成；支援选项为 None + support.gains；
κ 取 shield.alignment_values。低于达标线如实记录，不复制样本。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from ..contracts import CombatRequest, EvalStatus, sha256_hex
from .proxy import CombatInputError, CombatScenario, ProxyCombatModel

BENCHMARK_PROTOCOL = "fixed_benchmark_panel_v2"


@dataclass(frozen=True)
class BenchmarkAnchor:
    unit_id: str
    b_ref: float
    pierce_share: float


@dataclass(frozen=True)
class BenchmarkEnvironment:
    env_id: str
    hp: float
    theta: float
    uptime: float
    mechanism_type: Optional[str] = None
    mechanism_strength: Optional[float] = None


@dataclass(frozen=True)
class Investment:
    cinema: int
    signature_weapon: bool

    @property
    def label(self) -> str:
        return f"{self.cinema}+{int(self.signature_weapon)}"

    @classmethod
    def parse(cls, label: str) -> "Investment":
        try:
            c, w = label.split("+")
            ci, wi = int(c), int(w)
        except ValueError as exc:
            raise CombatInputError(f"非法投资标签 {label!r}") from exc
        if ci < 0 or wi not in (0, 1):
            raise CombatInputError(f"非法投资标签 {label!r}")
        return cls(ci, bool(wi))


@dataclass(frozen=True)
class BenchmarkRow:
    benchmark_id: str
    anchor_unit_id: str
    investment: str
    support: Optional[str]
    kappa: float
    gear: float
    env_id: str
    eval_status: EvalStatus
    ci: Optional[float]
    meets_threshold: Optional[bool]
    gap: Optional[float]

    @property
    def key(self) -> tuple:
        return (self.anchor_unit_id, self.investment, self.support or "", self.kappa, self.gear, self.env_id)


def investment_grid(model: ProxyCombatModel) -> list[Investment]:
    n = len(model.params.cinema_multipliers)
    return [Investment(c, w) for c in range(n) for w in (False, True)]


def support_options(model: ProxyCombatModel) -> list[Optional[str]]:
    return [None, *sorted(model.params.support_gains)]


def benchmark_id(model: ProxyCombatModel, anchors: Sequence[BenchmarkAnchor],
                 environments: Sequence[BenchmarkEnvironment], gears: Sequence[float],
                 neutral_fixed: str, neutral_flex: str, *,
                 investments: Optional[Iterable[Investment]] = None,
                 supports: Optional[Iterable[Optional[str]]] = None,
                 kappas: Optional[Iterable[float]] = None) -> str:
    """固定战斗模型、场景与队伍清单的摘要。"""
    inv = list(investments) if investments is not None else investment_grid(model)
    sup = list(supports) if supports is not None else support_options(model)
    kap = list(kappas) if kappas is not None else list(model.params.alignment_values)
    inv_keys = [(i.cinema, i.signature_weapon) for i in inv]
    for name, values in (("investments", inv_keys), ("supports", sup), ("kappas", kap),
                         ("gears", gears)):
        if len(set(values)) != len(values):
            raise CombatInputError(f"基准选项 {name} 重复")
    return sha256_hex([
        BENCHMARK_PROTOCOL,
        model.params.model_version,
        model.params.config_digest,
        sorted([[a.unit_id, a.b_ref, a.pierce_share] for a in anchors]),
        sorted([[e.env_id, e.hp, e.theta, e.uptime] +
                ([e.mechanism_type, e.mechanism_strength]
                 if e.mechanism_type is not None or e.mechanism_strength is not None else [])
                for e in environments]),
        sorted(float(g) for g in gears),
        [neutral_fixed, neutral_flex],
        sorted(inv_keys),
        sorted(sup, key=lambda s: (s is not None, s or "")),
        sorted(float(k) for k in kap),
    ])


def build_fixed_benchmark_panel(
    model: ProxyCombatModel,
    anchors: Sequence[BenchmarkAnchor],
    environments: Sequence[BenchmarkEnvironment],
    *,
    neutral_fixed: str,
    neutral_flex: str,
    gears: Sequence[float] = (1.0,),
    investments: Optional[Iterable[Investment]] = None,
    supports: Optional[Iterable[Optional[str]]] = None,
    kappas: Optional[Iterable[float]] = None,
) -> list[BenchmarkRow]:
    """枚举预注册面板；行按稳定键排序。"""
    if len({a.unit_id for a in anchors}) != len(anchors):
        raise CombatInputError("锚定主输出重复")
    if len({e.env_id for e in environments}) != len(environments):
        raise CombatInputError("环境ID重复")
    inv_list = list(investments) if investments is not None else investment_grid(model)
    sup_list = list(supports) if supports is not None else support_options(model)
    kap_list = list(kappas) if kappas is not None else list(model.params.alignment_values)
    bid = benchmark_id(model, anchors, environments, gears, neutral_fixed, neutral_flex,
                       investments=inv_list, supports=sup_list, kappas=kap_list)

    rows: list[BenchmarkRow] = []
    for env in environments:
        for kappa in kap_list:
            sid = f"{bid[:12]}:{env.env_id}:k={kappa!r}"
            model.add_scenario(CombatScenario(
                scenario_id=sid, hp=env.hp, theta=env.theta, uptime=env.uptime, kappa=kappa,
                support_gain_scale=model.params.default_gain_scale,
                mechanism_type=env.mechanism_type,
                mechanism_strength=env.mechanism_strength,
            ))
            for a in anchors:
                for inv in inv_list:
                    for sup in sup_list:
                        flex = neutral_flex if sup is None else sup
                        for g in gears:
                            req = CombatRequest(
                                team=(a.unit_id, neutral_fixed, flex),
                                anchor_unit_id=a.unit_id,
                                investment={"B_ref": a.b_ref, "pierce_share": a.pierce_share,
                                            "cinema": inv.cinema,
                                            "signature_weapon": inv.signature_weapon},
                                actual_g=g,
                                scenario_id=sid,
                                model_version=model.params.model_version,
                            )
                            res = model.evaluate(req)
                            rows.append(BenchmarkRow(
                                benchmark_id=bid, anchor_unit_id=a.unit_id, investment=inv.label,
                                support=sup, kappa=kappa, gear=float(g), env_id=env.env_id,
                                eval_status=res.eval_status, ci=res.ci,
                                meets_threshold=res.meets_threshold,
                                gap=None if res.ci is None else max(0.0, 1.0 - res.ci),
                            ))
    rows.sort(key=lambda r: r.key)
    return rows


def paired_differences(rows: Sequence[BenchmarkRow], field_name: str, base, alt) -> list[tuple[BenchmarkRow, BenchmarkRow, float]]:
    """只改变 field_name（base→alt），其余键完全相同的配对差 ΔCI。缺失 CI 的配对跳过。"""
    names = ("anchor_unit_id", "investment", "support", "kappa", "gear", "env_id")
    if field_name not in names:
        raise CombatInputError(f"未知配对字段 {field_name!r}")
    keys = [(r.benchmark_id, *r.key) for r in rows]
    if len(set(keys)) != len(keys):
        raise CombatInputError("基准面板存在重复行，不能唯一配对")
    idx = names.index(field_name)

    def rest(r: BenchmarkRow) -> tuple:
        k = (r.anchor_unit_id, r.investment, r.support, r.kappa, r.gear, r.env_id)
        return (r.benchmark_id, *k[:idx], *k[idx + 1:])

    base_rows = {rest(r): r for r in rows if getattr(r, field_name) == base}
    out = []
    for r in rows:
        if getattr(r, field_name) != alt:
            continue
        b = base_rows.get(rest(r))
        if b is not None and b.ci is not None and r.ci is not None:
            out.append((b, r, r.ci - b.ci))
    out.sort(key=lambda t: (t[1].benchmark_id, t[1].key))
    return out
