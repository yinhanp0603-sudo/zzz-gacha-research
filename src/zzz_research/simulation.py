"""M-RUNTIME：情景解析、六步编排与单抽原子提交。

- 时间推进与六步顺序只在此维护；概率/价格/效用/伤害公式全部调用对应模块。
- 单抽：wallet 支付草案 → gacha 单抽转移 → roster 入库 → 原子提交（任一步失败不改状态）。
- 玩家之间相互独立，处理顺序不影响结果；随机值全部来自事件索引 CRN。

情景解析使用三条显式假设（版本 SCENARIO_RESOLVER_VERSION，同时进入config_id和运行清单）：
  * HP_t = hp_index · (1 + hp_growth)^(t−1)
  * 第 k 个首发主输出 B_ref = B_ref_first · (1 + growth_per_debut_step)^(k−1)；
  * role=piercing 的 pierce_share=1，role=normal 为 0。
"""

from __future__ import annotations

import math
import platform
import time
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Any, Iterable, Mapping, Optional, Sequence

from .agents import decision as DEC
from .agents import lifecycle as LC
from .agents import roster as R
from .agents import spending as SP
from .combat.proxy import CombatScenario, ProxyCombatModel
from .contracts import (
    BudgetView,
    BundleKind,
    CombatRequest,
    CombatResult,
    CreditSource,
    EvalStatus,
    EventBundle,
    LifecycleState,
    PityState,
    PoolKind,
    ResolvedConfig,
    RosterState,
    RunIdentity,
    WalletState,
    canonical_json,
    deep_thaw,
    sha256_hex,
    signature_weapon_id,
)
from .economy import gacha as G
from .economy import wallet as W
from .economy.planner import DPPlanner, PlanForecast
from .rng import HashCounterRng
from .storage.writer import BundleAnnotation, PullAnnotation, SQLiteWriter, STORAGE_VERSION
from .versions import PLANNER_VERSION, RUNTIME_VERSION, SCENARIO_RESOLVER_VERSION

ROLE_STARTER = "starter"
ROLE_SUPPORT = "support"
ROLE_HYBRID = "hybrid"  # 主C兼体系辅助：可作锚定主输出，也可进入同体系主C的可替换位
ANCHOR_ROLES = frozenset({ROLE_STARTER, "normal", "piercing", ROLE_HYBRID})
FLEX_ROLES = frozenset({ROLE_SUPPORT, ROLE_HYBRID})
STARTER_ID = "starter_dps"


class SimulationError(RuntimeError):
    pass


# =========================================================================== 情景解析


@dataclass(frozen=True)
class Unit:
    unit_id: str
    role: str
    debut_t: Optional[int]
    debut_day: Optional[float]
    b_ref: Optional[float]
    pierce_share: Optional[float]


@dataclass(frozen=True)
class Banner:
    banner_id: str
    t_idx: int
    event_order: int
    unit_id: str


@dataclass(frozen=True)
class Period:
    t_idx: int
    period_id: str
    start_day: float
    end_day: float
    evaluation_day: float
    scenario: CombatScenario
    banners: tuple[Banner, ...]

    @property
    def elapsed_days(self) -> float:
        return self.end_day - self.start_day


@dataclass(frozen=True)
class World:
    scenario_name: str
    timeline_id: str
    catalog_id: str
    units: Mapping[str, Unit]
    periods: tuple[Period, ...]
    neutral_fixed: str
    neutral_flex: str
    supports: tuple[str, ...]
    anchors: tuple[str, ...]


