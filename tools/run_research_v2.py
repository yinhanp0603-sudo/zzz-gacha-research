"""固定队列的机制锁 × 售后支援实验；复用已完成运行，不读取或导入公开战斗原始数据。"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzz_research.analytics import reference as REF
from zzz_research.analytics.search import nondominated, spearman
from zzz_research.config import resolve_config
from zzz_research.pipeline import from_project, project_path, simulate, write_csv, write_json
from zzz_research.provenance import build_parameter_registry
from zzz_research.report import line_chart


@dataclass(frozen=True)
class RunSpec:
    policy_id: str
    mechanism_strength: float
    support_gain_scale: float
    combat_variant: str
    spending_variant: str
    horizon: int
    seed: int
    split: str
    log_mode: str

    @property
    def cell_id(self) -> str:
        return f"{self.policy_id}__combat-{self.combat_variant}__spending-{self.spending_variant}"

    def overrides(self, players: int) -> dict[str, Any]:
        result = {
            "population.n_players": players,
            "scenarios.research_v2.observation_periods": self.horizon,
            "combat.research_v2.mechanism_strength": self.mechanism_strength,
            "combat.support.default_gain_scale": self.support_gain_scale,
        }
        if self.combat_variant == "v1":
            result.update({"combat.model_version": "proxy_v1",
                           "combat.shield.defense_bonus": .8 * self.mechanism_strength,
                           "combat.shield.damage_reduction": .25 * self.mechanism_strength})
        if self.spending_variant == "v1":
            result["behavior.model_version"] = "behavior_v1"
        return result


def build_plan(cfg: Any, *, seeds: list[int], horizons: list[int], suite: str) -> list[RunSpec]:
    grid = cfg.module("experiments")["grid"]
    lambdas = list(grid["mechanism_strength"])
    gains = list(grid["support_gain_scale"])
    baseline = cfg.module("experiments")["baseline_grid_point"]
    base = (float(baseline["mechanism_strength"]), float(baseline["support_gain_scale"]))
    corners = {(float(l), float(a)) for l in (min(lambdas), max(lambdas))
               for a in (min(gains), max(gains))}
    pairs = [(float(l), float(a)) for l in lambdas for a in gains]
    if suite == "smoke":
        pairs = [p for p in pairs if p in corners or p == base]
    meta = cfg.metadata["module_metadata"]["experiments"]
    training = set(meta["train_seeds"])
    validation = set(meta["validation_seeds"])
    out = []
    for horizon in horizons:
        for l, a in pairs:
            variants = [("v2", "v2")]
            if (l, a) == base:
                variants.extend([("v1", "v1"), ("v1", "v2"), ("v2", "v1")])
            for combat, spending in variants:
                for seed in seeds:
                    split = "train" if seed in training else "validation" if seed in validation else "exploratory"
                    out.append(RunSpec(f"L{l:g}_A{a:g}", l, a, combat, spending,
                                       horizon, seed, split,
                                       "full" if (l, a) in corners or (l, a) == base else "summary"))
    return out


@contextmanager
def _open(db: Path):
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def _rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in con.execute(sql, params)]


def _ratio(n: float, d: float) -> float | None:
    return n / d if d else None


def _mean(xs: list[float | None]) -> float | None:
    values = [x for x in xs if x is not None]
    return statistics.mean(values) if values else None


def run_one(entry: str, out: str, players: int, spec_dict: dict) -> dict:
    spec = RunSpec(**spec_dict)
    overrides = spec.overrides(players)
    cfg = resolve_config(entry, overrides=overrides)
    directory = Path(out) / "runs" / f"h{spec.horizon}" / spec.cell_id
    name = cfg.module("experiments")["base_scenario"]
    db = directory / f"{name}_seed{spec.seed}.sqlite"
    reused = False
    started = time.perf_counter()
    if db.exists():
        with _open(db) as con:
            manifest = _rows(con, "SELECT run_id, config_id, master_seed, status FROM run_manifest")
            mode = con.execute("SELECT log_mode FROM run_attempt ORDER BY attempt_id DESC LIMIT 1").fetchone()
        if (len(manifest) != 1 or manifest[0]["config_id"] != cfg.config_id
                or manifest[0]["master_seed"] != spec.seed or manifest[0]["status"] != "completed"
                or mode is None or mode[0] != spec.log_mode):
            raise RuntimeError(f"已有运行与本次实验不一致，请使用独立 --out 目录：{db}")
        run_id = manifest[0]["run_id"]
        reused = True
    else:
        result = simulate(entry, directory, seed=spec.seed, n_players=players,
                          log_mode=spec.log_mode, extra_overrides=overrides)
        run_id = result["run_id"]
    return {**asdict(spec), "cell_id": spec.cell_id, "config_id": cfg.config_id,
            "run_id": run_id, "database": project_path(db), "reused": reused,
            "wall_seconds": time.perf_counter() - started}


def read_outcomes(result: dict) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """从运行日志读取固定初始队列的现金、活跃、付费意愿与老资产结果。"""
    keys = ("cell_id", "policy_id", "mechanism_strength", "support_gain_scale", "combat_variant",
            "spending_variant", "horizon", "seed", "split", "config_id", "run_id", "log_mode")
    identity = {k: result[k] for k in keys}
    with _open(from_project(result["database"])) as con:
        config_json = json.loads(con.execute("SELECT config_json FROM run_manifest WHERE run_id=?",
                                             (result["run_id"],)).fetchone()[0])
        core = config_json["modules"]["population"]["core_retention_tier"]
        per = _rows(con, """SELECT t_idx, COUNT(*) AS initial_players,
            SUM(active_end) AS active_end, SUM(CASE WHEN player_tier=? THEN 1 ELSE 0 END) AS core_initial,
            SUM(CASE WHEN player_tier=? THEN active_end ELSE 0 END) AS core_active_end,
            SUM(sub_revenue_fen) AS sub_fen, SUM(topup_revenue_fen) AS topup_fen,
            SUM(CASE WHEN sub_revenue_fen>0 THEN 1 ELSE 0 END) AS subscription_payers,
            SUM(CASE WHEN topup_revenue_fen>0 THEN 1 ELSE 0 END) AS topup_payers,
            SUM(CASE WHEN active_end=1 AND CI_post>=1 THEN 1 ELSE 0 END) AS active_full_star_players,
            SUM(total_pulls) AS total_pulls, SUM(curr_free_used) AS curr_free_used,
            SUM(hoarded_free_used) AS hoarded_free_used, SUM(sub_pulls_used) AS sub_pulls_used,
            SUM(topup_pulls_used) AS topup_pulls_used, SUM(ending_free_minor) AS ending_free_minor,
            SUM(ending_sub_minor) AS ending_sub_minor
            FROM agg_player_period WHERE run_id=? GROUP BY t_idx ORDER BY t_idx""",
            (core, core, result["run_id"]))
        cumulative_cash = cumulative_sub = cumulative_topup = 0
        periods = []
        for p in per:
            cumulative_sub += p["sub_fen"]
            cumulative_topup += p["topup_fen"]
            cumulative_cash = cumulative_sub + cumulative_topup
            periods.append({**identity, **p,
                "retention_all": _ratio(p["active_end"], p["initial_players"]),
                "retention_core": _ratio(p["core_active_end"], p["core_initial"]),
                "cash_yuan": (p["sub_fen"] + p["topup_fen"]) / 100,
                "cumulative_cash_yuan": cumulative_cash / 100,
                "cumulative_sub_yuan": cumulative_sub / 100,
                "cumulative_topup_yuan": cumulative_topup / 100})
        spending = _rows(con, """SELECT s.t_idx, s.decision_kind, COUNT(*) AS decisions,
            SUM(s.allowed) AS allowed_decisions, AVG(s.probability) AS mean_probability,
            SUM(CASE WHEN s.capacity>0 THEN 1 ELSE 0 END) AS eligible_decisions,
            SUM(CASE WHEN s.capacity>0 THEN s.allowed ELSE 0 END) AS eligible_allowed_decisions,
            AVG(s.prior_F) AS prior_fatigue, AVG(s.prior_failure_span) AS prior_failure_span,
            SUM(CASE WHEN s.capacity>0 AND s.allowed=0 AND p.active_end=1 THEN 1 ELSE 0 END) AS stopped_spending_still_active_events,
            COUNT(DISTINCT CASE WHEN s.capacity>0 AND s.allowed=0 AND p.active_end=1 THEN s.player_id END) AS stopped_spending_still_active_players
            FROM fact_spending_decision s JOIN agg_player_period p
            ON s.run_id=p.run_id AND s.player_id=p.player_id AND s.t_idx=p.t_idx
            WHERE s.run_id=? GROUP BY s.t_idx,s.decision_kind ORDER BY 1,2""", (result["run_id"],))
        spending = [{**identity, **s, "allowed_share": _ratio(s["allowed_decisions"], s["decisions"]),
                     "eligible_allowed_share": _ratio(s["eligible_allowed_decisions"], s["eligible_decisions"])}
                    for s in spending]
        assets = []
        if result["log_mode"] == "full":
            asset_rows = _rows(con, """SELECT p.player_id,p.unit_id,p.t_idx,p.eval_phase,p.is_full_star,
                p.active_start,p.has_active_support,p.is_deployed_main,p.clear_index,
                u.debut_period,a.active_end AS holder_active_end
                FROM fact_player_roster_panel p JOIN dim_unit u ON p.unit_id=u.unit_id
                JOIN agg_player_period a ON p.run_id=a.run_id AND p.player_id=a.player_id AND p.t_idx=a.t_idx
                WHERE p.run_id=? AND ((p.eval_phase='post' AND p.active_start=1)
                  OR p.eval_phase='frozen_post') AND u.role!='support'
                ORDER BY p.player_id,p.unit_id,p.t_idx""", (result["run_id"],))
            grouped: dict[tuple, list[dict]] = defaultdict(list)
            for row in asset_rows:
                grouped[(row["player_id"], row["unit_id"])].append(row)
            for (player_id, unit_id), rows in grouped.items():
                failures = [r for r in rows if r["is_full_star"] == 0]
                first_fail = failures[0]["t_idx"] if failures else None
                actual_failures = [r for r in failures if r["active_start"]]
                recovery = [r for r in rows if first_fail is not None and r["t_idx"] > first_fail
                            and r["is_full_star"] == 1]
                assets.append({**identity, "player_id": player_id, "unit_id": unit_id,
                    "debut_period": rows[0]["debut_period"], "first_observed_period": rows[0]["t_idx"],
                    "last_observed_period": rows[-1]["t_idx"], "observed_periods": len(rows),
                    "first_failure_period": first_fail, "first_failure_event": first_fail is not None,
                    "first_failure_context": ("frozen_counterfactual" if failures[0]["eval_phase"] == "frozen_post" else "active_actual") if failures else None,
                    "actual_first_failure_period": actual_failures[0]["t_idx"] if actual_failures else None,
                    "first_recovery_period": recovery[0]["t_idx"] if recovery else None,
                    "full_star_periods": sum(r["is_full_star"] == 1 for r in rows),
                    "active_full_star_periods": sum(r["is_full_star"] == 1 and r["active_start"] for r in rows),
                    "supported_periods": sum(r["has_active_support"] for r in rows),
                    "deployed_periods": sum(r["is_deployed_main"] for r in rows),
                    "final_counterfactual_full_star": rows[-1]["is_full_star"],
                    "final_holder_active": rows[-1]["holder_active_end"]})
            maintenance = con.execute("""SELECT COUNT(*),COALESCE(SUM(c.amount_fen),0)
                FROM fact_pull_event p JOIN dim_banner b ON p.banner_id=b.banner_id
                JOIN dim_unit u ON b.target_unit_id=u.unit_id
                LEFT JOIN fact_cash_event c ON p.run_id=c.run_id AND p.paid_cash_event_id=c.cash_event_id
                WHERE p.run_id=? AND u.role='support'""", (result["run_id"],)).fetchone()
        else:
            maintenance = (None, None)
        timing = _rows(con, "SELECT stage,SUM(wall_seconds) AS seconds FROM stage_timing WHERE run_id=? GROUP BY stage",
                       (result["run_id"],))
    last = periods[-1]
    summary = {**identity, "database": result["database"], "reused": result["reused"],
        "players": last["initial_players"], "core_initial": last["core_initial"],
        "retention_all": last["retention_all"], "retention_core": last["retention_core"],
        "cash_yuan": cumulative_cash / 100, "subscription_yuan": cumulative_sub / 100,
        "topup_yuan": cumulative_topup / 100,
        "cash_per_initial_player_yuan": cumulative_cash / 100 / last["initial_players"],
        "total_pulls": sum(p["total_pulls"] for p in periods),
        "hoarded_free_used": sum(p["hoarded_free_used"] for p in periods),
        "final_free_resource_minor": last["ending_free_minor"],
        "final_subscription_resource_minor": last["ending_sub_minor"],
        "support_banner_pulls": maintenance[0],
        "support_banner_topup_yuan": maintenance[1] / 100 if maintenance[1] is not None else None,
        "simulation_seconds": sum(t["seconds"] for t in timing),
        "observed_asset_units": len(assets) if result["log_mode"] == "full" else None,
        "recovered_assets": sum(a["first_recovery_period"] is not None for a in assets)
                            if result["log_mode"] == "full" else None}
    return summary, periods, spending, assets


def build_reference_panels(entry: Path, out: Path, players: int, plan: list[RunSpec]) -> None:
    """L2 主体逐条保留失效、恢复与删失；配对不受购买和退坑影响。"""
    seen = set()
    panels, subjects = [], []
    for spec in plan:
        key = (spec.policy_id, spec.combat_variant, spec.horizon)
        if key in seen:
            continue
        seen.add(key)
        cfg = resolve_config(entry, overrides=spec.overrides(players))
        bid, _, observations = REF.evaluate_trajectories(cfg)
        grouped: dict[str, list[Any]] = defaultdict(list)
        common = {"policy_id": spec.policy_id, "mechanism_strength": spec.mechanism_strength,
                  "support_gain_scale": spec.support_gain_scale, "combat_variant": spec.combat_variant,
                  "horizon": spec.horizon, "benchmark_id": bid, "config_id": cfg.config_id}
        for o in observations:
            s = o.subject
            grouped[s.reference_subject_id].append(o)
            panels.append({**common, "reference_subject_id": s.reference_subject_id,
                "anchor_unit_id": s.anchor_unit_id, "investment_plan": s.investment_plan,
                "gear_policy": s.gear_policy, "support_policy": s.support_policy,
                "t_idx": o.t_idx, "age_days": o.age_days, "clear_index": o.ci,
                "is_full_star": o.is_full_star, "support_active": o.support_active,
                "eval_status": o.eval_status})
        for sid, obs in grouped.items():
            obs.sort(key=lambda o: o.t_idx)
            s = obs[0].subject
            failed = [o for o in obs if o.ci is not None and o.ci < 1]
            first = failed[0] if failed else None
            recovered = [o for o in obs if first and o.t_idx > first.t_idx and o.ci is not None and o.ci >= 1]
            subjects.append({**common, "reference_subject_id": sid, "anchor_unit_id": s.anchor_unit_id,
                "investment_plan": s.investment_plan, "gear_policy": s.gear_policy,
                "support_policy": s.support_policy, "debut_period": s.debut_t,
                "first_failure_event": first is not None,
                "first_failure_or_censor_days": first.age_days if first else obs[-1].age_days,
                "first_recovery_age_days": recovered[0].age_days if recovered else None,
                "full_star_periods": sum(o.is_full_star is True for o in obs),
                "observed_periods": len(obs), "final_clear_index": obs[-1].ci})
    lookup = {(r["policy_id"], r["combat_variant"], r["horizon"], r["anchor_unit_id"],
               r["investment_plan"], r["gear_policy"], r["support_policy"]): r for r in subjects}
    paired = []
    for row in subjects:
        if row["support_policy"] == "none":
            continue
        key = (row["policy_id"], row["combat_variant"], row["horizon"], row["anchor_unit_id"],
               row["investment_plan"], row["gear_policy"], "none")
        natural = lookup[key]
        paired.append({**row, "natural_first_failure_event": natural["first_failure_event"],
            "natural_first_failure_or_censor_days": natural["first_failure_or_censor_days"],
            "natural_full_star_periods": natural["full_star_periods"],
            "incremental_full_star_periods": row["full_star_periods"] - natural["full_star_periods"],
            "paired_final_clear_index_gain": (row["final_clear_index"] - natural["final_clear_index"]
                if row["final_clear_index"] is not None and natural["final_clear_index"] is not None else None),
            "first_failure_gain_days_both_events": (row["first_failure_or_censor_days"] - natural["first_failure_or_censor_days"]
                if row["first_failure_event"] and natural["first_failure_event"] else None)})
    write_csv(out / "tables" / "l2_reference_panel.csv", panels)
    write_csv(out / "tables" / "l2_reference_lifetimes.csv", subjects)
    write_csv(out / "tables" / "l2_support_pair_effects.csv", paired)


def summarize_grid(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["horizon"], r["split"], r["cell_id"])].append(r)
    summaries = []
    for (h, split, cell), group in sorted(groups.items()):
        summaries.append({"horizon": h, "split": split, "cell_id": cell,
            **{k: group[0][k] for k in ("policy_id", "mechanism_strength", "support_gain_scale",
                                       "combat_variant", "spending_variant")},
            "n_seeds": len(group),
            **{k: _mean([r[k] for r in group]) for k in
               ("cash_yuan", "subscription_yuan", "topup_yuan", "retention_all", "retention_core",
                "cash_per_initial_player_yuan", "total_pulls", "hoarded_free_used")}})
    for h in sorted({r["horizon"] for r in summaries}):
        for split in sorted({r["split"] for r in summaries if r["horizon"] == h}):
            cells = [r for r in summaries if r["horizon"] == h and r["split"] == split
                     and r["combat_variant"] == r["spending_variant"] == "v2"]
            frontier = nondominated({r["cell_id"]: (r["cash_yuan"], r["retention_core"])
                                    for r in cells if r["retention_core"] is not None})
            for r in cells:
                r["mean_nondominated"] = r["cell_id"] in frontier
                eligible = [x for x in rows if x["horizon"] == h and x["split"] == split
                            and x["combat_variant"] == x["spending_variant"] == "v2"]
                included = 0
                seeds = sorted({x["seed"] for x in eligible})
                for seed in seeds:
                    front = nondominated({x["cell_id"]: (x["cash_yuan"], x["retention_core"])
                                          for x in eligible if x["seed"] == seed and x["retention_core"] is not None})
                    included += r["cell_id"] in front
                r["inclusion_frequency"] = included / len(seeds)
    stability = []
    for h in sorted({r["horizon"] for r in summaries}):
        modern = [r for r in summaries if r["horizon"] == h
                  and r["combat_variant"] == r["spending_variant"] == "v2"]
        train = {r["cell_id"]: r for r in modern if r["split"] == "train"}
        valid = {r["cell_id"]: r for r in modern if r["split"] == "validation"}
        matched = sorted(set(train) & set(valid))
        if matched:
            retention_matched = [k for k in matched if train[k]["retention_core"] is not None
                                 and valid[k]["retention_core"] is not None]
            stability.append({"horizon": h, "matched_candidates": len(matched),
                "retention_rankable_candidates": len(retention_matched),
                "train_seeds": train[matched[0]]["n_seeds"], "validation_seeds": valid[matched[0]]["n_seeds"],
                "rho_revenue": spearman([train[k]["cash_yuan"] for k in matched], [valid[k]["cash_yuan"] for k in matched]),
                "rho_retention": spearman([train[k]["retention_core"] for k in retention_matched],
                                          [valid[k]["retention_core"] for k in retention_matched])})
    return summaries, stability


def write_parameter_registry(cfg: Any, out: Path) -> None:
    rows = []
    for record in build_parameter_registry(cfg):
        row = {"config_id": cfg.config_id, **asdict(record)}
        row["source_kind"] = record.source_kind.value
        name = record.parameter_name
        if name.startswith("combat.research_v2."):
            row["source_kind"] = "scenario_assumed"
            row["source_ref"] = "config/research_v2/modules/combat.json"
            row["rationale"] = "机制惩罚、适配与配对为显式情景参数，不从公开成绩比识别因果值。"
            if name.endswith("net_gain"):
                row["source_ref"] += "; outputs/final_release/empirical_knob2_support_lift.csv"
                row["status"] = "observationally_anchored_scenario"
                row["rationale"] = "配对净增益参考专拐相对可用替代辅助的条件成绩范围；不是伤害比或因果增益估计。"
            elif ".mechanisms." in name:
                row["source_ref"] += "; outputs/final_release/empirical_knob1_boss_lock.csv"
                row["rationale"] = "输出窗口锁与数值壁垒采用未拟合的情景系数；公开机制成绩只提供业务分类参照。"
        elif name.startswith("behavior.research_v2."):
            row["source_kind"] = "scenario_assumed"
            row["source_ref"] = "config/research_v2/modules/behavior.json"
            row["rationale"] = "公开成功战斗记录不含续订、直购意愿与疲劳，不能估计这些系数。"
        elif name.startswith("scenarios.research_v2."):
            row["source_kind"] = "scenario_assumed"
            row["source_ref"] = "config/research_v2/modules/scenarios.json"
        rows.append(row)
    write_csv(out / "tables/parameter_registry.csv", rows)


def build_figures(out: Path, summaries: list[dict], periods: list[dict]) -> list[str]:
    figures = out / "figures"
    figures.mkdir(exist_ok=True)
    names = []
    modern = [r for r in summaries if r["combat_variant"] == r["spending_variant"] == "v2"]
    screening = [r for r in modern if r["split"] == "train"] or modern
    for metric, name, title, scale, unit in (
        ("retention_core", "mechanism_retention", "机制加重后，核心玩家留下多少？", 100, "核心层期末留存（%）"),
        ("cash_yuan", "mechanism_cash", "机制加重后，固定队列贡献多少现金？", .0001, "累计现金（万元）")):
        series = {}
        for h in sorted({r["horizon"] for r in screening}):
            by_lock: dict[float, list[float | None]] = defaultdict(list)
            for r in screening:
                if r["horizon"] == h:
                    by_lock[r["mechanism_strength"]].append(r[metric])
            series[f"{h}期"] = [(l, value * scale if (value := _mean(values)) is not None else None)
                                  for l, values in sorted(by_lock.items())]
        svg = line_chart(series, title=title, xlabel="机制锁强度 λ", ylabel=unit,
                         subtitle="每个 λ 对本轮运行的支援档位取等权平均；线条区分观察期", ymin=0)
        (figures / f"{name}.svg").write_text(svg, encoding="utf-8")
        names.append(name)
    horizon = max(r["horizon"] for r in periods)
    base_periods = [r for r in periods if r["horizon"] == horizon and r["mechanism_strength"] == 1
                    and r["support_gain_scale"] == 1 and r["split"] == "train"]
    for metric, name, title, scale, unit in (
        ("retention_core", "ablation_retention", "战斗与付费回路：哪一部分改变了留存？", 100, "核心层留存（%）"),
        ("cumulative_cash_yuan", "ablation_cash", "战斗与付费回路：哪一部分改变了现金？", .0001, "累计现金（万元）")):
        by_variant: dict[tuple, dict[int, list[float | None]]] = defaultdict(lambda: defaultdict(list))
        for row in base_periods:
            by_variant[(row["combat_variant"], row["spending_variant"])][row["t_idx"]].append(row[metric])
        series = {f"战斗{c}/付费{s}": [(t, val * scale if (val := _mean(vs)) is not None else None)
                                     for t, vs in sorted(points.items())]
                  for (c, s), points in sorted(by_variant.items())}
        if series:
            svg = line_chart(series, title=title, xlabel="观察期（每期42天）", ylabel=unit,
                             subtitle="同一商品目录，基准 λ=1、a=1；四种模块组合", ymin=0)
            (figures / f"{name}.svg").write_text(svg, encoding="utf-8")
            names.append(name)
    return names


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def write_report(out: Path, manifest: dict, rows: list[dict], summaries: list[dict], stability: list[dict]) -> None:
    modern = [r for r in summaries if r["combat_variant"] == r["spending_variant"] == "v2"]
    train_rows = [r for r in modern if r["split"] == "train"] or modern
    horizon = max(manifest["horizons"])
    longest = [r for r in train_rows if r["horizon"] == horizon]
    low_lock = min(r["mechanism_strength"] for r in longest)
    high_lock = max(r["mechanism_strength"] for r in longest)
    low_gain = min(r["support_gain_scale"] for r in longest)
    high_gain = max(r["support_gain_scale"] for r in longest)
    findings = []
    cash_by_horizon = []
    for h in sorted(manifest["horizons"]):
        observed = [r for r in train_rows if r["horizon"] == h]
        low_cash = _mean([r["cash_yuan"] for r in observed if r["mechanism_strength"] == low_lock])
        high_cash = _mean([r["cash_yuan"] for r in observed if r["mechanism_strength"] == high_lock])
        if low_cash:
            cash_by_horizon.append(f"{h} 期累计现金变化 {(high_cash/low_cash-1)*100:+.1f}%")
    if len(cash_by_horizon) > 1:
        findings.append(f"- **机制锁的现金效应随观察期变化**：筛选样本中，λ 从 {low_lock:g} 到 {high_lock:g}，对支援档位取等权平均后，" + "；".join(cash_by_horizon) + "。")
    for axis, low, high, label in (("mechanism_strength", low_lock, high_lock, "机制锁 λ"),
                                   ("support_gain_scale", low_gain, high_gain, "专拐净增益 a")):
        first = [r for r in longest if r[axis] == low]
        last = [r for r in longest if r[axis] == high]
        rev0, rev1 = _mean([r["cash_yuan"] for r in first]), _mean([r["cash_yuan"] for r in last])
        ret0, ret1 = _mean([r["retention_core"] for r in first]), _mean([r["retention_core"] for r in last])
        if ret0 is not None and ret1 is not None:
            cash_change = f"{(rev1/rev0-1)*100:+.1f}%" if rev0 else "基期现金为零"
            findings.append(f"- **{label} 从 {low:g} 到 {high:g}**：对另一个旋钮的已运行档位取均值，{horizon} 期核心层留存从 {ret0:.1%} 变为 {ret1:.1%}（{(ret1-ret0)*100:+.1f} 个百分点）；累计现金从 ¥{rev0:,.0f} 变为 ¥{rev1:,.0f}（{cash_change}）。")
    base_long = next((r for r in longest if r["mechanism_strength"] == 1 and r["support_gain_scale"] == 1), None)
    base_short = next((r for r in train_rows if r["horizon"] == min(manifest["horizons"])
                       and r["mechanism_strength"] == 1 and r["support_gain_scale"] == 1), None)
    if base_long and base_short and base_long["horizon"] != base_short["horizon"]:
        extra = base_long["cash_yuan"] - base_short["cash_yuan"]
        findings.append(f"- **第二轮周期仍需独立观察**：λ=1、a=1 时，前 {base_short['horizon']} 期现金 ¥{base_short['cash_yuan']:,.0f}，延长至 {horizon} 期累计 ¥{base_long['cash_yuan']:,.0f}，后续窗口新增 ¥{extra:,.0f}；核心层期末留存由 {_percent(base_short['retention_core'])} 变为 {_percent(base_long['retention_core'])}。")
    cfg = resolve_config(from_project(manifest["entry"]))
    pairs = cfg.module("combat")["research_v2"]["support_pairs"]
    early = {e["unit_id"] for e in cfg.module("scenarios")["events"] if e["t"] <= 3 and e["role"] != "support"}
    with open(out / "tables/l2_support_pair_effects.csv", encoding="utf-8", newline="") as handle:
        pair_rows = [r for r in csv.DictReader(handle)
                     if r["policy_id"] == "L1_A1" and r["combat_variant"] == "v2"
                     and int(r["horizon"]) == horizon and r["investment_plan"] == "0+1"
                     and r["gear_policy"] == "growth_from_debut" and r["anchor_unit_id"] in early
                     and r["support_policy"].split(":")[-1] in pairs.get(r["anchor_unit_id"], {})]
    asset_table = ["首 3 期出道主输出的预设适配支援对照，λ=1、a=1、0+1、持续养成：", "",
                   "| 主输出 | 适配支援 | 无专拐首次失效/删失（天） | 配支援首次失效/删失（天） | 额外满星期数 |",
                   "|---|---|---:|---:|---:|"]
    for row in sorted(pair_rows, key=lambda r: (r["anchor_unit_id"], r["support_policy"])):
        natural = f"{float(row['natural_first_failure_or_censor_days']):g}" + ("（删失）" if row["natural_first_failure_event"] != "True" else "")
        supported = f"{float(row['first_failure_or_censor_days']):g}" + ("（删失）" if row["first_failure_event"] != "True" else "")
        asset_table.append(f"| {row['anchor_unit_id']} | {row['support_policy'].split(':')[-1]} | {natural} | {supported} | {float(row['incremental_full_star_periods']):+g} |")
    asset_table.append("")
    lines = ["# 机制锁与售后支援：固定玩家队列对照实验", "",
        "研究问题：在持续推出新主输出、机制环境与限定支援的抽卡动作游戏中，机制锁强度 λ 与售后支援强度 a 如何改变老角色的可用寿命、玩家囤抽和付费意愿，以及固定初始队列的收入与留存？", "",
        f"本次实验：每个运行 {manifest['players']:,} 名初始玩家，{len(manifest['seeds'])} 组独立种子；观察期为 {' / '.join(str(h) for h in manifest['horizons'])} 期。共完成 {len(rows)} 个运行。",
        "", *findings,
        "", "这些是本次情景下的筛选样本结果；独立复核与适用范围见第 6、7 节。完整现金、两类付费和固定队列留存可直接核对[候选汇总](tables/grid_summary.csv)与[独立运行](tables/run_summary.csv)。",
        "",
        "## 1. 对照设计", "",
        "所有候选共用商品目录、首发数量、资源、价格、HP 和主输出基准增长路径。λ 仅改变机制惩罚，a 仅改变相对通用旧辅的专属支援净收益。四个极端组合用于解释取舍；正式候选集合为本次实际运行的网格点。",
        "",
        "短期 10 期与长期 20 期采用同一 20 期目录的前缀。两轮环境与售后周期使第一轮角色能被追踪到下一轮机制更替；角色出道较晚带来的右删失单独保留。",
        "",
        "初始预算层不随停氪改变。订阅续费意愿、直购意愿与活跃状态分别记录；停氪玩家仍保留已获得角色、保底和免费/月卡库存。", "",
        "## 2. 机制锁 × 支援强度的收入与留存", "",
        "下表使用筛选种子的均值；复核种子、每个独立运行和逐期现金均保存在配套 CSV 中。前沿表示在本次有限候选中，未被其他候选同时在收入和核心留存两方面超过。", "",
        "![机制锁与核心留存](figures/mechanism_retention.svg)", "",
        "![机制锁与累计现金](figures/mechanism_cash.svg)", "",
        "| 观察期 | λ | a | 累计现金 | 其中订阅 | 其中直购 | 核心层留存 | 全队列留存 | 非支配 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
    for r in sorted(train_rows, key=lambda r: (r["horizon"], r["mechanism_strength"], r["support_gain_scale"])):
        lines.append(f"| {r['horizon']} | {r['mechanism_strength']:g} | {r['support_gain_scale']:g} | ¥{r['cash_yuan']:,.0f} | ¥{r['subscription_yuan']:,.0f} | ¥{r['topup_yuan']:,.0f} | {_percent(r['retention_core'])} | {_percent(r['retention_all'])} | {'★' if r.get('mean_nondominated') else ''} |")
    lines += ["", "## 3. 战斗与付费行为的分离对照", "",
        "在 λ=1、a=1 的同一商品目录上，比较战斗 v1/v2 × 付费行为 v1/v2 四种组合。战斗 v1 保留旧秽盾惩罚；行为 v1 保留固定付费能力、不启用疲劳驱动的续订和直购意愿门控。该对照检验结果来自哪个模块，不能把两个模块同时变化的总差异解释为单一参数效应。", "",
        "![模块对照：留存](figures/ablation_retention.svg)", "",
        "![模块对照：现金](figures/ablation_cash.svg)", "",
        "| 观察期 | 战斗 | 付费行为 | 累计现金 | 订阅现金 | 直购现金 | 核心层留存 |",
        "|---:|:---:|:---:|---:|---:|---:|---:|"]
    ablation = [r for r in summaries if r["mechanism_strength"] == 1 and r["support_gain_scale"] == 1 and r["split"] == "train"]
    for r in sorted(ablation, key=lambda r: (r["horizon"], r["combat_variant"], r["spending_variant"])):
        lines.append(f"| {r['horizon']} | {r['combat_variant']} | {r['spending_variant']} | ¥{r['cash_yuan']:,.0f} | ¥{r['subscription_yuan']:,.0f} | ¥{r['topup_yuan']:,.0f} | {_percent(r['retention_core'])} |")
    lines += ["", "## 4. 老角色：首次失效、售后恢复与维护需求", "",
        "L2 固定参考面板在同条件下逐个比较不配限定支援与支援出道即加入，输出每名主输出 × 投资档位 × 养成路径 × 支援方案的完整轨迹。首次失效采用首次 CI<1；后续重新满星记作恢复，不回写首次失效时间。观察结束仍未失效的主体保留右删失。", "",
        "[L2 参考寿命](tables/l2_reference_lifetimes.csv)记录首次失效/删失天数、恢复时点、满星期数与总观测期数；[L2 完整轨迹](tables/l2_reference_panel.csv)可复核所有 CI；[支援配对净收益](tables/l2_support_pair_effects.csv)逐主体比较支援与无支援的新增满星期数。只有两条轨迹均已首次失效时才计算首次失效天数差，不把右删失当作已测得的完整寿命。L3 的[老资产结果](tables/old_asset_outcomes.csv)在四角和基准配置全量记录所有持有老角色，包含仓库角色与退坑冻结配置；实际活跃期首次失败与含冻结反事实随访的首次失败分别记录。", "",
        *asset_table,
        "`run_summary.csv` 中售后池抽数与直购金额反映支援维护需求。它们是该支援池的实际支付，不把一次支援购买重复分摊为多个主输出的成本，也不视为单个老角色延寿的因果价格。", "",
        "## 5. 囤抽、停氪与退坑", "",
        "[逐期经济结果](tables/period_outcomes.csv)同时保留四来源抽数、免费与订阅期末库存、两类现金、实际付费人数以及固定初始层留存。现金字段 `_yuan` 的单位为元；逐期原始流水 `sub_fen`、`topup_fen` 的单位为分。[付费意愿结果](tables/spending_outcomes.csv)记录续费/直购门控概率、决策前疲劳和连续失败，并单列拒绝付费但期末仍活跃的人数；同一玩家同一期同一付费类型仅计一次。付费拒绝事件不等于永久降层；下一期仍可恢复意愿。", "",
        "## 6. 跨种子复核", "",
        "| 观察期 | 配对候选数 | 筛选种子数 | 复核种子数 | 收入排名 ρ | 留存排名 ρ |",
        "|---:|---:|---:|---:|---:|---:|"]
    for r in stability:
        rev = "—" if r["rho_revenue"] is None else f"{r['rho_revenue']:.3f}"
        ret = "—" if r["rho_retention"] is None else f"{r['rho_retention']:.3f}"
        lines.append(f"| {r['horizon']} | {r['matched_candidates']} | {r['train_seeds']} | {r['validation_seeds']} | {rev} | {ret} |")
    lines += ["", "各配置的入选频率以独立种子下的非支配集合分别计算，见 `tables/grid_summary.csv`；只有一个种子时频率仅为 0/1，不能作为稳定概率。收入与留存排名分别报告，不合成总分。", "",
        "## 7. 适用范围与数据索引", "",
        "Layer 1 的[机制对口/非对口成绩](../final_release/empirical_knob1_boss_lock.csv)与[专拐/替代辅助成绩](../final_release/empirical_knob2_support_lift.csv)为条件观测锚点，不是随机实验，不能直接识别 λ 或 a 的因果值。模型把这些区间与机制分类作为可解释的情景参数；行为权重仍是显式假设。[参数登记表](tables/parameter_registry.csv)区分源码公式、显式情景取值和带条件观测参照的情景取值。", "",
        "本实验只有固定初始玩家队列，没有获客、回流和现实更新成本。20 期累计现金是该队列在情景内的收入，不是游戏全盘流水或完整生命周期价值；未观察到的后续付费与退坑不外推。短窗口现金与长期留存的取舍由表中结果呈现，不预设售后或机制锁必胜。", "",
        "购买规划器只评估当前卡池的部分获取结果，不预知未来排期，也不求解跨期最优囤抽。囤抽记忆 h 表示随经历调整的行为状态，本实验不能据此识别玩家的理性预期或 Coase 式等待均衡。", "",
        "| 文件 | 内容 |", "|---|---|",
        "| `experiment_manifest.json` | 入口配置、种子、规模、运行路径与科学配置身份 |",
        "| `tables/run_summary.csv` | 独立运行的收入、留存、售后支出与旧资产恢复 |",
        "| `tables/grid_summary.csv` | 各观察期/种子组的候选均值及非支配入选频率 |",
        "| `tables/rank_stability.csv` | 收入与留存排序的独立复核 |",
        "| `tables/parameter_registry.csv` | 基准科学配置逐参数来源与适用范围 |",
        "| `tables/period_outcomes.csv` | 逐期现金、库存、抽数来源与固定层留存 |",
        "| `tables/spending_outcomes.csv` | 续订/直购意愿与停氪但活跃人期 |",
        "| `tables/old_asset_outcomes.csv` | 全量日志配置的历史持有角色轨迹摘要 |",
        "| `tables/l2_reference_panel.csv` | 固定同条件战斗轨迹 |",
        "| `tables/l2_reference_lifetimes.csv` | 首次失效、右删失与售后恢复 |",
        "| `tables/l2_support_pair_effects.csv` | 同条件支援净增满星期数与首次失效差 |", ""]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/research_v2/experiment.json")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/research_v2")
    parser.add_argument("--players", type=int, default=200)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1101, 9101])
    parser.add_argument("--horizons", type=int, nargs="+", default=[10, 20])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--suite", choices=("smoke", "search"), default="search",
                        help="smoke: 四角、基准与模块对照；search: 全网格与模块对照")
    args = parser.parse_args(argv)
    if args.players <= 0 or args.workers <= 0 or any(s < 0 for s in args.seeds):
        parser.error("人数、工作进程必须为正，随机种子不得为负")
    if len(args.seeds) != len(set(args.seeds)) or len(args.horizons) != len(set(args.horizons)):
        parser.error("种子与观察期不能重复")
    args.config = args.config.resolve()
    args.out = args.out.resolve()
    base = resolve_config(args.config, overrides={"population.n_players": args.players})
    maximum = base.module("scenarios")["timeline"]["n_periods"]
    if any(h <= 0 or h > maximum for h in args.horizons):
        parser.error(f"观察期必须介于 1 和目录总期数 {maximum} 之间")
    args.out.mkdir(parents=True, exist_ok=True)
    plan = build_plan(base, seeds=args.seeds, horizons=args.horizons, suite=args.suite)
    manifest = {"entry": project_path(args.config), "base_config_id": base.config_id, "players": args.players,
        "seeds": args.seeds, "horizons": args.horizons, "suite": args.suite,
        "planned_runs": len(plan), "status": "running", "runs": []}
    write_json(args.out / "experiment_manifest.json", manifest)
    print(f"固定队列实验：{len(plan)} 个运行，{args.players} 人，{args.workers} 个工作进程", flush=True)
    results = []
    if args.workers == 1:
        for i, spec in enumerate(plan, 1):
            results.append(run_one(str(args.config), str(args.out), args.players, asdict(spec)))
            print(f"[{i}/{len(plan)}] h{spec.horizon} {spec.cell_id} seed={spec.seed} {'复用' if results[-1]['reused'] else '完成'}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(run_one, str(args.config), str(args.out), args.players, asdict(spec))
                       for spec in plan]
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                results.append(result)
                print(f"[{i}/{len(plan)}] h{result['horizon']} {result['cell_id']} seed={result['seed']} {'复用' if result['reused'] else '完成'}", flush=True)
    results.sort(key=lambda r: (r["horizon"], r["cell_id"], r["seed"]))
    summaries, periods, spending, assets = [], [], [], []
    for result in results:
        summary, per, spend, asset = read_outcomes(result)
        summaries.append(summary)
        periods.extend(per)
        spending.extend(spend)
        assets.extend(asset)
    for name, rows in (("run_summary", summaries), ("period_outcomes", periods),
                       ("spending_outcomes", spending), ("old_asset_outcomes", assets)):
        write_csv(args.out / "tables" / f"{name}.csv", rows)
    grid, stability = summarize_grid(summaries)
    grid_columns = list(grid[0]) + [k for k in ("mean_nondominated", "inclusion_frequency") if k not in grid[0]]
    write_csv(args.out / "tables/grid_summary.csv", grid, columns=grid_columns)
    write_csv(args.out / "tables/rank_stability.csv", stability)
    print("计算固定参考战斗轨迹与首次失效/恢复口径", flush=True)
    build_reference_panels(args.config, args.out, args.players, plan)
    write_parameter_registry(base, args.out)
    figures = build_figures(args.out, grid, periods)
    manifest.update({"status": "completed", "completed_runs": len(results), "runs": results})
    manifest["figures"] = figures
    write_json(args.out / "experiment_manifest.json", manifest)
    write_report(args.out, manifest, summaries, grid, stability)
    print(f"完成：{args.out / 'REPORT.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
