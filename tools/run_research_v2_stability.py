"""P0 主网格多种子复核；20 期运行同时提供 10/20 期前缀统计。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_research_v2 import RunSpec, run_one
from zzz_research.config import resolve_config
from zzz_research.pipeline import from_project, project_path, write_csv, write_json


def build_plan(cfg: Any, seeds: list[int] | None = None) -> list[RunSpec]:
    meta = cfg.metadata["module_metadata"]["experiments"]
    train, validation = list(meta["train_seeds"]), list(meta["validation_seeds"])
    if set(train) & set(validation):
        raise ValueError("筛选与复核种子必须独立")
    selected = train + validation if seeds is None else list(seeds)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("种子必须非空且不得重复")
    if set(selected) - set(train + validation):
        raise ValueError("种子必须在 experiments 的筛选或复核清单中登记")
    horizon = cfg.module("scenarios")["timeline"]["n_periods"]
    grid = cfg.module("experiments")["grid"]
    return [RunSpec(f"L{l:g}_A{a:g}", float(l), float(a), "v2", "v2", horizon,
                    seed, "train" if seed in train else "validation", "summary")
            for l in grid["mechanism_strength"] for a in grid["support_gain_scale"]
            for seed in selected]


@contextmanager
def _open(db: Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def _run_path(directory: Path, spec: RunSpec, scenario: str) -> Path:
    return directory / "runs" / f"h{spec.horizon}" / spec.cell_id / f"{scenario}_seed{spec.seed}.sqlite"


def _matching_cache(db: Path, config_id: str, seed: int) -> str | None:
    if not db.exists():
        return None
    with _open(db) as con:
        manifests = con.execute("SELECT config_id,master_seed,status FROM run_manifest").fetchall()
        mode = con.execute("SELECT log_mode FROM run_attempt ORDER BY attempt_id DESC LIMIT 1").fetchone()
    if (len(manifests) == 1 and manifests[0]["config_id"] == config_id
            and manifests[0]["master_seed"] == seed and manifests[0]["status"] == "completed"
            and mode is not None):
        return mode["log_mode"]
    return None


def resolve_cached_run(entry: str, out: str, cache_out: str, players: int,
                       spec_dict: dict[str, Any]) -> dict[str, Any]:
    spec = RunSpec(**spec_dict)
    cfg = resolve_config(entry, overrides=spec.overrides(players))
    scenario = cfg.module("experiments")["base_scenario"]
    cache = _run_path(Path(cache_out), spec, scenario)
    mode = _matching_cache(cache, cfg.config_id, spec.seed)
    source = (Path(cache_out) if mode is not None else Path(out)).resolve()
    result = run_one(entry, str(source), players,
                     asdict(replace(spec, log_mode=mode)) if mode else spec_dict)
    result["cache_source"] = "prior_experiment" if mode else "current_experiment"
    return result


def read_prefix_outcomes(result: dict[str, Any], horizons: tuple[int, ...] = (10, 20)
                         ) -> tuple[list[dict], list[dict], list[dict]]:
    """单卡池区间的直购现金按商品类型分账；前缀保留原 20 期运行身份。"""
    with _open(from_project(result["database"])) as con:
        config = json.loads(con.execute("SELECT config_json FROM run_manifest WHERE run_id=?",
                                       (result["run_id"],)).fetchone()[0])
        modules = config["modules"]
        core = modules["population"]["core_retention_tier"]
        simulation_horizon = modules["scenarios"]["research_v2"]["observation_periods"]
        products = con.execute("""SELECT p.t_idx,COUNT(b.banner_id) AS banners,
            MIN(b.banner_id) AS banner_id,MIN(u.role) AS role
            FROM dim_period p LEFT JOIN dim_banner b ON b.period_id=p.period_id
            LEFT JOIN dim_unit u ON u.catalog_id=b.catalog_id AND u.unit_id=b.target_unit_id
            GROUP BY p.t_idx ORDER BY p.t_idx""").fetchall()
        if len(products) != simulation_horizon or any(p["banners"] != 1 for p in products):
            raise ValueError("现金类型拆分要求每个物理区间恰好一个卡池")
        roles = {p["t_idx"]: p["role"] for p in products}
        if set(roles.values()) - {"normal", "piercing", "hybrid", "support"}:
            raise ValueError("卡池商品存在未知角色类型")
        periods = [dict(r) for r in con.execute("""SELECT t_idx,COUNT(*) AS initial_players,
            SUM(active_end) AS active_end,
            SUM(CASE WHEN player_tier=? THEN 1 ELSE 0 END) AS core_initial,
            SUM(CASE WHEN player_tier=? THEN active_end ELSE 0 END) AS core_active_end,
            SUM(sub_revenue_fen) AS subscription_fen,SUM(topup_revenue_fen) AS topup_fen,
            SUM(CASE WHEN sub_revenue_fen>0 THEN 1 ELSE 0 END) AS subscription_payers,
            SUM(CASE WHEN topup_revenue_fen>0 THEN 1 ELSE 0 END) AS topup_payers,
            SUM(total_pulls) AS total_pulls,SUM(curr_free_used) AS curr_free_used,
            SUM(hoarded_free_used) AS hoarded_free_used,SUM(sub_pulls_used) AS sub_pulls_used,
            SUM(topup_pulls_used) AS topup_pulls_used,SUM(ending_free_minor) AS ending_free_minor,
            SUM(ending_sub_minor) AS ending_sub_minor
            FROM agg_player_period WHERE run_id=? GROUP BY t_idx ORDER BY t_idx""",
            (core, core, result["run_id"]))]
        population = [dict(r) for r in con.execute("""SELECT player_tier,COUNT(*) AS initial_players
            FROM agg_player_period WHERE run_id=? AND t_idx=1 GROUP BY player_tier ORDER BY player_tier""",
            (result["run_id"],))]
    if [p["t_idx"] for p in periods] != list(range(1, simulation_horizon + 1)):
        raise ValueError("已完成运行缺少物理区间汇总")
    players = modules["population"]["n_players"]
    if any(p["initial_players"] != players or p["core_initial"] != periods[0]["core_initial"] for p in periods):
        raise ValueError("初始队列分母在物理区间间不一致")
    identity = {key: result[key] for key in ("run_id", "config_id", "policy_id", "cell_id", "seed", "split",
                                              "mechanism_strength", "support_gain_scale", "database", "log_mode")}
    identity["simulation_horizon"] = simulation_horizon
    accumulated = {"subscription_fen": 0, "new_main_topup_fen": 0, "support_topup_fen": 0}
    per_rows = []
    for p in periods:
        role = roles[p["t_idx"]]
        p["new_main_topup_fen"] = p["topup_fen"] if role != "support" else 0
        p["support_topup_fen"] = p["topup_fen"] if role == "support" else 0
        p["cash_fen"] = p["subscription_fen"] + p["topup_fen"]
        for key in accumulated:
            accumulated[key] += p[key]
        if p["cash_fen"] != p["subscription_fen"] + p["new_main_topup_fen"] + p["support_topup_fen"]:
            raise ValueError("三类现金与总现金不守恒")
        p["retention_core"] = p["core_active_end"] / p["core_initial"] if p["core_initial"] else None
        p["retention_all"] = p["active_end"] / players
        per_rows.append({**identity, "horizon": simulation_horizon, **p, "banner_role": role,
            **{key.replace("_fen", "_yuan"): value / 100 for key, value in p.items() if key.endswith("_fen")},
            **{f"cumulative_{key.replace('_fen','_yuan')}": value / 100 for key, value in accumulated.items()},
            "cumulative_cash_yuan": sum(accumulated.values()) / 100})
    rows = []
    for horizon in horizons:
        if not 1 <= horizon <= simulation_horizon:
            raise ValueError("观察前缀超出实际运行区间")
        prefix, last = per_rows[:horizon], per_rows[horizon - 1]
        sums = {key: sum(p[key] for p in prefix) for key in
                ("cash_fen", "subscription_fen", "new_main_topup_fen", "support_topup_fen",
                 "total_pulls", "curr_free_used", "hoarded_free_used", "sub_pulls_used", "topup_pulls_used")}
        rows.append({**identity, "horizon": horizon, "observation_horizon": horizon,
            "players": players, "core_initial": last["core_initial"],
            "core_active_end": last["core_active_end"], "active_end": last["active_end"],
            "retention_core": last["retention_core"], "retention_all": last["retention_all"],
            **{key: value for key, value in sums.items() if not key.endswith("_fen")},
            **{key.replace("_fen", "_yuan"): value / 100 for key, value in sums.items() if key.endswith("_fen")},
            "cash_per_initial_player_yuan": sums["cash_fen"] / 100 / players,
            "subscription_payer_periods": sum(p["subscription_payers"] for p in prefix),
            "topup_payer_periods": sum(p["topup_payers"] for p in prefix),
            "final_subscription_payers": last["subscription_payers"],
            "final_topup_payers": last["topup_payers"],
            "ending_free_minor": last["ending_free_minor"], "ending_sub_minor": last["ending_sub_minor"]})
    population = [{"seed": result["seed"], "split": result["split"], **p} for p in population]
    return rows, per_rows, population


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/research_v2/experiment.json")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/research_v2_stability")
    parser.add_argument("--cache-out", type=Path, default=ROOT / "outputs/research_v2")
    parser.add_argument("--players", type=int, default=200)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seeds", type=int, nargs="+")
    parser.add_argument("--horizons", type=int, nargs="+", default=[10, 20])
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    if args.players <= 0 or args.workers <= 0:
        parser.error("人数与进程数必须为正")
    args.config, args.out, args.cache_out = args.config.resolve(), args.out.resolve(), args.cache_out.resolve()
    if args.out in {args.cache_out, (ROOT / "outputs/final_release").resolve()}:
        parser.error("扩种子实验须使用独立输出目录")
    cfg = resolve_config(args.config, overrides={"population.n_players": args.players})
    plan = build_plan(cfg, args.seeds)
    horizon = plan[0].horizon
    if (not args.horizons or len(args.horizons) != len(set(args.horizons))
            or any(h < 1 or h > horizon for h in args.horizons)):
        parser.error(f"观察窗口须互异且在 1–{horizon} 之间")
    meta = cfg.metadata["module_metadata"]["experiments"]
    reusable = 0
    for spec in plan:
        candidate = resolve_config(args.config, overrides=spec.overrides(args.players))
        path = _run_path(args.cache_out, spec, candidate.module("experiments")["base_scenario"])
        reusable += _matching_cache(path, candidate.config_id, spec.seed) is not None
    print(f"P0 主网格：{len(plan)} 个 {horizon} 期运行，旧实验可复用 {reusable} 个；观察前缀 {args.horizons}", flush=True)
    if args.plan_only:
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    selected_seeds = {s.seed for s in plan}
    manifest = {"entry": project_path(args.config), "base_config_id": cfg.config_id, "players": args.players,
        "population_shares": {tier["id"]: tier["share"] for tier in cfg.module("population")["tiers"]},
        "seeds": sorted(selected_seeds),
        "train_seeds": [s for s in meta["train_seeds"] if s in selected_seeds],
        "validation_seeds": [s for s in meta["validation_seeds"] if s in selected_seeds], "horizons": args.horizons,
        "simulation_horizon": horizon, "planned_runs": len(plan), "completed_runs": 0,
        "reused_runs": 0, "new_runs": 0, "prior_experiment_runs": 0,
        "current_experiment_runs": 0, "status": "running", "runs": [],
        "prefix_definition": "10/20期统计读取同一20期运行的前缀；config_id与run_id均为实际20期物理运行身份。",
        "cash_definition": "每物理区间单卡池：主输出/支援直购按卡池商品类型归类；订阅现金独立；单位元。"}
    path = args.out / "experiment_manifest.json"
    write_json(path, manifest)
    results = []
    try:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(resolve_cached_run, str(args.config), str(args.out), str(args.cache_out),
                                       args.players, asdict(spec)) for spec in plan]
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                results.append(result)
                manifest.update(completed_runs=len(results), reused_runs=sum(r["reused"] for r in results),
                                new_runs=sum(not r["reused"] for r in results),
                                prior_experiment_runs=sum(r["cache_source"] == "prior_experiment" for r in results),
                                current_experiment_runs=sum(r["cache_source"] == "current_experiment" for r in results),
                                runs=results)
                write_json(path, manifest)
                print(f"[{i}/{len(plan)}] {result['policy_id']} seed={result['seed']} "
                      f"{'复用' if result['reused'] else '完成'}", flush=True)
        run_rows, period_rows, population_by_seed = [], [], {}
        for result in sorted(results, key=lambda r: (r["policy_id"], r["seed"])):
            runs, periods, population = read_prefix_outcomes(result, tuple(args.horizons))
            run_rows.extend(runs)
            period_rows.extend(periods)
            population_by_seed[result["seed"]] = population
        from research_v2_stability_stats import compute_statistics
        from research_v2_stability_report import write_stability_report
        tables = compute_statistics(run_rows, baseline_id="L1_A1")
        tables.update(run_summary=run_rows, period_outcomes=period_rows,
                      population_by_seed=[r for seed in sorted(population_by_seed) for r in population_by_seed[seed]])
        for name, rows in tables.items():
            write_csv(args.out / "tables" / f"{name}.csv", rows)
        manifest["status"] = "completed"
        manifest["runs"] = sorted(results, key=lambda r: (r["policy_id"], r["seed"]))
        write_stability_report(args.out, manifest, tables)
        write_json(path, manifest)
    except BaseException as exc:
        manifest.update(status="failed", error_type=type(exc).__name__, error=str(exc))
        write_json(path, manifest)
        raise
    print(f"完成：{args.out / 'REPORT.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