def resolve_world(cfg: ResolvedConfig, model: ProxyCombatModel) -> World:
    scen = deep_thaw(cfg.module("scenarios"))
    name = cfg.module("experiments")["base_scenario"]
    ref = scen["reference_scenarios"][name]
    tl = scen["timeline"]
    if tl["kind"] != "synthetic_relative" or tl["evaluation"] != "period_end":
        raise SimulationError("当前 runtime 只支持合成相对时间轴、区间末评估")
    span = tl["days_per_period"]
    pp = scen["product_power"]
    starter = scen["initial_assets"][STARTER_ID]
    neutrals = list(scen["initial_assets"]["neutral_units"])
    if len(neutrals) != 2:
        raise SimulationError("需要恰好两个初始中性角色（固定位 + 可替换位）")

    units: dict[str, Unit] = {STARTER_ID: Unit(STARTER_ID, ROLE_STARTER, None, starter["acquired_day"],
                                               float(starter["B_ref"]), float(starter["pierce_share"]))}
    for n in neutrals:
        units[n] = Unit(n, "neutral", None, 0.0, None, None)
    events = sorted(scen["events"], key=lambda e: (e["t"], e["event_order"], e["banner_id"]))
    k = 0
    for e in events:
        end = span * e["t"]
        if e["role"] == ROLE_SUPPORT:
            units[e["unit_id"]] = Unit(e["unit_id"], ROLE_SUPPORT, e["t"], end, None, None)
        elif e["role"] in ("normal", "piercing", ROLE_HYBRID):
            k += 1
            units[e["unit_id"]] = Unit(
                e["unit_id"], e["role"], e["t"], end,
                pp["B_ref_first"] * (1.0 + pp["growth_per_debut_step"]) ** (k - 1),
                1.0 if e["role"] == "piercing" else 0.0)
        else:
            raise SimulationError(f"未知角色类型 {e['role']}")

    p = model.params
    periods = []
    research = scen.get("research_v2")
    n_periods = research["observation_periods"] if research else tl["n_periods"]
    for t in range(1, n_periods + 1):
        evs = [e for e in events if e["t"] == t]
        if ref["theta_mode"] == "path":
            if not evs:
                raise SimulationError(f"θ 路径在第 {t} 期缺少事件")
            theta = evs[0]["theta"] * ref["theta_scale"]
        elif ref["theta_mode"] == "constant":
            theta = ref["theta_constant"] * ref["theta_scale"]
        else:
            raise SimulationError(f"未知 theta_mode {ref['theta_mode']}")
        uptime = evs[0]["uptime"] if evs else 1.0
        sc = CombatScenario(
            scenario_id=f"{name}:{cfg.config_id[:12]}:t{t}",
            hp=p.hp_index * (1.0 + ref["hp_growth"]) ** (t - 1),
            theta=theta, uptime=uptime, kappa=p.default_alignment,
            support_gain_scale=ref["support_gain_scale"] * p.default_gain_scale,
            mechanism_type=(research["mechanism_schedule"][str(t)]
                            if research and p.model_version.startswith("proxy_v2") else None),
            mechanism_strength=(p.research_v2["mechanism_strength"]
                                if p.model_version.startswith("proxy_v2") else None),
        )
        model.add_scenario(sc)
        periods.append(Period(t, f"t{t:02d}", span * (t - 1), span * t, span * t, sc,
                              tuple(Banner(e["banner_id"], t, e["event_order"], e["unit_id"]) for e in evs)))
    for s in units.values():
        if s.role in FLEX_ROLES and s.unit_id not in p.support_gains:
            raise SimulationError(f"支援 {s.unit_id} 缺少增益配置")
    return World(
        scenario_name=name, timeline_id=sha256_hex(["timeline", tl])[:16],
        catalog_id=sha256_hex([
            "catalog_v2", SCENARIO_RESOLVER_VERSION, scen["events"], scen["initial_assets"], pp,
            span, p.support_gains,
            {kind: pool["pity_group"] for kind, pool in cfg.module("gacha")["pools"].items()},
            sorted(cfg.module("gacha")["target_paths"]),
        ])[:16],
        units=units, periods=tuple(periods), neutral_fixed=neutrals[0], neutral_flex=neutrals[1],
        supports=tuple(sorted(u.unit_id for u in units.values() if u.role in FLEX_ROLES)),
        anchors=tuple(sorted(u.unit_id for u in units.values() if u.role in ANCHOR_ROLES)),
    )


# =========================================================================== 战斗评估


@dataclass(frozen=True)
class AnchorEval:
    unit_id: str
    team: Optional[tuple[str, str, str]]
    result: CombatResult
    gear: float
    cinema: int
    has_weapon: bool


class Evaluator:
    """全库存最佳 CI 评估。结果只依赖与战斗相关的库存签名、场景与日期，按签名缓存（纯函数）。"""

    CACHE_LIMIT = 200_000

    def __init__(self, world: World, model: ProxyCombatModel):
        self.world, self.model = world, model
        self._cache: dict[tuple, list] = {}

    def _signature(self, roster: RosterState, scenario: CombatScenario, day: float) -> tuple:
        chars = tuple((c.unit_id, c.cinema, c.first_acquired_day, c.gear_value if roster.frozen else None)
                      for c in roster.characters)
        weapons = tuple(w.weapon_id for w in roster.weapons if w.count >= 1)
        return (scenario.scenario_id, day, roster.frozen, chars, weapons)

    def gear_for(self, roster: RosterState, unit_id: str, day: float) -> float:
        c = roster.character(unit_id)
        if roster.frozen:
            if c.gear_value is None:
                raise SimulationError(f"冻结角色 {unit_id} 缺少实际装备值")
            return c.gear_value
        return self.model.params.gear(day, c.first_acquired_day)

    def anchors(self, roster: RosterState, scenario: CombatScenario, day: float) -> list[AnchorEval]:
        key = self._signature(roster, scenario, day)
        hit = self._cache.get(key)
        if hit is not None:
            return list(hit)
        out = self._anchors(roster, scenario, day)
        if len(self._cache) >= self.CACHE_LIMIT:
            self._cache.clear()
        self._cache[key] = tuple(out)
        return out

    def _anchors(self, roster: RosterState, scenario: CombatScenario, day: float) -> list[AnchorEval]:
        out = []
        for a in self.world.anchors:
            c = roster.character(a)
            if c is None:
                continue
            u = self.world.units[a]
            g = self.gear_for(roster, a, day)
            sig = roster.weapon_count(signature_weapon_id(a)) >= 1
            teams = R.enumerate_feasible_teams(roster, anchors=[a], neutral_fixed=self.world.neutral_fixed,
                                               neutral_flex=self.world.neutral_flex,
                                               supports=self.world.supports)
            best: Optional[tuple[tuple, CombatResult]] = None
            for team in teams:
                flex_inv, flex_g = self._flex_investment(roster, team[2], day)
                res = self.model.evaluate(CombatRequest(
                    team=team, anchor_unit_id=a,
                    investment={"B_ref": u.b_ref, "pierce_share": u.pierce_share,
                                "cinema": c.cinema, "signature_weapon": sig},
                    actual_g=g, scenario_id=scenario.scenario_id, model_version=self.model.params.model_version,
                    flex_investment=flex_inv, flex_actual_g=flex_g))
                if not res.eval_status.ci_defined:
                    best = (team, res)
                    break
                if best is None or res.ci > best[1].ci:  # 并列时保留按稳定顺序的先者
                    best = (team, res)
            if best is None:
                res = CombatResult(EvalStatus.NO_FEASIBLE_TEAM, 0.0, False, {}, "", self.model.engine_kind)
                out.append(AnchorEval(a, None, res, g, c.cinema, sig))
            else:
                out.append(AnchorEval(a, best[0], best[1], g, c.cinema, sig))
        return out

    def _flex_investment(self, roster: RosterState, unit_id: str, day: float
                         ) -> tuple[Optional[dict], Optional[float]]:
        u = self.world.units.get(unit_id)
        if u is None or u.role != ROLE_HYBRID:
            return None, None
        c = roster.character(unit_id)
        return ({"B_ref": u.b_ref, "pierce_share": u.pierce_share, "cinema": c.cinema,
                 "signature_weapon": roster.weapon_count(signature_weapon_id(unit_id)) >= 1},
                self.gear_for(roster, unit_id, day))

    @staticmethod
    def best(evals: Sequence[AnchorEval]) -> tuple[EvalStatus, Optional[float], Optional[str]]:
        if any(not e.result.eval_status.ci_defined for e in evals):
            bad = next(e for e in evals if not e.result.eval_status.ci_defined)
            return bad.result.eval_status, None, None
        if not evals:
            return EvalStatus.NO_FEASIBLE_TEAM, 0.0, None
        top = max(evals, key=lambda e: e.result.ci)  # 并列取稳定ID顺序中的第一个
        return EvalStatus.OK, top.result.ci, top.unit_id

    def ci_evaluator(self, scenario: CombatScenario, day: float):
        def f(roster: RosterState) -> tuple[EvalStatus, Optional[float]]:
            st, ci, _ = self.best(self.anchors(roster, scenario, day))
            return st, ci
        return f


