"""阶段编排（engineering.md 13.1/13.2）：每阶段验证输入、写产物与计时；失败写 manifest 并停止依赖阶段。

产物布局：outputs/<experiment_dir>/
  validate.json · benchmarks/benchmarks.sqlite · observations/observations.sqlite
  reference/<scenario>_seed<seed>.sqlite · search/{runs/,search.sqlite,experiment_manifest.json}
  tables/*.csv · report/REPORT.md, figures/*.svg · dashboard/index.html · pipeline_manifest.json
"""

from __future__ import annotations

import csv
import json
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from .analytics import cohorts as CO
from .analytics import reference as REF
from .analytics import search as SE
from .analytics import survival as SV
from .combat import benchmark as BM
from .combat.proxy import ProxyCombatModel
from .config import resolve_config
from .contracts import deep_thaw, sha256_hex
from .observations import adapter_shiyu as AD
from .observations import quality as QA
from .provenance import build_parameter_registry
from .simulation import resolve_world, run_simulation
from .storage.writer import SQLiteWriter

PIPELINE_VERSION = "pipeline_v1"
DEFAULT_META_DIR = Path("data") / "raw" / "SRC_SHIYU_meta_d930cec50bfd"
ANALYTICS_SCHEMA = Path(__file__).resolve().parent / "storage" / "analytics_schema.sql"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_path(path: Path | str) -> str:
    """清单记录的路径：项目内为相对项目根的 POSIX 路径，项目外保留绝对路径。"""
    p = Path(path).resolve()
    return p.relative_to(PROJECT_ROOT).as_posix() if p.is_relative_to(PROJECT_ROOT) else str(p)


def from_project(value: Path | str) -> Path:
    """解析清单中的路径；相对路径以项目根为基准，绝对路径原样返回。"""
    return PROJECT_ROOT / value


class PipelineError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _replace_output(tmp: Path, path: Path) -> None:
    """Windows读取/杀毒程序短暂占用目标文件时重试，始终保留原子替换语义。"""
    deadline = time.monotonic() + 2.0
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Optional[Sequence[str]] = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    cols = list(columns or (rows[0].keys() if rows else []))
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    _replace_output(tmp, path)
    return path


def write_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _replace_output(tmp, path)
    return path


class StageLog:
    """阶段计时与状态；写入 pipeline_manifest.json。"""

    def __init__(self, out: Path):
        self.out = out
        self.path = out / "pipeline_manifest.json"
        self.data = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {
            "pipeline_version": PIPELINE_VERSION, "created_at": _now(),
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
            "stages": {}}

    def run(self, name: str, fn: Callable[[], Any]) -> Any:
        t0 = time.perf_counter()
        self.data["stages"][name] = {"status": "running", "started_at": _now()}
        write_json(self.path, self.data)
        try:
            result = fn()
        except Exception as exc:
            self.data["stages"][name].update(status="failed", error=f"{type(exc).__name__}: {exc}",
                                             wall_seconds=time.perf_counter() - t0)
            write_json(self.path, self.data)
            raise
        info = result if isinstance(result, dict) else {}
        self.data["stages"][name].update(status="completed", ended_at=_now(),
                                         wall_seconds=round(time.perf_counter() - t0, 3),
                                         **{k: v for k, v in info.items() if k.startswith("n_") or k in
                                            ("config_id", "run_id", "experiment_id", "scale", "path")})
        write_json(self.path, self.data)
        return result


# =========================================================================== validate


def validate_config(entry: Path | str) -> dict:
    cfg = resolve_config(entry)
    reg = build_parameter_registry(cfg)
    model = ProxyCombatModel(cfg.module("combat"))
    world = resolve_world(cfg, model)
    return {"config_id": cfg.config_id, "algorithm_versions": dict(cfg.algorithm_versions),
            "n_parameters": len(reg), "n_periods": len(world.periods), "n_units": len(world.units),
            "base_scenario": world.scenario_name,
            "baseline_grid_point": dict(cfg.module("experiments")["baseline_grid_point"]),
            "fatigue_params": {k: cfg.module("behavior")["fatigue"][k] for k in ("budget_weight", "success_recovery")},
            # 模块 JSON 的 description 是元数据（不进入 config_id），其中登记情景标定说明
            "module_notes": {m: d.get("description") for m, d in sorted(cfg.metadata["module_metadata"].items())
                             if isinstance(d, Mapping) and "标定" in str(d.get("description") or "")},
            "parameter_source_kinds": sorted({r.source_kind.value for r in reg}), "status": "ok"}


