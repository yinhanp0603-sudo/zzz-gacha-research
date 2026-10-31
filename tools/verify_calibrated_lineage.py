"""Read-only audit of a calibrated rebuild's frozen configuration and run lineage.

The normal invocation writes verification/lineage_audit.json.  --sample-runs N
checks only N already published run databases and writes lineage_sample_audit.json;
that result is deliberately never labelled "passed".  Neither mode simulates,
repairs databases, or changes any input file.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import sqlite3
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))
from zzz_research.analytics import reference as REF
from zzz_research.analytics import search as SE
from zzz_research.combat import benchmark as BM
from zzz_research.combat.proxy import ProxyCombatModel
from zzz_research.config import resolve_config
from zzz_research.contracts import RunIdentity, deep_thaw, sha256_hex
from zzz_research.pipeline import from_project
from zzz_research.simulation import resolve_world

AUDIT_VERSION = "calibrated_lineage_audit_v1"
DEFAULT_STAGE = ROOT / "outputs/benchmark_n200_refresh_201e32cee97c"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def same(actual: Any, expected: Any, label: str) -> None:
    if expected is None:
        ok = actual is None or actual == ""
    elif isinstance(expected, (int, float)):
        try:
            value = {"True": 1, "False": 0}.get(actual, actual) if isinstance(actual, str) else actual
            value = float(value)
            ok = math.isfinite(value) and math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-10)
        except (TypeError, ValueError):
            ok = False
    else:
        ok = actual == expected
    require(ok, f"{label}: actual={actual!r}, expected={expected!r}")


@contextmanager
def read_only(path: Path):
    require(path.is_file(), f"Missing database: {path}")
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    con.execute("PRAGMA query_only=ON")
    try:
        yield con
    finally:
        con.close()


def integrity(con: sqlite3.Connection, label: str) -> None:
    require(con.execute("PRAGMA quick_check").fetchall() == [("ok",)], f"{label}: quick_check failed")
    require(not con.execute("PRAGMA foreign_key_check").fetchall(), f"{label}: foreign_key_check failed")


def primary_key(con: sqlite3.Connection, table: str, expected: tuple[str, ...]) -> None:
    cols = con.execute(f"PRAGMA table_info({table})").fetchall()
    actual = tuple(r[1] for r in sorted((r for r in cols if r[5]), key=lambda r: r[5]))
    require(actual == expected, f"{table}: primary key {actual!r} != {expected!r}")


def dict_rows(con: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    cursor = con.execute(sql, params)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def compare_rows(actual: list[dict], expected: list[dict], keys: tuple[str, ...], label: str) -> None:
    def index(rows):
        result = {}
        for row in rows:
            key = tuple(str(row[k]) for k in keys)
            require(key not in result, f"{label}: duplicate key {key}")
            result[key] = row
        return result
    a, e = index(actual), index(expected)
    require(a.keys() == e.keys(), f"{label}: row keys differ ({len(a)} vs {len(e)})")
    for key, row in e.items():
        require(a[key].keys() == row.keys(), f"{label}/{key}: columns differ")
        for column, value in row.items():
            same(a[key][column], value, f"{label}/{key}/{column}")


def tracked_parameters(cfg) -> dict:
    paths = ("scenarios.product_power.B_ref_first", "scenarios.product_power.growth_per_debut_step",
             "scenarios.initial_assets.starter_dps.B_ref", "combat.gear_growth.max_bonus",
             "combat.shield.defense_bonus", "combat.shield.damage_reduction",
             "combat.support.default_gain_scale", "behavior.churn.base_hazard",
             "behavior.fatigue.gap_weight", "behavior.fatigue.failure_weight",
             "behavior.fatigue.budget_weight")
    return {path: cfg.get(path) for path in paths}


def expected_identity(cfg, seed: int) -> RunIdentity:
    return RunIdentity(str(cfg.metadata.get("profile_id") or "experiment"), cfg.config_id,
                       f"seed:{seed}", seed)


def objectives(con: sqlite3.Connection, run_id: str, core: str) -> dict:
    row = con.execute("SELECT MAX(t_idx),SUM(sub_revenue_fen+topup_revenue_fen) "
                      "FROM agg_player_period WHERE run_id=?", (run_id,)).fetchone()
    last, revenue = row
    n_all, a_all = con.execute("SELECT COUNT(*),SUM(active_end) FROM agg_player_period "
                              "WHERE run_id=? AND t_idx=?", (run_id, last)).fetchone()
    n_core, a_core = con.execute("SELECT COUNT(*),COALESCE(SUM(active_end),0) FROM agg_player_period "
                                "WHERE run_id=? AND t_idx=? AND player_tier=?",
                                (run_id, last, core)).fetchone()
    return {"total_revenue_fen": int(revenue), "core_initial": n_core,
            "core_active_end_last": a_core, "core_retention_end": a_core / n_core if n_core else None,
            "all_initial": n_all, "all_active_end_last": a_all,
            "retention_end_all": a_all / n_all if n_all else None}


def check_run_database(path: Path, cfg, seed: int, log_mode: str) -> dict:
    """Validate one completed run without creating a writer or invoking simulation."""
    identity = expected_identity(cfg, seed)
    before = path.stat()
    with read_only(path) as con:
        require(con.execute("SELECT COUNT(*) FROM run_manifest").fetchone()[0] == 1,
                f"{path.name}: expected one run_manifest row")
        SE._validate_completed(con, identity.run_id, expected_key=(cfg.config_id, seed),
                               required_log_mode=log_mode)
        for table, pk in (("run_manifest", ("run_id",)), ("run_attempt", ("run_id", "attempt_id")),
                          ("agg_player_period", ("run_id", "player_id", "t_idx")),
                          ("fact_player_lifecycle", ("run_id", "player_id", "t_idx"))):
            primary_key(con, table, pk)
        if log_mode == "full":
            primary_key(con, "fact_pull_event", ("run_id", "player_id", "t_idx", "banner_id", "pool_kind", "draw_slot"))
            primary_key(con, "fact_player_roster_panel", ("run_id", "player_id", "t_idx", "unit_id", "eval_phase"))
        manifest = dict_rows(con, "SELECT * FROM run_manifest")[0]
        require(json.loads(manifest["config_json"]) == deep_thaw(cfg.science),
                f"{path.name}: full expected scientific configuration differs")
        require(manifest["input_manifest_hash"] == sha256_hex(deep_thaw(cfg.input_digests)),
                f"{path.name}: input_manifest_hash differs")
        versions = json.loads(manifest["algorithm_versions_json"])
        for key, value in cfg.algorithm_versions.items():
            same(versions.get(key), value, f"{path.name}/algorithm/{key}")
        mode = con.execute("SELECT log_mode FROM run_attempt WHERE run_id=? "
                           "ORDER BY attempt_id DESC LIMIT 1", (identity.run_id,)).fetchone()[0]
        objective = objectives(con, identity.run_id, cfg.module("population")["core_retention_tier"])
        record = {"path": str(path), "run_id": identity.run_id, "config_id": cfg.config_id,
                  "seed": seed, "status": "completed", "log_mode": mode, **objective,
                  "tracked_parameters": tracked_parameters(cfg)}
    after = path.stat()
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
            f"{path.name}: database changed during audit")
    return record


class Inputs:
    def __init__(self):
        self.hashes = {}

    def data(self, path: Path) -> bytes:
        data = path.read_bytes()
        self.hashes[str(path)] = {"sha256": hashlib.sha256(data).hexdigest(), "byte_size": len(data)}
        return data

    def json(self, path: Path):
        return json.loads(self.data(path).decode("utf-8-sig"))

    def csv(self, path: Path) -> list[dict]:
        return list(csv.DictReader(io.StringIO(self.data(path).decode("utf-8-sig"), newline="")))

    def unchanged(self) -> None:
        for name, meta in self.hashes.items():
            require(hashlib.sha256(Path(name).read_bytes()).hexdigest() == meta["sha256"],
                    f"Input changed during audit: {name}")


def check_l2(stage: Path, cfg, inputs: Inputs, pipeline: dict) -> dict:
    model = ProxyCombatModel(cfg.module("combat"))
    world = resolve_world(cfg, model)
    anchors = [BM.BenchmarkAnchor(u.unit_id, u.b_ref, u.pierce_share)
               for u in sorted(world.units.values(), key=lambda u: u.unit_id) if u.b_ref is not None]
    environments = [BM.BenchmarkEnvironment(p.period_id, p.scenario.hp, p.scenario.theta, p.scenario.uptime)
                    for p in world.periods]
    bid = BM.benchmark_id(model, anchors, environments, (1.0,), world.neutral_fixed, world.neutral_flex)
    subjects = REF.build_subjects(cfg, world)
    ref_bid = sha256_hex([REF.REFERENCE_PROTOCOL, cfg.config_id, model.params.config_digest,
                         [s.reference_subject_id for s in subjects]])
    subject_map = {s.reference_subject_id: s for s in subjects}
    periods = {p.period_id: p for p in world.periods}
    expected_keys = {(s.reference_subject_id, p.period_id, p.scenario.scenario_id)
                     for s in subjects for p in world.periods if p.t_idx >= s.debut_t}
    fixed_keys = {(a.unit_id, inv.label, sup or "NONE", str(float(k)), "1.0", env.env_id)
                  for a in anchors for inv in BM.investment_grid(model) for sup in BM.support_options(model)
                  for k in model.params.alignment_values for env in environments}
    panel = inputs.csv(stage / "benchmarks/kappa_support_panel.csv")
    got_keys = [(r["anchor"], r["investment"], r["support"], str(float(r["kappa"])),
                 str(float(r["gear"])), r["env_id"]) for r in panel]
    require(len(got_keys) == len(set(got_keys)) and set(got_keys) == fixed_keys, "L2: fixed panel keys differ")
    require({r["benchmark_id"] for r in panel} == {bid}, "L2: fixed panel scientific identity differs")
    pairs = inputs.csv(stage / "benchmarks/benchmark_pairs.csv")
    expected_pairs = (len(model.params.support_gains) + len(model.params.alignment_values) - 1) * len(anchors) * len(BM.investment_grid(model)) * len(environments)
    same(len(pairs), expected_pairs, "L2/pair_count")
    require({r["benchmark_id"] for r in pairs} == {bid}, "L2: paired benchmark identity differs")
    trajectories = inputs.csv(stage / "benchmarks/reference_trajectories.csv")
    with read_only(stage / "benchmarks/benchmarks.sqlite") as con:
        integrity(con, "L2")
        subjects_db = dict_rows(con, "SELECT * FROM dim_reference_subject")
        require({r["reference_subject_id"] for r in subjects_db} == subject_map.keys(), "L2: subject IDs differ")
        require({r["benchmark_id"] for r in subjects_db} == {ref_bid}, "L2: reference scientific identity differs")
        for row in subjects_db:
            subject = subject_map[row["reference_subject_id"]]
            for col, attr in (("anchor_unit_id", "anchor_unit_id"), ("investment_plan", "investment_plan"),
                              ("gear_policy", "gear_policy"), ("support_policy", "support_policy"), ("origin_day", "origin_day")):
                same(row[col], getattr(subject, attr), f"L2/subject/{subject.reference_subject_id}/{col}")
        facts = dict_rows(con, "SELECT * FROM fact_fixed_benchmark_panel")
        require(len(facts) == len(expected_keys), "L2: reference fact count differs")
        require({(r["reference_subject_id"], r["period_id"], r["scenario_id"]) for r in facts} == expected_keys,
                "L2: reference fact time/scenario keys differ")
        require({r["benchmark_id"] for r in facts} == {ref_bid}, "L2: fact reference identity differs")
        expected_csv = []
        for row in facts:
            subject, period = subject_map[row["reference_subject_id"]], periods[row["period_id"]]
            same(row["anchor_unit_id"], subject.anchor_unit_id, "L2/fact/anchor")
            same(row["cinema"], subject.cinema, "L2/fact/cinema")
            same(row["has_weapon"], int(subject.signature_weapon), "L2/fact/weapon")
            expected_csv.append({"reference_subject_id": subject.reference_subject_id,
                "anchor": subject.anchor_unit_id, "plan": subject.investment_plan,
                "gear_policy": subject.gear_policy, "support_policy": subject.support_policy,
                "t_idx": period.t_idx, "age_days": period.evaluation_day - subject.origin_day,
                "support_active": row["support_id"], "gear": row["disc_mult"], "ci": row["clear_index"],
                "is_full_star": row["is_full_star"], "eval_status": row["eval_status"]})
        compare_rows(trajectories, expected_csv, ("reference_subject_id", "t_idx"), "L2/trajectories")
        compare_rows(pairs, dict_rows(con, "SELECT * FROM agg_benchmark_pairs"), ("benchmark_id", "pair_id"), "L2/pairs")
    section = pipeline["stages"]["build_benchmarks"]
    same(section["status"], "completed", "L2/stage/status")
    same(section["config_id"], cfg.config_id, "L2/stage/config_id")
    for column, expected in (("n_panel_rows", len(panel)), ("n_pairs", len(pairs)),
                             ("n_reference_obs", len(facts))):
        same(section[column], expected, f"L2/stage/{column}")
    return {"base_config_id": cfg.config_id, "benchmark_id": bid, "reference_benchmark_id": ref_bid,
            "panel_rows": len(panel), "pairs": len(pairs), "reference_subjects": len(subjects),
            "reference_observations": len(facts), "combat_recomputed": False}


def check_reference_exports(stage: Path, ref: dict, cfg, inputs: Inputs) -> dict:
    seed, name = ref["seed"], ref["scenario"]
    path = stage / "reference" / f"{name}_seed{seed}.sqlite"
    require(from_project(ref["path"]).resolve() == path.resolve(), f"reference/{name}: manifest database path differs")
    record = check_run_database(path, cfg, seed, "full")
    for column in ("run_id", "config_id", "total_revenue_fen", "core_initial", "core_retention_end", "retention_end_all"):
        same(ref[column], record[column], f"reference/{name}/{column}")
    same(ref["n_players"], cfg.module("population")["n_players"], f"reference/{name}/n_players")
    with read_only(path) as con:
        for by_tier, suffix in ((False, "retention_cash"), (True, "retention_cash_by_tier")):
            group = "player_tier" if by_tier else "'ALL'"
            rows = con.execute(f"SELECT {group},t_idx,COUNT(*),SUM(active_start),SUM(active_end),"
                               "SUM(sub_revenue_fen+topup_revenue_fen) FROM agg_player_period "
                               "WHERE run_id=? GROUP BY 1,2 ORDER BY 1,2", (record["run_id"],)).fetchall()
            expected, cumulative = [], {}
            for tier, t, n, active_start, active_end, cash in rows:
                cumulative[tier] = cumulative.get(tier, 0) + cash
                expected.append({"run_id": record["run_id"], "group": tier, "t_idx": t, "initial_players": n,
                    "retention_start": active_start / n, "retention_end": active_end / n,
                    "period_cash_fen": cash, "cum_cash_fen": cumulative[tier],
                    "cum_cash_per_initial_fen": cumulative[tier] / n})
            compare_rows(inputs.csv(stage / "tables" / f"{path.stem}_{suffix}.csv"), expected,
                         ("group", "t_idx"), f"reference/{name}/{suffix}")
        revenue = inputs.csv(stage / "tables" / f"{path.stem}_revenue_structure.csv")
        expected_revenue = dict_rows(con, "SELECT t_idx,SUM(sub_revenue_fen) sub_revenue_fen,"
            "SUM(topup_revenue_fen) topup_revenue_fen FROM agg_player_period WHERE run_id=? GROUP BY t_idx",
            (record["run_id"],))
        compare_rows([{k: r[k] for k in ("t_idx", "sub_revenue_fen", "topup_revenue_fen")} for r in revenue],
                     expected_revenue, ("t_idx",), f"reference/{name}/revenue_structure")
    return record


def check_merged(stage: Path, setup, expected_runs: dict, run_records: dict, inputs: Inputs) -> dict:
    path = stage / "search/search.sqlite"
    with read_only(path) as con:
        integrity(con, "merged search")
        manifest = dict_rows(con, "SELECT * FROM experiment_manifest")
        require(len(manifest) == 1, "merged: expected one experiment")
        row = manifest[0]
        for col, value in (("experiment_id", setup.experiment_id), ("base_config_id", setup.base.config_id),
                           ("status", "completed"), ("log_mode", setup.log_mode)):
            same(row[col], value, f"merged/experiment/{col}")
        for col, value in (("common_overrides_json", setup.common_overrides), ("train_seeds_json", setup.train_seeds),
                           ("validation_seeds_json", setup.validation_seeds)):
            require(json.loads(row[col]) == value, f"merged/experiment/{col} differs")
        actual_runs = dict_rows(con, "SELECT run_id,config_id,master_seed,status,config_json FROM run_manifest")
        require({r["run_id"] for r in actual_runs} == expected_runs.keys(), "merged: run identity set differs")
        for r in actual_runs:
            cfg, seed = expected_runs[r["run_id"]]
            require((r["config_id"], r["master_seed"], r["status"]) == (cfg.config_id, seed, "completed"), "merged: incomplete identity")
            require(json.loads(r["config_json"]) == deep_thaw(cfg.science), "merged: scientific configuration differs")
        expected_groups = len({(s.candidate.candidate_id, s.perturbation.perturbation_id, s.split) for s in setup.specs})
        n = next(iter(expected_runs.values()))[0].module("population")["n_players"]
        periods = setup.base.module("scenarios")["timeline"]["n_periods"]
        expected_counts = {"run_manifest": len(expected_runs), "agg_player_period": len(expected_runs) * n * periods,
            "fact_player_lifecycle": len(expected_runs) * n * periods, "agg_search_results": len(setup.specs),
            "agg_seed_robustness": expected_groups, "agg_rank_stability": len(setup.perturbations),
            "agg_parameter_sensitivity": len(setup.candidates) * (len(setup.perturbations) - 1)}
        counts = {}
        for table, count in expected_counts.items():
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            same(counts[table], count, f"merged/{table}/count")
        # Per-run/time coverage cannot be replaced by an overall row count.
        expected_coverage = {(rid, t, n, n, 0, n - 1) for rid in expected_runs for t in range(1, periods + 1)}
        for table in ("agg_player_period", "fact_player_lifecycle"):
            rows = con.execute(f"SELECT run_id,t_idx,COUNT(*),COUNT(DISTINCT player_id),MIN(player_id),MAX(player_id) "
                               f"FROM {table} GROUP BY run_id,t_idx").fetchall()
            require(len(rows) == len(expected_coverage) and set(rows) == expected_coverage, f"merged/{table}: incomplete periods")
        mismatch = con.execute("SELECT 1 FROM agg_player_period a JOIN fact_player_lifecycle l "
            "USING(run_id,player_id,t_idx) WHERE a.player_tier IS NOT l.player_tier "
            "OR a.active_start IS NOT l.active_start OR a.active_end IS NOT l.active_end "
            "OR a.CI_pre IS NOT l.CI_pre OR a.CI_post IS NOT l.CI_post LIMIT 1").fetchone()
        require(mismatch is None, "merged: lifecycle and player-period values differ")
        core = setup.base.module("population")["core_retention_tier"]
        raw_objectives = con.execute("SELECT run_id,SUM(sub_revenue_fen+topup_revenue_fen),"
            "SUM(CASE WHEN t_idx=? THEN active_end ELSE 0 END),"
            "SUM(CASE WHEN t_idx=? AND player_tier=? THEN active_end ELSE 0 END),"
            "SUM(CASE WHEN t_idx=? AND player_tier=? THEN 1 ELSE 0 END) "
            "FROM agg_player_period GROUP BY run_id", (periods, periods, core, periods, core)).fetchall()
        for rid, revenue, all_active, core_active, core_initial in raw_objectives:
            for key, value in (("total_revenue_fen", revenue), ("all_active_end_last", all_active),
                               ("core_active_end_last", core_active), ("core_initial", core_initial)):
                same(value, run_records[rid][key], f"merged/raw_objectives/{rid}/{key}")
        exported = inputs.csv(stage / "search/search_results.csv")
        db_rows = dict_rows(con, "SELECT * FROM agg_search_results")
        compare_rows(exported, db_rows, ("candidate_id", "perturbation_id", "split", "master_seed"), "merged/search CSV")
        for r in db_rows:
            require(r["experiment_id"] == setup.experiment_id, "merged: aggregate experiment differs")
            for key in ("total_revenue_fen", "core_initial", "core_retention_end"):
                same(r[key], run_records[r["run_id"]][key], f"merged/objectives/{r['run_id']}/{key}")
    return {"path": str(path), "table_counts": counts, "experiment_id": setup.experiment_id,
            "quick_check": "ok", "foreign_key_check": "ok"}


def audit_stage(stage: Path, *, sample_runs: int | None = None) -> dict:
    stage = stage.resolve()
    inputs, started = Inputs(), time.perf_counter()
    result = {"audit_version": AUDIT_VERSION, "status": "failed", "stage": str(stage),
              "generated_at_utc": datetime.now(timezone.utc).isoformat(), "simulations_executed": 0,
              "sample_only": sample_runs is not None, "failures": []}
    try:
        require(sample_runs is None or sample_runs > 0, "sample_runs must be positive")
        plan = inputs.json(stage / "refresh_plan.json")
        entry = stage / "config_snapshot/experiment.json"
        for path in sorted((stage / "config_snapshot").rglob("*.json")):
            inputs.data(path)
        base = resolve_config(entry)
        require(base.config_id == plan["base_config_id"], "Frozen config differs from refresh_plan")
        players = plan["players"]
        require(isinstance(players, int) and not isinstance(players, bool) and players > 0, "Invalid plan.players")
        if sample_runs is None:
            same(plan.get("build_status"), "completed", "refresh_plan/build_status")
        for name, digest in plan["source_hashes"].items():
            same(hashlib.sha256(inputs.data(ROOT / name)).hexdigest(), digest, f"frozen source/{name}")
        validation = inputs.json(stage / "validate.json")
        same(validation["config_id"], base.config_id, "validate/config_id")
        same(validation.get("status"), "ok", "validate/status")
        require(validation["algorithm_versions"] == dict(base.algorithm_versions), "validate/algorithm_versions differs")
        execution = resolve_config(entry, overrides={"population.n_players": players})
        same(validation["execution_base_config_id"], execution.config_id, "validate/execution_base_config_id")
        require(validation["execution_overrides"] == {"population.n_players": players}, "validate/execution_overrides differs")
        pipeline = inputs.json(stage / "pipeline_manifest.json")
        same(pipeline["stages"]["validate_config"]["config_id"], base.config_id, "pipeline/validate/config_id")
        result.update(base_config_id=base.config_id, execution_base_config_id=execution.config_id,
                      players=players, frozen_parameters=tracked_parameters(base))
        result["layer2"] = check_l2(stage, base, inputs, pipeline)
        setup = SE.plan_search(entry, n_players=players)
        expected_runs, expected_logical, cached = {}, {}, {}
        for spec in setup.specs:
            key = (spec.candidate.candidate_id, spec.perturbation.perturbation_id)
            if key not in cached:
                cached[key] = resolve_config(entry, overrides=SE.run_overrides(base, spec.candidate, spec.perturbation,
                                                                              setup.common_overrides))
            cfg = cached[key]
            rid = expected_identity(cfg, spec.seed).run_id
            expected_runs[rid] = (cfg, spec.seed)
            expected_logical[(key[0], key[1], spec.split, spec.seed)] = rid
        result.update(experiment_id=setup.experiment_id, expected_logical_runs=len(setup.specs),
                      expected_unique_runs=len(expected_runs), expected_unique_configs=len({c.config_id for c, _ in expected_runs.values()}),
                      train_seeds=setup.train_seeds, validation_seeds=setup.validation_seeds)
        refs = inputs.json(stage / "reference_runs.json")
        names = SE.reference_scenario_names(base)
        require(len(refs) == len(names) and {r["scenario"] for r in refs} == set(names), "Reference scenario coverage differs")
        reference_records = []
        seed = setup.train_seeds[0]
        for ref in refs:
            same(ref["seed"], seed, f"reference/{ref['scenario']}/seed")
            cfg = resolve_config(entry, overrides=SE.scenario_overrides(base, ref["scenario"], {"population.n_players": players}))
            reference_records.append(check_reference_exports(stage, ref, cfg, inputs))
        result["reference_runs"] = reference_records
        runs_dir = stage / "search/runs"
        files = {p.stem: p for p in runs_dir.glob("*.sqlite") if not p.name.startswith(".tmp")}
        selected = sorted(expected_runs)
        if sample_runs is None:
            require(files.keys() == expected_runs.keys(), f"Run file coverage differs: {len(files)} available, {len(expected_runs)} expected")
        else:
            selected = sorted(files.keys() & expected_runs.keys())[:sample_runs]
            require(len(selected) == sample_runs, f"Only {len(selected)} completed expected runs available for sample")
        records = {}
        for index, rid in enumerate(selected, 1):
            cfg, seed = expected_runs[rid]
            records[rid] = check_run_database(files[rid], cfg, seed, setup.log_mode)
            if index % 50 == 0:
                print(f"Lineage checked {index}/{len(selected)} search databases", flush=True)
        result.update(search_databases_verified=len(records), search_log_modes=dict(Counter(r["log_mode"] for r in records.values())),
                      search_runs=records)
        if sample_runs is None:
            manifest = inputs.json(stage / "search/experiment_manifest.json")
            same(manifest["base_config_id"], base.config_id, "search manifest/base_config_id")
            same(manifest["experiment_id"], setup.experiment_id, "search manifest/experiment_id")
            same(manifest["summary"]["logical_runs"], len(setup.specs), "search manifest/logical_runs")
            same(manifest["summary"]["unique_runs"], len(expected_runs), "search manifest/unique_runs")
            for key, value in (("common_overrides", setup.common_overrides), ("train_seeds", setup.train_seeds),
                               ("validation_seeds", setup.validation_seeds), ("log_mode", setup.log_mode),
                               ("candidates", [c.candidate_id for c in setup.candidates]),
                               ("perturbations", [p.perturbation_id for p in setup.perturbations])):
                require(manifest[key] == value, f"search manifest/{key} differs")
            csv_rows = inputs.csv(stage / "search/search_results.csv")
            got = {(r["candidate_id"], r["perturbation_id"], r["split"], int(r["master_seed"])): r for r in csv_rows}
            require(len(got) == len(csv_rows) and got.keys() == expected_logical.keys(), "Search CSV logical coverage differs")
            for key, rid in expected_logical.items():
                same(got[key]["run_id"], rid, f"search CSV/{key}/run_id")
                same(got[key]["config_id"], expected_runs[rid][0].config_id, f"search CSV/{key}/config_id")
            same(pipeline["stages"]["search"]["status"], "completed", "pipeline/search/status")
            same(pipeline["stages"]["search"]["experiment_id"], setup.experiment_id, "pipeline/search/experiment_id")
            result["merged_search"] = check_merged(stage, setup, expected_runs, records, inputs)
        inputs.unchanged()
        result["status"] = "sample_passed" if sample_runs is not None else "passed"
        if sample_runs is not None:
            result["not_verified"] = ["Remaining search databases", "Final search exports and merged search.sqlite", "Build completion"]
    except Exception as exc:
        result["failures"].append(f"{type(exc).__name__}: {exc}")
    result.update(input_sha256=inputs.hashes, elapsed_seconds=round(time.perf_counter() - started, 3))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", nargs="?", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--sample-runs", type=int, help="Bounded check of N completed runs; cannot produce a passed full audit")
    args = parser.parse_args()
    result = audit_stage(args.stage, sample_runs=args.sample_runs)
    name = "lineage_sample_audit.json" if args.sample_runs is not None else "lineage_audit.json"
    destination = args.stage.resolve() / "verification" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("status", "base_config_id", "experiment_id", "players",
          "expected_logical_runs", "search_databases_verified", "failures", "elapsed_seconds")}, ensure_ascii=False))
    print(f"Audit: {destination}")
    return 0 if result["status"] in ("passed", "sample_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