# =========================================================================== 玩家状态


@dataclass
class PlayerState:
    player_id: int
    tier_id: str
    wallet: WalletState
    roster: RosterState
    pities: dict[str, PityState]
    lifecycle: LifecycleState
    batch_earned: dict[str, int] = field(default_factory=dict)  # batch_id → earned_period
    spending: Optional[SP.SpendingState] = None


@dataclass(frozen=True)
class PeriodSummary:
    player_id: int
    t_idx: int
    active_start: bool
    active_end: bool
    ci_pre: Optional[float]
    ci_post: Optional[float]
    pulls: int
    topup_pulls: int
    cash_fen: int
    decisions: int
    entered: int
    partial: int


@dataclass
class SimulationResult:
    run_id: str
    config_id: str
    master_seed: int
    world: World
    players: dict[int, PlayerState]
    summaries: list[PeriodSummary]
    timings: list[tuple[str, float, Optional[int]]]


# =========================================================================== 编排


class Runtime:
    def __init__(self, cfg: ResolvedConfig, master_seed: int, writer: Optional[SQLiteWriter] = None):
        actual = {"planner": PLANNER_VERSION, "runtime": RUNTIME_VERSION,
                  "scenario_resolver": SCENARIO_RESOLVER_VERSION}
        if any(cfg.algorithm_versions.get(k) != v for k, v in actual.items()):
            raise SimulationError("科学算法版本与配置身份不一致，请重新resolve_config")
        self.cfg = cfg
        self.rng = HashCounterRng.from_config(master_seed, cfg.module("randomness"))
        self.model = ProxyCombatModel(cfg.module("combat"))
        self.world = resolve_world(cfg, self.model)
        self.eval = Evaluator(self.world, self.model)
        self.gacha = G.GachaModel.from_config(cfg.module("gacha"))
        self.planner = DPPlanner(self.gacha)
        self.policy = W.WalletPolicy.from_config(cfg.module("economy"))
        self.dec = DEC.DecisionParams.from_config(cfg.module("behavior"))
        self.lc = LC.LifecycleParams.from_config(cfg.module("behavior"))
        self.spending_params = SP.SpendingParams.from_config(cfg.module("behavior"))
        self.tiers = {t["id"]: t for t in deep_thaw(cfg.module("population"))["tiers"]}
        self.writer = writer
        self.identity = RunIdentity(
            experiment_id=str(cfg.metadata.get("profile_id") or "experiment"),
            config_id=cfg.config_id, replicate_id=f"seed:{master_seed}", master_seed=master_seed)
        self.run_id = self.identity.run_id
        self._teams_written: set[str] = set()
        self._team_ids: dict[tuple, str] = {}

    # ------------------------------------------------------------------ 初始化
    def assign_tier(self, player_id: int) -> str:
        u = self.rng.population_u(player_id, "tier")
        acc = 0.0
        ids = sorted(self.tiers)
        for tid in ids:
            acc += self.tiers[tid]["share"]
            if u < acc:
                return tid
        return ids[-1]

    def init_player(self, player_id: int) -> tuple[PlayerState, list]:
        tier = self.assign_tier(player_id)
        deltas = R.initial_asset_deltas(self.cfg.module("scenarios"), player_id=player_id,
                                        starter_unit_id=STARTER_ID)
        roster = R.apply_asset_deltas(RosterState(), deltas)
        pities = {p.pity_group: PityState(p.pity_group) for p in self.gacha.pools.values()}
        lc = self.lc.initial_state(self.dec.tiers[tier].initial_hoarding)
        spending = (SP.SpendingState(bool(self.tiers[tier]["subscribed"]),
                                    self.tiers[tier]["paid_pulls_per_standard_span"] > 0)
                    if self.spending_params is not None else None)
        return PlayerState(player_id, tier, WalletState(), roster, pities, lc,
                           spending=spending), deltas

    # ------------------------------------------------------------------ 写出辅助
    def _w(self, table: str, row: Mapping[str, Any]) -> None:
        if self.writer is not None:
            self.writer.insert(table, row)

    def _spending_row(self, ps: PlayerState, period: Period, kind: str, slot: int,
                      decision: SP.SpendingDecision, *, capacity: int,
                      banner_id: Optional[str] = None, gain: Optional[float] = None,
                      expected_topup: Optional[float] = None) -> None:
        self._w("fact_spending_decision", {
            "run_id": self.run_id, "player_id": ps.player_id, "t_idx": period.t_idx,
            "decision_kind": kind, "event_slot": slot, "banner_id": banner_id,
            "prior_F": ps.lifecycle.fatigue, "prior_failure_span": ps.lifecycle.failure_span,
            "prior_hoarding": ps.lifecycle.hoarding,
            "last_paid_failure": int(ps.spending.last_paid_failure),
            "probability": decision.probability, "utility": decision.utility,
            "allowed": int(decision.allowed), "capacity": capacity,
            "planned_gain": gain, "expected_topup": expected_topup,
            "subscription_active": int(decision.state.subscription_active),
            "topup_allowed": int(decision.state.topup_allowed), "reason": decision.reason,
        })

    def _write_team(self, team: Optional[tuple], cinema: int, has_weapon: bool, gear: float) -> Optional[str]:
        if team is None:
            return None
        memo_key = (team, cinema, has_weapon, gear)
        hit = self._team_ids.get(memo_key)
        if hit is not None:
            return hit
        builds = {"anchor": team[0], "cinema": cinema, "signature_weapon": has_weapon, "gear": gear}
        build_hash = sha256_hex(builds)
        support = team[2] if team[2] in self.world.supports else "NONE"
        team_id = sha256_hex(["team_v1", list(team), build_hash])
        if team_id not in self._teams_written:
            self._teams_written.add(team_id)
            self._w("dim_team_snapshot", {"team_id": team_id, "anchor_unit_id": team[0],
                                          "neutral_unit_id": team[1], "support_id": support,
                                          "component_builds_json": canonical_json(builds),
                                          "build_hash": build_hash})
        self._team_ids[memo_key] = team_id
        return team_id

    def _write_panel(self, ps: PlayerState, period: Period, phase: str, evals: Sequence[AnchorEval],
                     active_start: bool, inactive: bool = False) -> None:
        _, _, deployed = Evaluator.best(evals) if not inactive else (None, None, None)
        for e in evals:
            team_id = None if inactive else self._write_team(e.team, e.cinema, e.has_weapon, e.gear)
            sup = None
            if e.team is not None and not inactive:
                sup = e.team[2] if e.team[2] in self.world.supports else "NONE"
            self._w("fact_player_roster_panel", {
                "run_id": self.run_id, "player_id": ps.player_id, "t_idx": period.t_idx, "unit_id": e.unit_id,
                "eval_phase": phase, "scenario_id": period.scenario.scenario_id, "team_id": team_id,
                "support_id": sup, "cinema": e.cinema, "has_weapon": int(e.has_weapon),
                "disc_actual": None if inactive else e.gear,
                "build_hash": None if team_id is None else sha256_hex(
                    {"anchor": e.unit_id, "cinema": e.cinema, "signature_weapon": e.has_weapon, "gear": e.gear}),
                "clear_index": None if inactive else e.result.ci,
                "is_full_star": None if inactive or e.result.meets_threshold is None else int(e.result.meets_threshold),
                "is_deployed_main": int(not inactive and e.unit_id == deployed),
                "has_active_support": int(sup not in (None, "NONE")),
                "active_start": int(active_start),
                "eval_status": "inactive" if inactive else e.result.eval_status.value})

    def _wallet_rows(self, ps: PlayerState, t: int, phase: str) -> None:
        acc: dict[tuple[str, int], int] = {}
        for b in ps.wallet.batches:
            key = (b.source.value, ps.batch_earned[b.batch_id])
            acc[key] = acc.get(key, 0) + b.credit_minor
        for (prov, earned), bal in sorted(acc.items()):
            self._w("fact_wallet_snapshot", {"run_id": self.run_id, "player_id": ps.player_id, "t_idx": t,
                                             "phase": phase, "provenance": prov, "earned_period": earned,
                                             "balance_minor": bal})

    def _pity_rows(self, ps: PlayerState, t: int, phase: str) -> None:
        for g in sorted(ps.pities):
            p = ps.pities[g]
            self._w("fact_pity_snapshot", {"run_id": self.run_id, "player_id": ps.player_id, "t_idx": t,
                                           "pity_group": g, "phase": phase, "pity": p.pulls_since_s,
                                           "guaranteed": int(p.guaranteed_up)})

    def _annotate_resources(self, ps: PlayerState, deltas, cash_events, t: int) -> dict:
        cash_for_batch = {}
        for e in cash_events:
            if e.product == W.PRODUCT_SUBSCRIPTION:
                cash_for_batch[f"sub:{e.event_id}"] = e.event_id
            elif e.related_pull_id:
                cash_for_batch[f"topup:{e.related_pull_id}"] = e.event_id
        out = {}
        for d in deltas:
            earned = ps.batch_earned.get(d.batch_id, t)
            out[d.event_id] = (earned, cash_for_batch.get(d.batch_id) if d.delta_credit_minor > 0 else None)
        return out

    # ------------------------------------------------------------------ 单抽（execute_target 的一步）
    def _one_pull(self, ps: PlayerState, period: Period, banner: Banner, stage: G.TargetStage,
                  slot: int) -> Optional[tuple[EventBundle, BundleAnnotation]]:
        day = period.end_day
        pull_id = f"p{ps.player_id}:t{period.t_idx}:{banner.banner_id}:{stage.pool_kind.value}:{slot}"
        pay = W.prepare_payment(self.policy, ps.wallet, pull_id=pull_id, day=day)
        if pay is None:
            return None
        pool = self.gacha.pool(stage.pool_kind)
        pity = ps.pities[pool.pity_group]
        p_s = pool.s_probability(pity.pulls_since_s + 1)
        out = G.draw_with_rng(self.gacha, self.rng, player_id=ps.player_id, t_idx=period.t_idx,
                              banner_id=banner.banner_id, pool_kind=stage.pool_kind, draw_slot=slot,
                              pity=pity, up_unit_id=banner.unit_id, day=day, pull_id=pull_id)
        roster_after = R.apply_asset_deltas(ps.roster, out.asset_deltas)
        batch_earned_after = dict(ps.batch_earned)
        for d in pay.resource_deltas:
            batch_earned_after.setdefault(d.batch_id, period.t_idx)
        pities_after = dict(ps.pities)
        pities_after[pool.pity_group] = out.pity_after
        bundle = EventBundle(
            bundle_id=f"b:{pull_id}", kind=BundleKind.SINGLE_PULL, run_id=self.run_id,
            player_id=ps.player_id, t_idx=period.t_idx, wallet_before=ps.wallet, wallet_after=pay.state,
            roster_before=ps.roster, roster_after=roster_after, pity_before=(pity,), pity_after=(out.pity_after,),
            pulls=(out,), resource_deltas=pay.resource_deltas, cash_events=pay.cash_events,
            asset_deltas=out.asset_deltas)
        ann = BundleAnnotation(period.period_id, {pull_id: PullAnnotation(p_s, stage.stage_id)},
                               self._annotate_resources(ps, pay.resource_deltas, pay.cash_events, period.t_idx))
        # 先完整接收事件包；阈值触发的SQL提交也必须成功，内存才生效。
        if self.writer is not None:
            with self.writer.atomic_buffer():
                self.writer.write_bundle(bundle, ann)
                self._cohort(ps, period, bundle)
        # ---- 原子提交：钱包、库存、保底与来源批次索引同时生效
        ps.wallet, ps.roster, ps.pities = pay.state, roster_after, pities_after
        ps.batch_earned = batch_earned_after
        return bundle, ann

    # ------------------------------------------------------------------ 一期
    def step_period(self, ps: PlayerState, period: Period, period_deltas_out: list) -> PeriodSummary:
        t, day = period.t_idx, period.end_day
        active_start = ps.lifecycle.active_at_end  # 期初活跃 = 上期末仍活跃
        ps.wallet = W.roll_period(ps.wallet)
        wallet_start = ps.wallet
        self._wallet_rows(ps, t, "start")
        self._pity_rows(ps, t, "start")

        if not active_start:
            # 已流失：不执行 1–5，只保留余额与冻结反事实评估
            evals = self.eval.anchors(ps.roster, period.scenario, day)
            self._write_panel(ps, period, "post", evals, False, inactive=True)
            self._write_panel(ps, period, "frozen_post", evals, False)
            ps.lifecycle = LC.inactive_state(ps.lifecycle)
            self._finish_period_rows(ps, period, None, None, [], [], 0, None)
            return PeriodSummary(ps.player_id, t, False, False, None, None, 0, 0, 0, 0, 0, 0)

        # ---- 1. 推进日期、养成、发放、订阅、直购额度
        deltas: list = []
        cash: list = []
        ch = W.grant_free(self.policy, ps.wallet, t_idx=t, start_day=period.start_day,
                          end_day=period.end_day, grant_day=period.start_day)
        ps.batch_earned[f"free:t{t}"] = t
        ps.wallet, deltas, cash = ch.state, list(ch.resource_deltas), list(ch.cash_events)
        tier = self.tiers[ps.tier_id]
        if tier["subscribed"]:
            for renewal_slot, d in enumerate(self.policy.renewal_days_in(period.start_day, period.end_day)):
                if self.spending_params is not None:
                    decision = SP.subscription_decision(
                        self.spending_params, ps.spending, ps.lifecycle,
                        tier_subscribed=True,
                        u=self.rng.uniform([ps.player_id, t, "subscription", renewal_slot, "spending"]))
                    self._spending_row(ps, period, "subscription", renewal_slot, decision, capacity=1)
                    ps.spending = decision.state
                    if not decision.allowed:
                        continue
                ev = f"subcash:p{ps.player_id}:t{t}:d{d}"
                ps.batch_earned[f"sub:{ev}"] = t
                ch = W.purchase_subscription(self.policy, ps.wallet, day=d, event_id=ev)
                ps.wallet = ch.state
                deltas += ch.resource_deltas
                cash += ch.cash_events
        paid_cap = self.policy.paid_cap_for(tier["paid_pulls_per_standard_span"], period.start_day, period.end_day)
        ps.wallet = W.set_period_paid_cap(self.policy, ps.wallet, paid_cap)
        gear_now = {c.unit_id: self.model.params.gear(day, c.first_acquired_day) for c in ps.roster.characters}
        ps.roster = R.update_disc_values(ps.roster, gear_now)
        pre_bundle = EventBundle(
            bundle_id=f"b:p{ps.player_id}:t{t}:period", kind=BundleKind.PERIOD, run_id=self.run_id,
            player_id=ps.player_id, t_idx=t, wallet_before=wallet_start, wallet_after=ps.wallet,
            roster_before=ps.roster, roster_after=ps.roster, resource_deltas=tuple(deltas),
            cash_events=tuple(cash))
        if self.writer is not None:
            self.writer.write_bundle(pre_bundle, BundleAnnotation(
                period.period_id, {}, self._annotate_resources(ps, deltas, cash, t)))

        # ---- 2. PreEval
        pre_evals = self.eval.anchors(ps.roster, period.scenario, day)
        st_pre, ci_pre, _ = Evaluator.best(pre_evals)
        self._write_panel(ps, period, "pre", pre_evals, True)

        # ---- 3/4. 按 banner 顺序决策与逐抽执行
        pulls = topups = decisions = entered = partial = 0
        ci_fn = self.eval.ci_evaluator(period.scenario, day)
        for banner_slot, banner in enumerate(sorted(period.banners, key=lambda b: (b.event_order, b.banner_id))):
            unit = self.world.units[banner.unit_id]
            path = self.dec.support_target if unit.role == ROLE_SUPPORT else self.dec.tiers[ps.tier_id].dps_target
            plan = G.build_target_plan(self.gacha, path, banner.unit_id,
                                       {PoolKind.CHARACTER: banner.banner_id, PoolKind.WEAPON: banner.banner_id})
            F = W.stock_pulls(self.policy, ps.wallet)
            P = ps.wallet.direct_purchase_remaining_pulls if self.policy.topup_enabled else 0
            fc: PlanForecast = self.planner.forecast_target(plan, ps.roster, BudgetView(F, P), ps.pities,
                                                            ci_evaluator=ci_fn, day=day)
            if self.spending_params is not None and P > 0 and not fc.target_owned and fc.eval_status.ci_defined:
                _, gain = DEC.utility(self.dec, ps.tier_id, ps.lifecycle.hoarding, fc)
                decision = SP.paid_decision(
                    self.spending_params, ps.spending, ps.lifecycle,
                    paid_cap=P, expected_gain=gain, expected_topup_pulls=fc.expected_topup,
                    u=self.rng.uniform([ps.player_id, t, banner.banner_id, "topup", "spending"]))
                self._spending_row(ps, period, "topup", banner_slot, decision, capacity=P,
                                   banner_id=banner.banner_id, gain=gain,
                                   expected_topup=fc.expected_topup)
                ps.spending = decision.state
                if not decision.allowed:
                    P = 0
                    fc = self.planner.forecast_target(plan, ps.roster, BudgetView(F, P), ps.pities,
                                                      ci_evaluator=ci_fn, day=day)
            needs_u = not (fc.target_owned or fc.budget_pulls == 0 or not fc.eval_status.ci_defined)
            u = self.rng.decision_u(ps.player_id, t, banner.banner_id, 0) if needs_u else None
            intent, rec = DEC.decide(self.dec, tier_id=ps.tier_id, hoarding=ps.lifecycle.hoarding,
                                     path_id=path, forecast=fc, u_decision=u,
                                     novelty=unit.role != ROLE_SUPPORT)
            decisions += 1
            self._w("fact_decision_log", {
                "run_id": self.run_id, "player_id": ps.player_id, "t_idx": t, "banner_id": banner.banner_id,
                "decision_slot": 0, "target_path": rec.target_path, "CI_pre": rec.ci_pre,
                "available_pulls": rec.available_pulls, "remaining_paid_cap": rec.remaining_paid_cap,
                "expected_gain": rec.expected_gain, "expected_pulls": rec.expected_pulls,
                "expected_topup_pulls": rec.expected_topup_pulls, "utility": rec.utility,
                "p_enter": rec.p_enter, "did_enter": int(rec.did_enter), "skip_reason": rec.skip_reason,
                "planner_version": rec.planner_version})
            if not intent.attempt:
                continue
            entered += 1
            slots = {k: 0 for k in PoolKind}
            spent = 0
            while spent < intent.max_spend_pulls:
                stage = G.next_stage(plan, ps.roster)
                if stage is None:
                    break
                res = self._one_pull(ps, period, banner, stage, slots[stage.pool_kind])
                if res is None:
                    break
                bundle, ann = res
                slots[stage.pool_kind] += 1
                spent += 1
                pulls += 1
                topups += len(bundle.cash_events)
                deltas += bundle.resource_deltas
                cash += bundle.cash_events
            if G.next_stage(plan, ps.roster) is not None:
                partial += 1

        # 本期钱包逐批次守恒（期初 roll 之后到期末）
        W.check_conservation(wallet_start, ps.wallet, deltas)

        # ---- 5. PostEval
        ps.roster = R.update_disc_values(ps.roster, {
            c.unit_id: self.model.params.gear(day, c.first_acquired_day)
            for c in ps.roster.characters
        })
        post_evals = self.eval.anchors(ps.roster, period.scenario, day)
        st_post, ci_post, _ = Evaluator.best(post_evals)
        self._write_panel(ps, period, "post", post_evals, True)
        if st_post is not EvalStatus.OK:
            raise SimulationError(f"PostEval 不可评估（{st_post.value}），停止依赖它的生命周期更新")
        if ps.spending is not None and topups > 0:
            ps.spending = replace(ps.spending, last_paid_failure=ci_post < 1.0)

        # ---- 6. 生命周期（每期一次）、流失、冻结
        u_churn = self.rng.churn_u(ps.player_id, t)
        prev = ps.lifecycle
        step = LC.step(self.lc, prev, ci_post=ci_post, elapsed_days=period.elapsed_days,
                       topup_pulls_used=topups, paid_cap=paid_cap, u_churn=u_churn, exit_day=day)
        ps.lifecycle = step.state
        if step.churned:
            ps.roster = R.freeze(ps.roster)
            frozen = {"characters": [[c.unit_id, c.cinema, c.first_acquired_day, c.gear_value]
                                     for c in ps.roster.characters],
                      "weapons": [[w.weapon_id, w.count] for w in ps.roster.weapons],
                      "categories": [[k.category_id, k.count] for k in ps.roster.categories]}
            self._w("fact_frozen_state", {"run_id": self.run_id, "player_id": ps.player_id, "churn_period": t,
                                          "freeze_day": day, "freeze_build_hash": sha256_hex(frozen),
                                          "frozen_state_json": canonical_json(frozen)})
        self._finish_period_rows(ps, period, ci_pre, ci_post, deltas, cash, paid_cap,
                                 (prev, step, topups, active_start))
        period_deltas_out.extend(deltas)
        return PeriodSummary(ps.player_id, t, True, not step.churned, ci_pre, ci_post, pulls, topups,
                             sum(e.amount_fen for e in cash), decisions, entered, partial)

    def _cohort(self, ps: PlayerState, period: Period, bundle: EventBundle) -> None:
        for a in bundle.asset_deltas:
            if a.asset_kind.value == "character" and bundle.roster_before.character(a.asset_id) is None:
                self._w("fact_unit_cohort", {"run_id": self.run_id, "unit_id": a.asset_id,
                                             "acquisition_period": period.t_idx, "player_id": ps.player_id,
                                             "acquisition_day": a.day,
                                             "cohort_id": f"{a.asset_id}:t{period.t_idx:02d}"})

    def _finish_period_rows(self, ps: PlayerState, period: Period, ci_pre, ci_post, deltas, cash,
                            paid_cap: int, lc_info) -> None:
        t = period.t_idx
        self._wallet_rows(ps, t, "end")
        self._pity_rows(ps, t, "end")
        # 期末持有
        for c in ps.roster.characters:
            self._w("fact_ownership", {"run_id": self.run_id, "player_id": ps.player_id, "t_idx": t,
                                       "asset_kind": "character", "asset_id": c.unit_id,
                                       "first_acquired_day": c.first_acquired_day, "copies": c.cinema + 1,
                                       "cinema": c.cinema, "has_weapon": int(c.equipped_signature),
                                       "disc_actual": c.gear_value,
                                       "is_combat_eligible": int(self.world.units.get(c.unit_id) is not None)})
        for w_ in ps.roster.weapons:
            self._w("fact_ownership", {"run_id": self.run_id, "player_id": ps.player_id, "t_idx": t,
                                       "asset_kind": "weapon", "asset_id": w_.weapon_id,
                                       "first_acquired_day": w_.first_acquired_day, "copies": w_.count,
                                       "cinema": None, "has_weapon": None, "disc_actual": None,
                                       "is_combat_eligible": 1})
        for k in ps.roster.categories:
            self._w("fact_ownership", {"run_id": self.run_id, "player_id": ps.player_id, "t_idx": t,
                                       "asset_kind": "category", "asset_id": k.category_id,
                                       "first_acquired_day": None, "copies": k.count, "cinema": None,
                                       "has_weapon": None, "disc_actual": None, "is_combat_eligible": 0})
        usage = W.summarize_usage(self.policy, deltas)
        rev = W.revenue(cash)
        ending_free = sum(b.credit_minor for b in ps.wallet.batches
                          if b.source in (CreditSource.CURRENT_FREE, CreditSource.HISTORICAL_FREE))
        ending_sub = sum(b.credit_minor for b in ps.wallet.batches if b.source is CreditSource.SUBSCRIPTION)
        if usage.topup_pulls_used.denominator != 1 or usage.total.denominator != 1:
            raise SimulationError("四来源抽数合计必须为整数")
        lcs = ps.lifecycle
        if lc_info is None:
            row = dict(active_start=0, active_end=0, churn=0, h_before=lcs.hoarding, h_after=lcs.hoarding,
                       F_before=lcs.fatigue, F_after=lcs.fatigue, p_churn=None, util=None, paid_used=0)
        else:
            prev, step, topups, _ = lc_info
            row = dict(active_start=1, active_end=int(not step.churned), churn=int(step.churned),
                       h_before=step.h_before, h_after=lcs.hoarding, F_before=step.f_before,
                       F_after=lcs.fatigue, p_churn=step.p_churn, util=step.budget_utilization,
                       paid_used=topups)
        self._w("fact_player_lifecycle", {
            "run_id": self.run_id, "player_id": ps.player_id, "t_idx": t, "player_tier": ps.tier_id,
            "active_start": row["active_start"], "active_end": row["active_end"],
            "churn_this_period": row["churn"], "CI_pre": ci_pre, "CI_post": ci_post,
            "is_full_star_post": None if ci_post is None else int(CombatResult.threshold_met(ci_post)),
            "h_before": row["h_before"], "h_after": row["h_after"], "F_before": row["F_before"],
            "F_after": row["F_after"], "failure_span": lcs.failure_span, "paid_cap": paid_cap,
            "paid_used": row["paid_used"], "budget_utilization_rate": row["util"], "p_churn": row["p_churn"]})
        self._w("agg_player_period", {
            "run_id": self.run_id, "player_id": ps.player_id, "t_idx": t, "player_tier": ps.tier_id,
            "active_start": row["active_start"], "active_end": row["active_end"], "CI_pre": ci_pre,
            "CI_post": ci_post, "sub_revenue_fen": rev.sub_revenue_fen, "topup_revenue_fen": rev.topup_revenue_fen,
            "total_pulls": int(usage.total), "curr_free_used": float(usage.curr_free_used),
            "hoarded_free_used": float(usage.hoarded_free_used), "sub_pulls_used": float(usage.sub_pulls_used),
            "topup_pulls_used": int(usage.topup_pulls_used), "ending_free_minor": ending_free,
            "ending_sub_minor": ending_sub})

    # ------------------------------------------------------------------ 维度
    def _write_dims(self) -> None:
        if self.writer is None:
            return
        wd = self.writer.write_dim
        wd("dim_period", [{"timeline_id": self.world.timeline_id, "period_id": p.period_id, "t_idx": p.t_idx,
                           "start_day": p.start_day, "end_day": p.end_day, "evaluation_day": p.evaluation_day,
                           "elapsed_days": p.elapsed_days, "actual_start_date": None, "actual_end_date": None,
                           "calendar_mode": "synthetic_relative"} for p in self.world.periods])
        wd("dim_unit", [{"catalog_id": self.world.catalog_id, "unit_id": u.unit_id, "role": u.role,
                         "debut_period": u.debut_t, "debut_day": u.debut_day, "B_ref": u.b_ref,
                         "pierce_share": u.pierce_share, "weapon_id": signature_weapon_id(u.unit_id),
                         "base_support_gain": self.model.params.support_gains.get(u.unit_id),
                         "combat_eligible": 1} for u in sorted(self.world.units.values(), key=lambda u: u.unit_id)])
        pools = {k: self.gacha.pool(k).pity_group for k in PoolKind}
        wd("dim_banner", [{"catalog_id": self.world.catalog_id, "banner_id": b.banner_id,
                           "period_id": p.period_id, "event_order": b.event_order, "target_unit_id": b.unit_id,
                           "target_weapon_id": signature_weapon_id(b.unit_id),
                           "char_pity_group": pools[PoolKind.CHARACTER], "wep_pity_group": pools[PoolKind.WEAPON],
                           "allowed_target_json": canonical_json(sorted(self.gacha.target_paths))}
                          for p in self.world.periods for b in p.banners])
        pr = self.model.params
        wd("dim_combat_scenario", [{"scenario_id": p.scenario.scenario_id, "combat_config_id": pr.config_digest,
                                    "period_id": p.period_id, "hp": p.scenario.hp, "base_def": pr.reference_defense,
                                    "shield_def_bonus": pr.shield_defense_bonus,
                                    "shield_dr": pr.shield_damage_reduction, "theta": p.scenario.theta,
                                    "kappa": p.scenario.kappa, "uptime": p.scenario.uptime,
                                    "support_gain_scale": p.scenario.support_gain_scale,
                                    "source_kind": "scenario_assumed"} for p in self.world.periods])

    # ------------------------------------------------------------------ 运行
    def algorithm_versions(self) -> dict[str, str]:
        v = dict(self.cfg.algorithm_versions)
        v["storage"] = STORAGE_VERSION
        return v

    def run(self, player_ids: Optional[Iterable[int]] = None) -> SimulationResult:
        n = self.cfg.module("population")["n_players"]
        ids = list(range(n)) if player_ids is None else list(player_ids)
        if sorted(ids) != list(range(n)):
            raise SimulationError("player_ids 必须是 0…n_players−1 的一个排列")
        timings: list[tuple[str, float, Optional[int]]] = []
        if self.writer is not None:
            self.writer.start_run(
                run_id=self.run_id, experiment_id=self.identity.experiment_id, config_id=self.cfg.config_id,
                replicate_id=self.identity.replicate_id, master_seed=self.identity.master_seed,
                rng_version=self.rng.params.rng_version, algorithm_versions=self.algorithm_versions(),
                config_science=deep_thaw(self.cfg.science),
                input_manifest_hash=sha256_hex(deep_thaw(self.cfg.input_digests)),
                environment={"python": platform.python_version(), "platform": platform.platform()})
        try:
            t0 = time.perf_counter()
            self._write_dims()
            players: dict[int, PlayerState] = {}
            summaries: list[PeriodSummary] = []
            first = self.world.periods[0]
            for pid in ids:  # 玩家相互独立
                ps, init = self.init_player(pid)
                if self.writer is not None:
                    self.writer.write_rows("fact_asset_event", [{
                        "run_id": self.run_id, "asset_event_id": a.event_id, "player_id": pid,
                        "period_id": first.period_id, "event_day": a.day, "banner_id": None,
                        "pull_event_id": None, "asset_kind": a.asset_kind.value, "asset_id": a.asset_id,
                        "event_kind": "initial", "copies_delta": a.quantity} for a in init])
                for period in self.world.periods:
                    summaries.append(self.step_period(ps, period, []))
                players[pid] = ps
            timings.append(("simulate", time.perf_counter() - t0, len(summaries)))
            summaries.sort(key=lambda s: (s.player_id, s.t_idx))
            if self.writer is not None:
                self.writer.finish_run(status="completed", stage_timings=timings)
        except Exception as exc:
            if self.writer is not None:
                self.writer.finish_run(status="failed", error_code=type(exc).__name__)
            raise
        return SimulationResult(self.run_id, self.cfg.config_id, self.identity.master_seed, self.world,
                                players, summaries, timings)


def run_simulation(cfg: ResolvedConfig, *, master_seed: int, writer: Optional[SQLiteWriter] = None,
                   player_order: Optional[Iterable[int]] = None) -> SimulationResult:
    return Runtime(cfg, master_seed, writer).run(player_order)