# =========================================================================== benchmarks (L2)


def build_benchmarks(entry: Path | str, out_dir: Path | str) -> dict:
    """固定基准：κ×支援×投资面板（各期环境）、支援/κ 配对、预注册参考轨迹与 KM。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cfg = resolve_config(entry)
    db = out / "benchmarks.sqlite"
    if db.exists():
        db.unlink()
    w = SQLiteWriter(db)
    w.conn.executescript(ANALYTICS_SCHEMA.read_text("utf-8"))
    model = ProxyCombatModel(cfg.module("combat"))
    world = resolve_world(cfg, model)
    # --- κ / 支援 / 投资面板：每期环境、锚定=全部主输出（含 starter）
    anchors = [BM.BenchmarkAnchor(u.unit_id, u.b_ref, u.pierce_share) for u in
               sorted(world.units.values(), key=lambda x: x.unit_id) if u.b_ref is not None]
    envs = [BM.BenchmarkEnvironment(p.period_id, p.scenario.hp, p.scenario.theta, p.scenario.uptime,
                                  p.scenario.mechanism_type, p.scenario.mechanism_strength)
            for p in world.periods]
    rows = BM.build_fixed_benchmark_panel(model, anchors, envs, neutral_fixed=world.neutral_fixed,
                                          neutral_flex=world.neutral_flex, gears=(1.0,))
    bid = rows[0].benchmark_id
    panel = [{"benchmark_id": r.benchmark_id, "anchor": r.anchor_unit_id, "investment": r.investment,
              "support": r.support or "NONE", "kappa": r.kappa, "gear": r.gear, "env_id": r.env_id,
              "eval_status": r.eval_status.value, "ci": r.ci, "meets_threshold": r.meets_threshold, "gap": r.gap}
             for r in rows]
    write_csv(out / "kappa_support_panel.csv", panel)
    # --- 配对：只改变支援（成本差：增加支援资产）；只改变 κ（同一未减伤输出总量）
    pairs = []
    default_k = model.params.default_alignment
    for sup in sorted(model.params.support_gains):
        for b, a, d in BM.paired_differences(rows, "support", None, sup):
            if b.kappa != default_k:
                continue
            pairs.append({"benchmark_id": bid, "pair_id": sha256_hex(["sup", b.key, sup])[:20], "period_id": b.env_id,
                          "changed_factor": "support", "base_label": f"{b.anchor_unit_id}|{b.investment}|NONE",
                          "alt_label": f"{a.anchor_unit_id}|{a.investment}|{sup}", "base_ci": b.ci, "alt_ci": a.ci,
                          "abs_diff": d, "rel_diff": None if not b.ci else d / b.ci,
                          "cost_note": "增加支援资产（非等成本比较）"})
    for k in model.params.alignment_values:
        if k == default_k:
            continue
        for b, a, d in BM.paired_differences(rows, "kappa", default_k, k):
            if b.support is not None:
                continue
            pairs.append({"benchmark_id": bid, "pair_id": sha256_hex(["kappa", b.key, k])[:20], "period_id": b.env_id,
                          "changed_factor": "kappa", "base_label": f"{b.anchor_unit_id}|{b.investment}|k={default_k:g}",
                          "alt_label": f"{a.anchor_unit_id}|{a.investment}|k={k:g}", "base_ci": b.ci, "alt_ci": a.ci,
                          "abs_diff": d, "rel_diff": None if not b.ci else d / b.ci,
                          "cost_note": "同一未减伤输出总量，仅改变盾内外时序分配"})
    w.write_rows("agg_benchmark_pairs", pairs)
    w.flush()
    write_csv(out / "benchmark_pairs.csv", pairs)
    # --- 参考轨迹（L2）与 KM
    ref_bid, _, obs = REF.evaluate_trajectories(cfg)
    REF.write_reference_panel(w, cfg, ref_bid, obs)
    traj = [{"reference_subject_id": o.subject.reference_subject_id, "anchor": o.subject.anchor_unit_id,
             "plan": o.subject.investment_plan, "gear_policy": o.subject.gear_policy,
             "support_policy": o.subject.support_policy, "t_idx": o.t_idx, "age_days": o.age_days,
             "support_active": o.support_active or "NONE", "gear": o.gear, "ci": o.ci,
             "is_full_star": o.is_full_star, "eval_status": o.eval_status} for o in obs]
    write_csv(out / "reference_trajectories.csv", traj)
    group_by = ("investment_plan", "gear_policy", "support_policy")
    km = SV.km_by_group(obs, group_by)
    km_rows = [SV.km_summary_row(k, group_by) for k in km]
    write_csv(out / "km_first_failure.csv", km_rows)
    km_steps = [{**dict(zip(group_by, k.group)), "age_days": s.time, "at_risk": s.at_risk, "events": s.events,
                 "censored": s.censored, "survival": s.survival} for k in km for s in k.steps]
    write_csv(out / "km_steps.csv", km_steps)
    write_csv(out / "dynamic_availability.csv", SV.dynamic_availability(obs, group_by))
    write_json(out / "cox_status.json", SV.cox_status(False))
    fk = w.query("PRAGMA foreign_key_check")
    w.close()
    if fk:
        raise PipelineError(f"基准数据库外键检查失败: {fk[:3]}")
    return {"config_id": cfg.config_id, "benchmark_id": bid, "reference_benchmark_id": ref_bid,
            "n_panel_rows": len(panel), "n_pairs": len(pairs), "n_reference_obs": len(obs),
            "n_km_groups": len(km), "path": str(db)}


# =========================================================================== observations (L1)


def prepare_observations(out_dir: Path | str, *, meta_dir: Path | str = DEFAULT_META_DIR,
                         snapshot_dir: Optional[Path | str] = None, snapshot_id: Optional[str] = None,
                         url: str = "https://huggingface.co/datasets/LvlUrArti/ShiyuData",
                         revision: Optional[str] = None, data_license: Optional[str] = None) -> dict:
    """无快照时只产出公开文件覆盖（真实元数据）；有快照时导入并产出覆盖、质量与描述汇总。不隐式联网。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "observations_manifest.json"
    state = {"adapter_version": AD.ADAPTER_VERSION, "started_at": _now(), "status": "running",
             "observed_data_available": False, "snapshot_id": snapshot_id if snapshot_dir is not None else None,
             "revision": revision if snapshot_dir is not None else None,
             "data_license": data_license if snapshot_dir is not None else None,
             "source_url": url,
             "snapshot_status": "not_provided" if snapshot_dir is None else "pending",
             "n_battles": 0, "n_eligible": 0, "path": str(out / "observations.sqlite")}
    # 同一目录的新调用不能继续暴露上一份快照的派生结果，尤其是metadata-only降级。
    for name in ("obs_coverage.csv", "exclusions.csv", "appearance.csv", "results_by_stage.csv",
                 "investment_association.csv", "quality_issues.json"):
        (out / name).unlink(missing_ok=True)
    write_json(manifest_path, state)
    try:
        result = _prepare_observations(out, meta_dir=meta_dir, snapshot_dir=snapshot_dir,
                                       snapshot_id=snapshot_id, url=url, revision=revision, data_license=data_license)
    except Exception as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}", ended_at=_now())
        write_json(manifest_path, state)
        raise
    available = snapshot_dir is not None
    result.update(observed_data_available=available, adapter_version=AD.ADAPTER_VERSION)
    state.update(result, snapshot_status="imported" if available else "not_provided", ended_at=_now())
    write_json(manifest_path, state)
    return result


