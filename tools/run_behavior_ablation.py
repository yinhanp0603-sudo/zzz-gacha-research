"""行为函数结构消融：M0 现行停氪缓冲、M1 无停氪缓冲、M2 强追新偏好，九个角点/中心点 × 三个复核种子。

M0 直接复用 N=200 多种子实验中配置身份一致的运行；M1/M2 为新增运行。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_research_v2 import RunSpec
from run_research_v2_stability import _matching_cache, read_prefix_outcomes
from research_v2_stability_stats import compute_statistics
from zzz_research.config import resolve_config
from zzz_research.pipeline import project_path, simulate, write_csv, write_json

BASE_ENTRY = ROOT / "config/research_v2/experiment.json"
NOVELTY_ENTRY = ROOT / "config/research_v2/experiment_m2_novelty.json"
LAMBDAS, GAINS, SEEDS = (0.0, 1.0, 2.0), (0.8, 1.0, 1.6), (9101, 9102, 9103)
NO_STOP_SPEND = {f"behavior.research_v2.spending.{channel}.{weight}": 0.0
                 for channel in ("subscription", "topup")
                 for weight in ("fatigue_weight", "failure_weight", "paid_failure_weight")}
MODELS = {
    "M0_Baseline": {"entry": BASE_ENTRY, "overrides": {},
                    "label": "当前 V2：卡关疲劳 → 停氪缓冲 → 退坑"},
    "M1_NoStopSpend": {"entry": BASE_ENTRY, "overrides": NO_STOP_SPEND,
                       "label": "无停氪缓冲：疲劳与连续失败不扣减直购/续订意愿，只驱动退坑"},
    "M2_NoveltyDriven": {"entry": NOVELTY_ENTRY, "overrides": {},
                         "label": "强追新偏好：新主C池下池效用另加固定 β_novelty"},
}


def plan(players: int) -> list[tuple[str, RunSpec]]:
    return [(model, RunSpec(f"L{l:g}_A{a:g}", l, a, "v2", "v2", 20, seed, "validation", "summary"))
            for model in MODELS for l in LAMBDAS for a in GAINS for seed in SEEDS]


def _config(model: str, spec: RunSpec, players: int):
    return resolve_config(MODELS[model]["entry"], overrides={**spec.overrides(players), **MODELS[model]["overrides"]})


def _baseline_runs(path: Path) -> dict[tuple[str, int], dict]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest["status"] != "completed":
        raise ValueError("M0 复用来源实验未完成")
    return {(r["policy_id"], r["seed"]): r for r in manifest["runs"]}


def run_cell(model: str, out: str, players: int, spec_dict: dict) -> dict:
    spec = RunSpec(**spec_dict)
    cfg = _config(model, spec, players)
    directory = Path(out) / "runs" / model / f"h{spec.horizon}" / spec.cell_id
    db = directory / f"{cfg.module('experiments')['base_scenario']}_seed{spec.seed}.sqlite"
    started = time.perf_counter()
    reused = _matching_cache(db, cfg.config_id, spec.seed) is not None
    if reused:
        import sqlite3
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
            run_id = con.execute("SELECT run_id FROM run_manifest").fetchone()[0]
    else:
        run_id = simulate(MODELS[model]["entry"], directory, seed=spec.seed, n_players=players,
                          log_mode=spec.log_mode,
                          extra_overrides={**spec.overrides(players), **MODELS[model]["overrides"]})["run_id"]
    return {**asdict(spec), "model": model, "cell_id": spec.cell_id, "config_id": cfg.config_id,
            "run_id": run_id, "database": project_path(db), "reused": reused,
            "cache_source": "current_experiment", "wall_seconds": time.perf_counter() - started}


def summarize(tables_by_model: dict[str, dict[str, list[dict]]]) -> list[dict]:
    """把三种行为设定的同口径轴效应并排成命题对照行。"""
    rows = []
    for model, tables in tables_by_model.items():
        effects = {(r["horizon"], r["axis"], r["metric"]): r for r in tables["axis_effects"]
                   if r["contrast_kind"] == "endpoint"}
        decomposition = {(r["horizon"], r["axis"], r["metric"]): r for r in tables["axis_cash_decomposition"]
                         if r["contrast_kind"] == "endpoint"}
        for horizon in (10, 20):
            for axis, proposition in (("mechanism_strength", "A"), ("support_gain_scale", "B/C")):
                retention, cash = effects[horizon, axis, "retention_core"], effects[horizon, axis, "cash_yuan"]
                row = {"model": model, "model_label": MODELS[model]["label"], "proposition": proposition,
                       "axis": axis, "low_level": retention["low_level"], "high_level": retention["high_level"],
                       "other_axis_levels": retention["other_axis_levels"], "horizon": horizon,
                       "n_seeds": retention["n_pairs"],
                       "retention_core_diff_pp": retention["mean_diff"],
                       "retention_core_ci95_low_pp": retention["ci95_low"],
                       "retention_core_ci95_high_pp": retention["ci95_high"],
                       "cash_relative_pct": cash["mean_relative_pct"],
                       "cash_relative_ci95_low_pct": cash["relative_ci95_low"],
                       "cash_relative_ci95_high_pct": cash["relative_ci95_high"],
                       "cash_ci_status": cash["ci_status"]}
                for metric in ("new_main_topup_yuan", "support_topup_yuan", "subscription_yuan"):
                    part = decomposition[horizon, axis, metric]
                    stem = metric.removesuffix("_yuan")
                    row[f"{stem}_contribution_pp"] = part["mean_contribution_pp"]
                    row[f"{stem}_contribution_ci95_low_pp"] = part["contribution_ci95_low_pp"]
                    row[f"{stem}_contribution_ci95_high_pp"] = part["contribution_ci95_high_pp"]
                rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/research_v2_behavior_ablation")
    parser.add_argument("--baseline-manifest", type=Path,
                        default=ROOT / "outputs/research_v2_stability/experiment_manifest.json")
    parser.add_argument("--players", type=int, default=200)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    specs = plan(args.players)
    baseline = _baseline_runs(args.baseline_manifest)
    reusable = []
    for model, spec in specs:
        if model != "M0_Baseline":
            continue
        prior = baseline.get((spec.policy_id, spec.seed))
        if prior is None or prior["config_id"] != _config(model, spec, args.players).config_id:
            raise ValueError(f"M0 缺少配置一致的 N={args.players} 复用运行：{spec.policy_id}/{spec.seed}")
        reusable.append({**prior, "model": model, "cache_source": "prior_experiment"})
    print(f"行为结构消融：{len(specs)} 次运行，M0 复用 {len(reusable)}，新增 {len(specs) - len(reusable)}", flush=True)
    if args.plan_only:
        return 0
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"entries": {m: str(v["entry"].relative_to(ROOT)) for m, v in MODELS.items()},
                "overrides": {m: v["overrides"] for m, v in MODELS.items()},
                "labels": {m: v["label"] for m, v in MODELS.items()},
                "players": args.players, "seeds": list(SEEDS), "mechanism_strength": list(LAMBDAS),
                "support_gain_scale": list(GAINS), "horizons": [10, 20], "simulation_horizon": 20,
                "planned_runs": len(specs), "reused_runs": len(reusable), "status": "running",
                "baseline_source": str(args.baseline_manifest.resolve().relative_to(ROOT)),
                "purpose": "检验商业命题对行为函数结构的依赖性；不扩大人口，不追逐点估计精度。"}
    write_json(out / "experiment_manifest.json", manifest)
    results = list(reusable)
    try:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(run_cell, model, str(out), args.players, asdict(spec))
                       for model, spec in specs if model != "M0_Baseline"]
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                results.append(result)
                print(f"[{i}/{len(futures)}] {result['model']} {result['policy_id']} seed={result['seed']}", flush=True)
        run_rows, tables_by_model = [], {}
        for model in MODELS:
            model_rows = []
            for result in sorted((r for r in results if r["model"] == model), key=lambda r: (r["policy_id"], r["seed"])):
                rows, _, _ = read_prefix_outcomes({**result, "split": "validation"})
                model_rows.extend(rows)
            tables_by_model[model] = compute_statistics(model_rows, baseline_id="L1_A1")
            run_rows.extend({"model": model, **row} for row in model_rows)
        write_csv(out / "tables/run_summary.csv", run_rows)
        for name in ("grid_summary", "axis_effects", "axis_cash_decomposition"):
            write_csv(out / f"tables/{name}.csv",
                      [{"model": m, **row} for m, tables in tables_by_model.items() for row in tables[name]])
        write_csv(out / "tables/proposition_summary.csv", summarize(tables_by_model))
        manifest.update(status="completed", completed_runs=len(results),
                        new_runs=sum(r["cache_source"] == "current_experiment" and not r.get("reused") for r in results),
                        runs=sorted(results, key=lambda r: (r["model"], r["policy_id"], r["seed"])))
        write_json(out / "experiment_manifest.json", manifest)
    except BaseException as exc:
        manifest.update(status="failed", error_type=type(exc).__name__, error=str(exc))
        write_json(out / "experiment_manifest.json", manifest)
        raise
    print(f"完成：{out / 'tables/proposition_summary.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