def _prepare_observations(out: Path, *, meta_dir: Path | str,
                          snapshot_dir: Optional[Path | str], snapshot_id: Optional[str],
                          url: str, revision: Optional[str], data_license: Optional[str] = None) -> dict:
    meta = Path(meta_dir)
    vm_path = meta / "versions_config.json"
    vm = AD.load_version_map(vm_path)
    cov = QA.repo_file_coverage(meta / "repo_files.csv", vm)
    write_csv(out / "repo_file_coverage.csv", [c.__dict__ for c in cov])
    db = out / "observations.sqlite"
    if db.exists():
        db.unlink()
    w = SQLiteWriter(db)
    try:
        return _populate_observations(w, out, meta, vm_path, cov, db, snapshot_dir, snapshot_id, url, revision, data_license)
    finally:
        w.close()


def _populate_observations(w, out, meta, vm_path, cov, db, snapshot_dir, snapshot_id, url, revision,
                           data_license=None) -> dict:
    w.write_dim("source_manifest", [AD.manifest_row(meta / f, url=f"https://github.com/LvlUrArti/ShiyuStats",
                                                    revision=AD.SOURCE_REVISION, retrieved_at=_now(),
                                                    coverage={"kind": "metadata", "file": f})
                                    for f in ("versions_config.json", "repo_files.csv", "characters.json")])
    result = {"n_collection_periods": len(cov), "n_periods_complete": sum(c.status == "complete" for c in cov),
              "status": "metadata_only", "path": str(db)}
    if snapshot_dir is not None:
        if not snapshot_id or not revision:
            raise PipelineError("导入快照需要 snapshot_id 与 revision（固定数据修订）")
        n_battles = n_eligible = n_batches = 0
        for parsed in AD.iter_snapshot_batches(snapshot_dir, version_map_path=vm_path, snapshot_id=snapshot_id,
                                              url=url, revision=revision, retrieved_at=_now(),
                                              data_license=data_license):
            AD.write_snapshot(w, parsed)
            n_battles += len(parsed.battles)
            n_eligible += sum(b["is_eligible"] for b in parsed.battles)
            n_batches += 1
            write_json(out / "import_progress.json", {"phase": "importing", "batches": n_batches,
                       "n_battles": n_battles, "n_eligible": n_eligible,
                       "last_file": parsed.manifest[-1]["source_file_id"], "updated_at": _now()})
        if n_battles == 0:
            raise PipelineError("快照没有可导入的战斗记录，不能标记为已导入")
        write_json(out / "import_progress.json", {"phase": "indexing_and_summarizing", "batches": n_batches,
                   "n_battles": n_battles, "updated_at": _now()})
        w.conn.execute("CREATE INDEX IF NOT EXISTS ix_obs_scope ON obs_battle(snapshot_id,combat_season,mode,is_eligible,stage_id)")
        w.conn.execute("CREATE INDEX IF NOT EXISTS ix_obs_member_unit ON obs_team_member(unit_id,battle_id)")
        w.conn.execute("CREATE INDEX IF NOT EXISTS ix_obs_build_identity ON obs_build(snapshot_id,local_uid)")
        w.conn.execute("ANALYZE obs_battle")
        w.conn.execute("ANALYZE obs_team_member")
        w.conn.execute("ANALYZE obs_build")
        rows = QA.write_obs_coverage(w)
        write_csv(out / "obs_coverage.csv", rows)
        write_csv(out / "exclusions.csv", [{"mode": m, "reason": r, "records": n}
                                           for m, r, n in QA.exclusion_summary(w)])
        issues = QA.check_quality(w)
        from .analytics import observed as OB
        chars = AD.load_characters(meta / "characters.json")
        app_rows, res_rows, inv_rows = [], [], []
        for sc in OB.scopes(w):
            base = {"snapshot_id": sc.snapshot_id, "combat_season": sc.combat_season, "mode": sc.mode}
            for r in OB.appearance(w, sc):
                app_rows.append({**base, **r})
            for r in OB.results_by_stage(w, sc):
                res_rows.append({**base, **r})
            for r in OB.appearance(w, sc)[:10]:
                a = OB.investment_association(w, sc, chars, r["unit_id"])
                if a.get("status") == "ok":
                    inv_rows.append({**base, "unit_id": a["unit_id"], "scoring_regime": a["scoring_regime"],
                                     "n_c0": a["cinema_0"]["n"], "median_c0": a["cinema_0"]["median"],
                                     "n_c1plus": a["cinema_1plus"]["n"], "median_c1plus": a["cinema_1plus"]["median"],
                                     "unknown_records": a["cinema_unknown_records"], "label": a["label"]})
        write_csv(out / "appearance.csv", app_rows)
        write_csv(out / "results_by_stage.csv", res_rows)
        write_csv(out / "investment_association.csv", inv_rows)
        write_json(out / "quality_issues.json", issues)
        result.update(status="imported" if not issues else "imported_with_issues",
                      n_battles=n_battles, n_eligible=n_eligible, n_import_batches=n_batches,
                      n_scopes=len(OB.scopes(w)), quality_issues=issues)
        write_json(out / "import_progress.json", {"phase": "completed", "n_battles": n_battles,
                   "n_eligible": n_eligible, "updated_at": _now()})
    return result


# =========================================================================== simulate / aggregate (L3)


def simulate(entry: Path | str, out_dir: Path | str, *, seed: int, scenario: Optional[str] = None,
             n_players: Optional[int] = None, log_mode: str = "full", extra_overrides: Optional[dict] = None) -> dict:
    base = resolve_config(entry)
    ov = dict(extra_overrides or {})
    if n_players is not None and n_players != base.module("population")["n_players"]:
        ov["population.n_players"] = int(n_players)
    if scenario:
        ov = SE.scenario_overrides(base, scenario, ov)
    cfg = resolve_config(entry, overrides=ov) if ov else base
    name = scenario or cfg.module("experiments")["base_scenario"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    final = out / f"{name}_seed{seed}.sqlite"
    tmp = final.with_suffix(".tmp.sqlite")
    for p in (tmp,):
        if p.exists():
            p.unlink()
    w = SQLiteWriter(tmp, log_mode=log_mode)
    try:
        res = run_simulation(cfg, master_seed=seed, writer=w)
        fk = w.query("PRAGMA foreign_key_check")
    finally:
        w.close()
    if fk:
        raise PipelineError(f"运行数据库外键检查失败: {fk[:3]}")
    os.replace(tmp, final)
    return {"config_id": cfg.config_id, "run_id": res.run_id, "scenario": name, "seed": seed,
            "n_players": cfg.module("population")["n_players"], "path": str(final), "overrides": ov}


def aggregate_run(db: Path | str, out_dir: Path | str, *, run_id: Optional[str] = None,
                  core_tier: Optional[str] = None, label: Optional[str] = None) -> dict:
    w = SQLiteWriter(db)
    try:
        rid = run_id or w.query("SELECT run_id FROM run_manifest WHERE status='completed'")[0][0]
        cfg_json = json.loads(w.query("SELECT config_json FROM run_manifest WHERE run_id=?", (rid,))[0][0])
        core = core_tier or cfg_json["modules"]["population"]["core_retention_tier"]
        tag = label or Path(db).stem
        out = Path(out_dir)
        rates = CO.cohort_availability(w, rid)
        bad = CO.check_identity(rates)
        if bad:
            raise PipelineError(f"三口径恒等式不成立: {bad[:2]}")
        write_csv(out / f"{tag}_cohort_availability.csv", [r.as_dict() for r in rates])
        write_csv(out / f"{tag}_retention_cash.csv", CO.retention_and_cash(w, rid))
        write_csv(out / f"{tag}_retention_cash_by_tier.csv", CO.retention_and_cash(w, rid, by_tier=True))
        write_csv(out / f"{tag}_revenue_structure.csv", CO.revenue_structure(w, rid))
        write_csv(out / f"{tag}_budget_fatigue_cross.csv", CO.budget_fatigue_cross(w, rid))
        obj = CO.run_objectives(w, rid, core)
    finally:
        w.close()
    return {"run_id": rid, "label": tag, "n_cohort_rows": len(rates), **obj.__dict__}


# =========================================================================== search


def search(entry: Path | str, out_dir: Path | str, *, n_players: Optional[int] = None, workers: int = 1,
           train_seeds: Optional[Sequence[int]] = None, validation_seeds: Optional[Sequence[int]] = None,
           candidate_ids: Optional[Sequence[str]] = None, perturbation_ids: Optional[Sequence[str]] = None,
           progress=None) -> dict:
    setup = SE.plan_search(entry, n_players=n_players, train_seeds=train_seeds, validation_seeds=validation_seeds,
                           candidate_ids=candidate_ids, perturbation_ids=perturbation_ids)
    out = Path(out_dir)
    results = SE.execute(setup, out, workers=workers, progress=progress)
    summary = SE.assemble(setup, results, out / "search.sqlite")
    SE.write_manifest(setup, out, summary)
    params = (setup.experiment_id,)
    db = sqlite3_rows(out / "search.sqlite", "SELECT * FROM agg_seed_robustness WHERE experiment_id=? "
                      "ORDER BY perturbation_id, split, candidate_id", params)
    write_csv(out / "seed_robustness.csv", db)
    write_csv(out / "search_results.csv", sqlite3_rows(out / "search.sqlite", "SELECT * FROM agg_search_results "
              "WHERE experiment_id=? ORDER BY 1,2,3,4,5", params))
    write_csv(out / "rank_stability.csv", sqlite3_rows(out / "search.sqlite", "SELECT * FROM agg_rank_stability "
              "WHERE experiment_id=?", params))
    write_csv(out / "parameter_sensitivity.csv", sqlite3_rows(out / "search.sqlite", "SELECT * FROM agg_parameter_sensitivity "
              "WHERE experiment_id=? ORDER BY 1,2,5", params))
    return {**{k: v for k, v in summary.items() if k != "rank_stability"},
            "n_logical_runs": summary["logical_runs"], "n_unique_runs": summary["unique_runs"]}


def sqlite3_rows(db: Path | str, sql: str, params: Sequence[Any] = ()) -> list[dict]:
    import sqlite3
    con = sqlite3.connect(db)
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


# =========================================================================== run-all


def run_all(entry: Path | str, out_dir: Path | str, *, n_players: Optional[int] = None, workers: int = 1,
            reference_seed: Optional[int] = None, train_seeds: Optional[Sequence[int]] = None,
            validation_seeds: Optional[Sequence[int]] = None, candidate_ids: Optional[Sequence[str]] = None,
            perturbation_ids: Optional[Sequence[str]] = None, snapshot_dir: Optional[str] = None,
            snapshot_id: Optional[str] = None, revision: Optional[str] = None,
            meta_dir: Path | str = DEFAULT_META_DIR, progress=None) -> dict:
    from . import app as APP
    from . import report as RP
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log = StageLog(out)
    base = resolve_config(entry)
    seed = reference_seed if reference_seed is not None else \
        base.metadata["module_metadata"]["experiments"]["train_seeds"][0]
    v = log.run("validate_config", lambda: validate_config(entry))
    write_json(out / "validate.json", v)
    log.run("build_benchmarks", lambda: build_benchmarks(entry, out / "benchmarks"))
    log.run("prepare_observations", lambda: prepare_observations(
        out / "observations", meta_dir=meta_dir, snapshot_dir=snapshot_dir, snapshot_id=snapshot_id,
        revision=revision))
    refs = []
    ref_log = base.metadata["module_metadata"]["experiments"].get("reference_log_mode", "full")
    for name in SE.reference_scenario_names(base):
        r = log.run(f"simulate_{name}", lambda name=name: simulate(entry, out / "reference", seed=seed,
                                                                   scenario=name, n_players=n_players,
                                                                   log_mode=ref_log))
        a = log.run(f"aggregate_{name}", lambda r=r: aggregate_run(r["path"], out / "tables", run_id=r["run_id"],
                                                                   label=Path(r["path"]).stem))
        refs.append({**r, **{k: a[k] for k in ("total_revenue_fen", "core_retention_end", "retention_end_all",
                                                 "core_initial")}})
    write_json(out / "reference_runs.json", refs)
    log.run("search", lambda: search(entry, out / "search", n_players=n_players, workers=workers,
                                     train_seeds=train_seeds, validation_seeds=validation_seeds,
                                     candidate_ids=candidate_ids, perturbation_ids=perturbation_ids,
                                     progress=progress))
    log.run("report", lambda: RP.build_report(out))
    log.run("dashboard", lambda: APP.build_dashboard(out))
    return log.data
